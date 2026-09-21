"""Outreach Agent: transforma pesquisa em primeira abordagem.

Três limites que este executor impõe de propósito, e que valem mais do que a
qualidade do texto que ele escreve:

* **Não envia, escreve.** A mensagem nasce como rascunho. Disparar email
  escrito por IA sem ninguém ter lido o primeiro é como se queima um domínio —
  e, com ele, o canal inteiro do cliente. O envio é passo separado, com
  integração de email, e não entra por aqui.
* **Sem pesquisa, não escreve.** Abordagem sem pesquisa é spam com nome
  próprio. Se não houver pesquisa da conta, a execução falha dizendo isso, em
  vez de produzir um texto genérico que parece trabalho feito.
* **Respeita descadastro e limite diário.** Quem pediu para sair, sai. E a
  campanha tem teto diário: volume é o que separa prospecção de disparo em
  massa, e o teto vive na configuração da campanha, não na cabeça de quem
  aperta o botão.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.client import client_for
from app.ai.pricing import cost_micro_usd
from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.engagement import Conversation, Message, MessageDirection, MessageStatus
from app.db.models.sales import Campaign, Company, Contact, Prospect, Research, Score
from app.orchestrator.context_builder import AgentContext, assert_same_tenant
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors.base import ExecutionResult
from app.orchestrator.executors.research import ResearchFailed, _accumulate

logger = logging.getLogger("ia_sdr.agents.outreach")

DEFAULT_DAILY_EMAILS = 50


class OutreachBlocked(AppError):
    """A abordagem não deve acontecer — e o motivo importa mais que o erro."""

    code = "outreach_blocked"
    status_code = 409


class Anchor(BaseModel):
    """Um gancho de personalização, preso a um fato da pesquisa."""

    fact: str = Field(description="O fato da pesquisa que está sendo usado")
    how_used: str = Field(description="Como ele aparece na mensagem")


class OutreachDraft(BaseModel):
    subject: str = Field(max_length=120, description="Assunto curto, sem clickbait")
    body: str = Field(description="Corpo do email, pt-BR, sem assinatura")
    anchors: list[Anchor] = Field(
        min_length=1, description="Pelo menos um gancho vindo da pesquisa"
    )
    call_to_action: str = Field(description="O pedido, específico e fácil de aceitar")
    rationale: str = Field(description="Por que esta abordagem, para quem for revisar")


SYSTEM_PROMPT = """Você escreve a primeira abordagem comercial de uma plataforma de prospecção B2B.

Escreve como um vendedor bom escreveria: curto, específico, honesto. O leitor
é um executivo ocupado que não pediu esse email.

Regras que não se negociam:
- Toda personalização vem da pesquisa fornecida. Não invente fato sobre a conta.
- Não prometa o que não está na oferta nem no playbook do cliente.
- Nada de bajulação ("admiro muito o trabalho de vocês"), nada de jargão vazio,
  nada de "espero que esteja tudo bem".
- Quatro a oito frases no corpo. Um pedido só, fácil de responder.
- Português do Brasil, a não ser que a pesquisa indique outro idioma."""


def _fmt(value: Any, limite: int = 3000) -> str:
    texto = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return texto if len(texto) <= limite else texto[:limite] + "\n… (truncado)"


class OutreachExecutor:
    def __init__(self, client_factory=client_for):
        self._client_factory = client_factory

    def __call__(
        self, session: Session, context: AgentContext, envelope: JobEnvelope
    ) -> ExecutionResult:
        prospect, contact, company, campaign = self._load(session, envelope)
        self._check_guardrails(session, envelope, prospect, contact, campaign)
        pesquisa, nota = self._load_intel(session, envelope, prospect)
        passo, anteriores = self._passo_da_cadencia(session, envelope, prospect)

        model = context.agent.get("model") or settings.ai_model_default
        prompt = self._build_prompt(context, contact, company, pesquisa, nota, passo, anteriores)
        rascunho, usage = self._write(session, envelope, model, context, prompt)

        custo = cost_micro_usd(
            model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
        )
        mensagem = self._persist(session, envelope, prospect, contact, rascunho)

        return ExecutionResult(
            output={
                **rascunho.model_dump(),
                "message_id": str(mensagem.id),
                "status": mensagem.status,
            },
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_micro_usd=custo,
            metadata={"conversation_id": str(mensagem.conversation_id), "sent": False},
        )

    # ---------------------------------------------------------------- carga
    def _load(
        self, session: Session, envelope: JobEnvelope
    ) -> tuple[Prospect, Contact, Company | None, Campaign | None]:
        if envelope.entity_id is None:
            raise ResearchFailed("Outreach precisa de entity_id (prospect)")
        prospect = session.get(Prospect, envelope.entity_id)
        if prospect is None:
            raise NotFound("Prospect não encontrado neste tenant")
        assert_same_tenant(prospect, envelope.tenant_id)

        contact = session.get(Contact, prospect.contact_id)
        if contact is None:
            raise NotFound("Contato do prospect não encontrado")
        assert_same_tenant(contact, envelope.tenant_id)

        company = session.get(Company, prospect.company_id) if prospect.company_id else None
        campaign = session.get(Campaign, prospect.campaign_id)
        return prospect, contact, company, campaign

    def _passo_da_cadencia(
        self, session: Session, envelope: JobEnvelope, prospect: Prospect
    ) -> tuple[dict | None, list[Message]]:
        """Qual toque da cadência é este, e o que já foi dito antes.

        Sem as mensagens anteriores, o segundo toque sai igual ao primeiro —
        e um follow-up que repete a abordagem é pior do que não mandar nada:
        prova que do outro lado não tem ninguém lendo.
        """
        passo = envelope.params.get("sequence_step")
        if not isinstance(passo, dict):
            return None, []

        anteriores = list(
            session.execute(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Conversation.prospect_id == prospect.id)
                .where(Message.direction == MessageDirection.OUTBOUND.value)
                .order_by(Message.created_at)
            ).scalars()
        )
        for mensagem in anteriores:
            assert_same_tenant(mensagem, envelope.tenant_id)
        return passo, anteriores

    def _check_guardrails(
        self,
        session: Session,
        envelope: JobEnvelope,
        prospect: Prospect,
        contact: Contact,
        campaign: Campaign | None,
    ) -> None:
        if contact.opted_out:
            raise OutreachBlocked("Contato pediu para não ser abordado")
        if contact.email is None:
            raise OutreachBlocked("Contato sem email: nada para escrever ainda")
        if campaign is not None and campaign.status == "paused":
            raise OutreachBlocked("Campanha pausada")

        teto = DEFAULT_DAILY_EMAILS
        if campaign is not None:
            teto = int((campaign.daily_limits or {}).get("emails", DEFAULT_DAILY_EMAILS))

        desde = datetime.now(UTC) - timedelta(days=1)
        hoje = session.execute(
            select(func.count(Message.id))
            .where(Message.tenant_id == envelope.tenant_id)
            .where(Message.direction == MessageDirection.OUTBOUND.value)
            .where(Message.created_at >= desde)
        ).scalar_one()
        if hoje >= teto:
            raise OutreachBlocked(
                "Limite diário de abordagens da campanha atingido",
                details={"limit": teto, "today": int(hoje)},
            )

    def _load_intel(
        self, session: Session, envelope: JobEnvelope, prospect: Prospect
    ) -> tuple[Research, Score | None]:
        stmt = (
            select(Research)
            .where(Research.tenant_id == envelope.tenant_id)
            .where(Research.entity_id == (prospect.company_id or prospect.contact_id))
            .order_by(Research.created_at.desc())
            .limit(1)
        )
        pesquisa = session.execute(stmt).scalar_one_or_none()
        if pesquisa is None:
            raise OutreachBlocked(
                "Sem pesquisa desta conta: rode o Research Agent antes de abordar"
            )
        assert_same_tenant(pesquisa, envelope.tenant_id)

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
        context: AgentContext,
        contact: Contact,
        company: Company | None,
        pesquisa: Research,
        nota: Score | None,
        passo: dict | None = None,
        anteriores: list[Message] | None = None,
    ) -> str:
        partes = [
            "## Quem está escrevendo",
            _fmt(
                {
                    "empresa": context.tenant["name"],
                    "posicionamento": context.company_brain.get("positioning"),
                    "oferta": (context.campaign or {}).get("offer")
                    or context.company_brain.get("products"),
                    "tom_de_voz": context.company_brain.get("brand_voice"),
                    "playbook": context.company_brain.get("sales_playbook"),
                    "objeções_conhecidas": context.company_brain.get("objections"),
                }
            ),
            "\n## Para quem",
            _fmt(
                {
                    "nome": contact.full_name,
                    "cargo": contact.title,
                    "persona": contact.persona,
                    "empresa": company.name if company else None,
                    "pais": company.country if company else None,
                }
            ),
            "\n## Pesquisa da conta (a personalização sai daqui, e só daqui)",
            _fmt({"resumo": pesquisa.summary, **(pesquisa.findings or {})}, limite=5000),
        ]
        if nota is not None:
            partes += [
                "\n## Aderência ao ICP",
                _fmt({"nota": float(nota.value), "banda": nota.band, "porquê": nota.rationale}),
            ]
        if context.campaign:
            partes += ["\n## Mensagem da campanha", _fmt(context.campaign.get("messaging"))]

        if passo is None:
            partes.append("\nEscreva a primeira abordagem no formato pedido.")
            return "\n".join(partes)

        partes += [
            f"\n## Este é o toque {passo.get('order')} de {passo.get('total_steps')}",
            _fmt({"instrução_deste_toque": passo.get("instruction")}),
            "\n## O que você já mandou para esta pessoa (não repita)",
            _fmt(
                [{"assunto": m.subject, "texto": m.body} for m in (anteriores or [])],
                limite=6000,
            ),
            (
                "\nEscreva o próximo toque. Ele é curto — mais curto que o anterior —, "
                "não repete o argumento já usado, não cobra resposta e não finge que "
                "vocês já conversaram. Traz um ângulo novo ou uma informação nova, "
                "seguindo a instrução deste toque."
            ),
        ]
        return "\n".join(partes)

    def _write(
        self,
        session: Session,
        envelope: JobEnvelope,
        model: str,
        context: AgentContext,
        prompt: str,
    ) -> tuple[OutreachDraft, dict]:
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
            output_format=OutreachDraft,
        )
        _accumulate(usage, getattr(response, "usage", None))

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise ResearchFailed("O modelo recusou escrever esta abordagem")
        if stop == "max_tokens":
            raise ResearchFailed("Rascunho truncado; aumente ai_max_output_tokens")

        rascunho = getattr(response, "parsed_output", None)
        if not isinstance(rascunho, OutreachDraft):
            raise ResearchFailed("A resposta do modelo não veio no formato esperado")
        return rascunho, usage

    # ---------------------------------------------------------------- persistência
    def _persist(
        self,
        session: Session,
        envelope: JobEnvelope,
        prospect: Prospect,
        contact: Contact,
        rascunho: OutreachDraft,
    ) -> Message:
        conversa = session.execute(
            select(Conversation)
            .where(Conversation.prospect_id == prospect.id)
            .where(Conversation.channel == "email")
            .limit(1)
        ).scalar_one_or_none()
        if conversa is None:
            conversa = Conversation(
                tenant_id=envelope.tenant_id,
                prospect_id=prospect.id,
                campaign_id=prospect.campaign_id,
                channel="email",
                subject=rascunho.subject,
            )
            session.add(conversa)
            session.flush()

        mensagem = Message(
            tenant_id=envelope.tenant_id,
            conversation_id=conversa.id,
            direction=MessageDirection.OUTBOUND.value,
            # Rascunho, não enviado: o envio é passo separado e explícito.
            status=MessageStatus.DRAFT.value,
            channel="email",
            subject=rascunho.subject,
            body=rascunho.body,
            metrics={
                "anchors": [a.model_dump() for a in rascunho.anchors],
                "call_to_action": rascunho.call_to_action,
                "rationale": rascunho.rationale,
                "to": contact.email,
            },
        )
        session.add(mensagem)
        session.flush()
        return mensagem
