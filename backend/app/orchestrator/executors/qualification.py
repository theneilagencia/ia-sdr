"""Qualification Agent: decide se o lead vira reunião.

É a decisão mais cara de errar da plataforma inteira. Um falso positivo ocupa
a agenda de um vendedor e queima a confiança dele no sistema; um falso negativo
descarta um cliente. Por isso a qualificação aqui é deliberadamente
conservadora, e três regras valem mais do que o texto do modelo:

* **Sem critério, não qualifica.** Se a campanha não define contra o que
  avaliar, o agente recusa em vez de inventar um padrão. Critério implícito é
  critério de quem escreveu o prompt, não do cliente.
* **Critério atendido exige evidência.** Um critério marcado como cumprido sem
  citar de onde isso veio é rebaixado para "desconhecido" pelo código, não pelo
  prompt. Afirmação sem lastro não vira sim.
* **Confiança baixa não vira "qualificado".** Abaixo do piso configurado, o
  resultado é "precisa de mais informação" e um humano decide. É melhor pedir
  mais um email do que colocar lixo na agenda.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import client_for
from app.ai.pricing import cost_micro_usd
from app.core.config import settings
from app.core.errors import NotFound
from app.db.models.engagement import Conversation, Message, MessageDirection, Qualification
from app.db.models.sales import Campaign, Prospect, ProspectStatus, Research, Score
from app.orchestrator.context_builder import AgentContext, assert_same_tenant
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors.base import ExecutionResult
from app.orchestrator.executors.outreach import OutreachBlocked
from app.orchestrator.executors.research import ResearchFailed, _accumulate

logger = logging.getLogger("ia_sdr.agents.qualification")

HISTORY_LIMIT = 30

#: Abaixo disso, ninguém é qualificado sem um humano olhar.
MIN_CONFIDENCE = 60

Outcome = Literal["qualified", "disqualified", "needs_more_info"]


class CriterionResult(BaseModel):
    criterion: str = Field(description="O critério da campanha, como ele foi definido")
    status: Literal["met", "not_met", "unknown"]
    evidence: str | None = Field(
        default=None, description="A frase da conversa ou da pesquisa que sustenta isso"
    )


class QualificationOutput(BaseModel):
    outcome: Outcome
    criteria_results: list[CriterionResult] = Field(min_length=1)
    rationale: str = Field(description="O porquê, em duas ou três frases")
    confidence: int = Field(ge=0, le=100, description="Quão seguro está desta conclusão")
    next_step: str = Field(description="O que fazer a seguir, concreto")
    proposed_agenda: str | None = Field(
        default=None, description="Pauta sugerida, se for caso de reunião"
    )


SYSTEM_PROMPT = """Você qualifica leads contra os critérios de uma campanha comercial.

Avalie **cada** critério separadamente e diga, para cada um: cumprido, não
cumprido ou desconhecido. Um critério só é cumprido se houver evidência na
conversa ou na pesquisa — cite a frase. Sem evidência, é desconhecido.

Não confunda simpatia com interesse: responder educadamente não é sinal de
compra. Interesse é pedido de detalhe, menção a prazo, orçamento, processo
de decisão ou disposição de conversar.

`confidence` é sobre a sua conclusão, não sobre o lead. Se a conversa ainda
não deu base para decidir, use `needs_more_info` com confiança baixa — pedir
mais uma informação é mais barato do que ocupar a agenda de um vendedor à toa.

Português do Brasil."""


def _fmt(value: Any, limite: int = 4000) -> str:
    texto = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return texto if len(texto) <= limite else texto[:limite] + "\n… (truncado)"


def _enforce_evidence(resultado: QualificationOutput) -> tuple[QualificationOutput, int]:
    """Rebaixa critério cumprido sem evidência, e reavalia o veredito.

    Fica no código, e não só no prompt, porque é a diferença entre uma regra e
    um pedido.
    """
    rebaixados = 0
    criterios: list[CriterionResult] = []
    for item in resultado.criteria_results:
        if item.status == "met" and not (item.evidence or "").strip():
            rebaixados += 1
            criterios.append(
                CriterionResult(criterion=item.criterion, status="unknown", evidence=None)
            )
        else:
            criterios.append(item)

    outcome = resultado.outcome
    confianca = resultado.confidence
    if rebaixados:
        # Perdeu lastro: não pode sair daqui como qualificado.
        confianca = max(0, confianca - 20 * rebaixados)
        if outcome == "qualified":
            outcome = "needs_more_info"

    if outcome == "qualified" and confianca < MIN_CONFIDENCE:
        outcome = "needs_more_info"

    return (
        resultado.model_copy(
            update={"criteria_results": criterios, "outcome": outcome, "confidence": confianca}
        ),
        rebaixados,
    )


class QualificationExecutor:
    def __init__(self, client_factory=client_for):
        self._client_factory = client_factory

    def __call__(
        self, session: Session, context: AgentContext, envelope: JobEnvelope
    ) -> ExecutionResult:
        prospect, campanha, conversa, historico = self._load(session, envelope)
        criterios = (campanha.qualification_criteria or {}) if campanha else {}
        if not criterios:
            raise OutreachBlocked(
                "Campanha sem critérios de qualificação: defina contra o que avaliar "
                "antes de qualificar"
            )

        pesquisa, nota = self._intel(session, envelope, prospect)
        model = context.agent.get("model") or settings.ai_model_default
        prompt = self._build_prompt(criterios, historico, pesquisa, nota)

        bruto, usage = self._judge(session, envelope, model, context, prompt)
        resultado, rebaixados = _enforce_evidence(bruto)

        custo = cost_micro_usd(
            model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
        )
        registro = self._persist(session, envelope, prospect, conversa, resultado)

        return ExecutionResult(
            output={**resultado.model_dump(), "qualification_id": str(registro.id)},
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_micro_usd=custo,
            metadata={
                "outcome": resultado.outcome,
                "confidence": resultado.confidence,
                "downgraded_criteria": rebaixados,
            },
        )

    # ---------------------------------------------------------------- carga
    def _load(
        self, session: Session, envelope: JobEnvelope
    ) -> tuple[Prospect, Campaign | None, Conversation | None, list[Message]]:
        if envelope.entity_id is None:
            raise ResearchFailed("Qualification precisa de entity_id (prospect)")
        prospect = session.get(Prospect, envelope.entity_id)
        if prospect is None:
            raise NotFound("Prospect não encontrado neste tenant")
        assert_same_tenant(prospect, envelope.tenant_id)

        campanha = session.get(Campaign, prospect.campaign_id)
        conversa = session.execute(
            select(Conversation)
            .where(Conversation.prospect_id == prospect.id)
            .order_by(Conversation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        historico: list[Message] = []
        if conversa is not None:
            historico = list(
                session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversa.id)
                    .order_by(Message.created_at.desc())
                    .limit(HISTORY_LIMIT)
                ).scalars()
            )[::-1]
        return prospect, campanha, conversa, historico

    def _intel(
        self, session: Session, envelope: JobEnvelope, prospect: Prospect
    ) -> tuple[Research | None, Score | None]:
        pesquisa = session.execute(
            select(Research)
            .where(Research.entity_id == (prospect.company_id or prospect.contact_id))
            .order_by(Research.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        nota = session.execute(
            select(Score)
            .where(Score.prospect_id == prospect.id)
            .order_by(Score.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return pesquisa, nota

    # ---------------------------------------------------------------- modelo
    def _build_prompt(
        self,
        criterios: dict,
        historico: list[Message],
        pesquisa: Research | None,
        nota: Score | None,
    ) -> str:
        partes = [
            "## Critérios de qualificação desta campanha",
            _fmt(criterios),
            "\n## Conversa com o lead",
            _fmt(
                [
                    {
                        "de": "lead" if m.direction == MessageDirection.INBOUND.value else "nós",
                        "texto": m.body,
                    }
                    for m in historico
                ]
                or "nenhuma conversa ainda",
                limite=6000,
            ),
        ]
        if pesquisa is not None:
            partes += [
                "\n## Pesquisa da conta",
                _fmt({"resumo": pesquisa.summary, **(pesquisa.findings or {})}),
            ]
        if nota is not None:
            partes += [
                "\n## Aderência ao ICP",
                _fmt({"nota": float(nota.value), "banda": nota.band}),
            ]
        partes.append("\nAvalie critério a critério e conclua no formato pedido.")
        return "\n".join(partes)

    def _judge(
        self,
        session: Session,
        envelope: JobEnvelope,
        model: str,
        context: AgentContext,
        prompt: str,
    ) -> tuple[QualificationOutput, dict]:
        client = self._client_factory(session, envelope.tenant_id)
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        response = client.messages.parse(
            model=model,
            max_tokens=context.agent.get("max_output_tokens") or settings.ai_max_output_tokens,
            system=context.agent.get("instructions") or SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": settings.ai_effort},
            output_format=QualificationOutput,
        )
        _accumulate(usage, getattr(response, "usage", None))

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise ResearchFailed("O modelo recusou qualificar este lead")
        if stop == "max_tokens":
            raise ResearchFailed("Veredito truncado; aumente ai_max_output_tokens")

        resultado = getattr(response, "parsed_output", None)
        if not isinstance(resultado, QualificationOutput):
            raise ResearchFailed("A resposta do modelo não veio no formato esperado")
        return resultado, usage

    # ---------------------------------------------------------------- persistência
    def _persist(
        self,
        session: Session,
        envelope: JobEnvelope,
        prospect: Prospect,
        conversa: Conversation | None,
        resultado: QualificationOutput,
    ) -> Qualification:
        registro = Qualification(
            tenant_id=envelope.tenant_id,
            prospect_id=prospect.id,
            conversation_id=conversa.id if conversa else None,
            outcome=resultado.outcome,
            criteria_results={
                "criteria": [c.model_dump() for c in resultado.criteria_results],
                "next_step": resultado.next_step,
                "proposed_agenda": resultado.proposed_agenda,
            },
            rationale=resultado.rationale,
            confidence=resultado.confidence,
        )
        session.add(registro)

        # "needs_more_info" não mexe no funil: o lead continua onde está, e a
        # conversa segue. Só veredito com lastro move alguém de estágio.
        if resultado.outcome == "qualified":
            prospect.status = ProspectStatus.QUALIFIED.value
        elif resultado.outcome == "disqualified":
            prospect.status = ProspectStatus.DISQUALIFIED.value

        session.flush()
        return registro
