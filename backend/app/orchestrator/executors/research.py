"""Research Agent: pesquisa uma conta-alvo e produz fatos com fonte.

É a primeira fatia do Sprint 2 — e a primeira vez que um modelo é chamado de
verdade. O que já existia continua valendo: o contexto vem do orquestrador e
só contém dado do tenant dono do job; consumo, custo e auditoria são gravados
pelo runner.

Três decisões que valem explicar:

* **Saída estruturada.** A pesquisa volta como JSON validado, não como texto
  livre. Isso é o que permite pontuar, filtrar e mostrar em tela sem
  reinterpretar prosa a cada leitura.
* **Busca na web como ferramenta de servidor.** Pesquisa sem acesso à web é
  chute bem escrito. A busca roda na infraestrutura da Anthropic; o que volta
  para o banco são os achados com suas fontes.
* **Teto de custo por execução.** Um agente com busca pode iterar muito. O
  limite em micro-dólares transforma "caro demais" em falha explícita, em vez
  de uma fatura surpreendente no fim do mês.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.client import client_for
from app.ai.pricing import cost_micro_usd
from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.sales import Company, Contact, Research
from app.orchestrator.context_builder import AgentContext, assert_same_tenant
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors.base import ExecutionResult
from app.services import scoring

logger = logging.getLogger("ia_sdr.agents.research")

MAX_RESUMES = 5


class ResearchFailed(AppError):
    code = "research_failed"
    status_code = 502


class CostCeilingExceeded(AppError):
    code = "cost_ceiling_exceeded"
    status_code = 402


class Finding(BaseModel):
    claim: str = Field(description="Um fato verificável sobre a conta-alvo")
    evidence: str = Field(description="O trecho ou dado que sustenta a afirmação")
    source_url: str | None = Field(default=None, description="De onde veio, quando houver")


class ResearchOutput(BaseModel):
    """O formato que o resto da plataforma consome.

    `unknowns` existe por um motivo específico: sem um lugar para dizer "não
    encontrei", o modelo tende a preencher a lacuna. Com ele, a ausência de
    informação vira dado — e dado que o vendedor precisa ver.
    """

    summary: str = Field(description="Três a cinco frases sobre a conta, sem adjetivo vazio")
    icp_fit: Literal["forte", "parcial", "fraco", "desconhecido"]
    icp_rationale: str = Field(description="Por que essa aderência, citando os achados")
    findings: list[Finding] = Field(default_factory=list)
    signals: list[str] = Field(
        default_factory=list,
        description="Gatilhos de timing: contratação, expansão, rodada, troca de sistema",
    )
    unknowns: list[str] = Field(
        default_factory=list, description="O que não foi possível confirmar"
    )
    recommended_angle: str = Field(description="Por onde abordar, dada a oferta do cliente")


SYSTEM_PROMPT = """Você é o Research Agent de uma plataforma de prospecção B2B.

Pesquisa a conta-alvo e produz fatos verificáveis que ajudem a decidir se ela
vale uma abordagem, sempre à luz do ICP da campanha e da oferta do cliente.

Regras que não se negociam:
- Nunca invente. O que não encontrar, liste em `unknowns`.
- Todo item de `findings` precisa de evidência; URL sempre que houver fonte.
- Não repita o que o cliente já sabe sobre si mesmo: o valor está na conta-alvo.
- Escreva em português do Brasil, direto, sem adjetivo de folheto."""


def _fmt(value: Any, limite: int = 4000) -> str:
    texto = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return texto if len(texto) <= limite else texto[:limite] + "\n… (truncado)"


def _build_prompt(context: AgentContext, company: Company, contact: Contact | None) -> str:
    partes = [
        "## Quem está vendendo",
        _fmt(
            {
                "empresa": context.tenant["name"],
                "posicionamento": context.company_brain.get("positioning"),
                "produtos": context.company_brain.get("products"),
                "casos": context.company_brain.get("cases"),
            }
        ),
        "\n## Campanha e ICP",
        _fmt({"campanha": context.campaign, "icp": context.icp}),
        "\n## Conta-alvo a pesquisar",
        _fmt(
            {
                "nome": company.name,
                "dominio": company.domain,
                "setor": company.industry,
                "pais": company.country,
                "porte": company.employee_count,
                "descricao": company.description,
            }
        ),
    ]
    if contact is not None:
        partes += [
            "\n## Pessoa de contato",
            _fmt({"nome": contact.full_name, "cargo": contact.title, "persona": contact.persona}),
        ]
    if context.knowledge:
        partes += [
            "\n## Base de conhecimento do cliente (use para calibrar a oferta)",
            _fmt([c["content"] for c in context.knowledge[:5]], limite=6000),
        ]
    if context.policies:
        partes += ["\n## Políticas de IA do cliente", _fmt(context.policies)]
    partes.append(
        "\nPesquise a conta-alvo e devolva a análise no formato pedido."
    )
    return "\n".join(partes)


class ResearchExecutor:
    """Executor do Research Agent.

    `client_factory` existe para o teste poder injetar um cliente falso: a
    suíte exercita todo o caminho — resume de `pause_turn`, teto de custo,
    persistência — sem rede e sem gastar token.
    """

    def __init__(self, client_factory=client_for):
        self._client_factory = client_factory

    def __call__(
        self, session: Session, context: AgentContext, envelope: JobEnvelope
    ) -> ExecutionResult:
        company, contact = self._load_entities(session, envelope)
        model = context.agent.get("model") or settings.ai_model_default
        prompt = _build_prompt(context, company, contact)

        response, usage = self._converse(session, envelope, model, context, prompt)
        custo = cost_micro_usd(
            model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
        )
        if custo > settings.ai_max_cost_micro_usd:
            raise CostCeilingExceeded(
                "Execução ultrapassou o teto de custo configurado",
                details={"cost_micro_usd": custo, "ceiling": settings.ai_max_cost_micro_usd},
            )

        resultado = _extract(response)
        pesquisa = self._persist(session, context, envelope, company, resultado)

        # A pesquisa vale para todos os prospects daquela conta na campanha:
        # pesquisar a mesma empresa uma vez por pessoa seria pagar várias
        # vezes pelo mesmo trabalho.
        notas = scoring.apply_to_prospects(
            session, tenant_id=envelope.tenant_id, research=pesquisa
        )

        return ExecutionResult(
            output={**resultado.model_dump(), "scored_prospects": len(notas)},
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_micro_usd=custo,
            metadata={
                "web_search": settings.ai_web_search,
                "research_id": str(pesquisa.id),
                "scored_prospects": len(notas),
                "band": notas[0].band if notas else None,
            },
        )

    # ---------------------------------------------------------------- entidades
    def _load_entities(
        self, session: Session, envelope: JobEnvelope
    ) -> tuple[Company, Contact | None]:
        if envelope.entity_id is None:
            raise ResearchFailed("Research precisa de entity_id (company ou contact)")

        if envelope.entity_type == "contact":
            contact = session.get(Contact, envelope.entity_id)
            if contact is None:
                raise NotFound("Contato não encontrado neste tenant")
            assert_same_tenant(contact, envelope.tenant_id)
            if contact.company_id is None:
                raise ResearchFailed("Contato sem empresa associada")
            company = session.get(Company, contact.company_id)
        else:
            contact = None
            company = session.get(Company, envelope.entity_id)

        if company is None:
            raise NotFound("Empresa não encontrada neste tenant")
        assert_same_tenant(company, envelope.tenant_id)
        return company, contact

    # ---------------------------------------------------------------- modelo
    def _request_params(self, model: str, context: AgentContext, messages: list) -> dict:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": context.agent.get("max_output_tokens") or settings.ai_max_output_tokens,
            "system": context.agent.get("instructions") or SYSTEM_PROMPT,
            "messages": messages,
            "output_config": {"effort": settings.ai_effort},
        }
        if settings.ai_web_search:
            # Busca do lado do servidor: nada de scraping nosso, e os
            # resultados chegam como blocos na mesma resposta.
            params["tools"] = [
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": settings.ai_max_web_searches,
                }
            ]
        return params

    def _converse(
        self,
        session: Session,
        envelope: JobEnvelope,
        model: str,
        context: AgentContext,
        prompt: str,
    ) -> tuple[Any, dict]:
        client = self._client_factory(session, envelope.tenant_id)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

        for _ in range(MAX_RESUMES + 1):
            params = self._request_params(model, context, messages)
            response = client.messages.parse(output_format=ResearchOutput, **params)
            _accumulate(usage, getattr(response, "usage", None))

            stop = getattr(response, "stop_reason", None)
            if stop == "refusal":
                detalhe = getattr(response, "stop_details", None)
                raise ResearchFailed(
                    "O modelo recusou a pesquisa",
                    details={"category": getattr(detalhe, "category", None)},
                )
            if stop == "max_tokens":
                raise ResearchFailed(
                    "Resposta truncada por max_tokens; aumente ai_max_output_tokens"
                )
            if stop == "pause_turn":
                # Turno longo de busca: devolve o turno pausado e continua.
                messages.append({"role": "assistant", "content": response.content})
                continue
            return response, usage

        raise ResearchFailed(
            f"A pesquisa continuou pausada após {MAX_RESUMES} retomadas",
            details={"model": model},
        )

    # ---------------------------------------------------------------- persistência
    def _persist(
        self,
        session: Session,
        context: AgentContext,
        envelope: JobEnvelope,
        company: Company,
        resultado: ResearchOutput,
    ) -> Research:
        pesquisa = Research(
            tenant_id=envelope.tenant_id,
            entity_type="company",
            entity_id=company.id,
            campaign_id=envelope.campaign_id,
            depth="standard",
            summary=resultado.summary,
            findings={
                "icp_fit": resultado.icp_fit,
                "icp_rationale": resultado.icp_rationale,
                "findings": [f.model_dump() for f in resultado.findings],
                "signals": resultado.signals,
                "unknowns": resultado.unknowns,
                "recommended_angle": resultado.recommended_angle,
            },
            sources=[f.source_url for f in resultado.findings if f.source_url],
        )
        session.add(pesquisa)
        session.flush()
        return pesquisa


def _accumulate(usage: dict, source) -> None:
    if source is None:
        return
    usage["input_tokens"] += getattr(source, "input_tokens", 0) or 0
    usage["output_tokens"] += getattr(source, "output_tokens", 0) or 0
    usage["cache_read_tokens"] += getattr(source, "cache_read_input_tokens", 0) or 0
    usage["cache_write_tokens"] += getattr(source, "cache_creation_input_tokens", 0) or 0


def _extract(response) -> ResearchOutput:
    """Tira o resultado da resposta, com um plano B.

    `messages.parse` devolve o objeto já validado. Quando a resposta mistura
    blocos de ferramenta de servidor, `parsed_output` pode vir vazio — aí
    sobra o texto, que ainda é o JSON pedido.
    """
    parsed = getattr(response, "parsed_output", None)
    if isinstance(parsed, ResearchOutput):
        return parsed

    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "text":
            continue
        texto = block.text.strip()
        if texto.startswith("```"):
            texto = texto.split("```")[1].removeprefix("json").strip()
        try:
            return ResearchOutput.model_validate_json(texto)
        except ValueError:
            continue
    raise ResearchFailed("A resposta do modelo não veio no formato esperado")


__all__ = [
    "CostCeilingExceeded",
    "Finding",
    "ResearchExecutor",
    "ResearchFailed",
    "ResearchOutput",
]
