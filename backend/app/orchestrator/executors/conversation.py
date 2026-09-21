"""Conversation Agent: responde quando o lead responde.

O ponto difícil aqui não é escrever bem — é saber a hora de calar. Um agente
de conversa que improvisa sobre preço, contrato ou prazo cria compromisso em
nome do cliente, e quem vai honrar (ou desfazer) isso é uma pessoa.

Por isso o agente decide duas coisas além do texto:

* **Escalar para humano.** Quando a resposta não está na base de conhecimento,
  quando o assunto é preço, jurídico ou algo fora do escopo, ou quando o lead
  demonstra irritação. A conversa fica marcada para um humano assumir, e o
  rascunho vira sugestão para essa pessoa — não resposta pronta para sair.
* **Descadastro.** Pedido de não receber mais é obedecido no ato, marcado no
  contato, e não depende de o texto seguinte ser bom.

Como no Outreach, o que sai daqui é rascunho. Quem envia é o passo de envio.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import get_client
from app.ai.pricing import cost_micro_usd
from app.core.config import settings
from app.core.errors import NotFound
from app.db.models.engagement import (
    Conversation,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.db.models.sales import Contact, Prospect, ProspectStatus
from app.orchestrator.context_builder import AgentContext, assert_same_tenant
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors.base import ExecutionResult
from app.orchestrator.executors.research import ResearchFailed, _accumulate

logger = logging.getLogger("ia_sdr.agents.conversation")

HISTORY_LIMIT = 20


class ConversationReply(BaseModel):
    reply: str = Field(description="A resposta sugerida, em pt-BR, curta e específica")
    escalate: bool = Field(
        description="true quando um humano precisa assumir antes de qualquer resposta"
    )
    escalation_reason: str | None = Field(
        default=None, description="Por que escalar: fora da base, preço, jurídico, irritação"
    )
    opt_out_requested: bool = Field(
        default=False, description="O lead pediu para não receber mais contato"
    )
    meeting_intent: bool = Field(
        default=False, description="O lead demonstrou interesse em conversar"
    )
    grounded_in: list[str] = Field(
        default_factory=list, description="Trechos da base usados para responder"
    )


SYSTEM_PROMPT = """Você responde leads em nome de uma empresa, numa conversa comercial já iniciada.

Responde **apenas** com o que estiver na base de conhecimento fornecida e nas
mensagens anteriores. O que não estiver ali, você não sabe — e não inventa.

Escale para um humano (`escalate: true`) quando:
- a resposta não estiver na base de conhecimento;
- o assunto for preço, desconto, contrato, jurídico ou prazo de entrega;
- o lead estiver irritado, ou pedindo algo fora do escopo da oferta;
- houver qualquer dúvida sobre o que pode ser prometido.

Escalar não é falha: é o comportamento certo. Nesses casos, escreva a resposta
como sugestão para a pessoa que vai assumir, e diga ao lead que vai confirmar
internamente — sem prometer prazo.

Se o lead pedir para não receber mais contato, marque `opt_out_requested` e
escreva uma confirmação curta e respeitosa. Nada de tentar reverter.

Português do Brasil, quatro frases ou menos, sem jargão."""


def _fmt(value: Any, limite: int = 3000) -> str:
    texto = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return texto if len(texto) <= limite else texto[:limite] + "\n… (truncado)"


class ConversationExecutor:
    def __init__(self, client_factory=get_client):
        self._client_factory = client_factory

    def __call__(
        self, session: Session, context: AgentContext, envelope: JobEnvelope
    ) -> ExecutionResult:
        conversa, prospect, contato, historico = self._load(session, envelope)

        model = context.agent.get("model") or settings.ai_model_default
        prompt = self._build_prompt(context, contato, historico)
        resposta, usage = self._answer(model, context, prompt)

        custo = cost_micro_usd(
            model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
        )
        mensagem = self._persist(session, envelope, conversa, prospect, contato, resposta)

        return ExecutionResult(
            output={**resposta.model_dump(), "message_id": str(mensagem.id)},
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_micro_usd=custo,
            metadata={
                "conversation_id": str(conversa.id),
                "escalated": resposta.escalate,
                "opt_out": resposta.opt_out_requested,
            },
        )

    # ---------------------------------------------------------------- carga
    def _load(
        self, session: Session, envelope: JobEnvelope
    ) -> tuple[Conversation, Prospect, Contact, list[Message]]:
        if envelope.entity_id is None:
            raise ResearchFailed("Conversation precisa de entity_id (conversation)")
        conversa = session.get(Conversation, envelope.entity_id)
        if conversa is None:
            raise NotFound("Conversa não encontrada neste tenant")
        assert_same_tenant(conversa, envelope.tenant_id)

        prospect = session.get(Prospect, conversa.prospect_id)
        if prospect is None:
            raise NotFound("Prospect da conversa não encontrado")
        contato = session.get(Contact, prospect.contact_id)
        if contato is None:
            raise NotFound("Contato da conversa não encontrado")

        historico = list(
            session.execute(
                select(Message)
                .where(Message.conversation_id == conversa.id)
                .order_by(Message.created_at.desc())
                .limit(HISTORY_LIMIT)
            ).scalars()
        )[::-1]
        if not historico:
            raise ResearchFailed("Conversa sem mensagens: nada a responder")
        return conversa, prospect, contato, historico

    # ---------------------------------------------------------------- modelo
    def _build_prompt(
        self, context: AgentContext, contato: Contact, historico: list[Message]
    ) -> str:
        partes = [
            "## Quem você representa",
            _fmt(
                {
                    "empresa": context.tenant["name"],
                    "posicionamento": context.company_brain.get("positioning"),
                    "oferta": (context.campaign or {}).get("offer"),
                    "tom_de_voz": context.company_brain.get("brand_voice"),
                    "objeções_conhecidas": context.company_brain.get("objections"),
                    "políticas": context.policies,
                }
            ),
            "\n## Com quem você fala",
            _fmt({"nome": contato.full_name, "cargo": contato.title}),
            "\n## Base de conhecimento (a resposta sai daqui, e só daqui)",
            _fmt([c["content"] for c in context.knowledge], limite=8000),
            "\n## Conversa até aqui",
            _fmt(
                [
                    {
                        "de": "lead" if m.direction == MessageDirection.INBOUND.value else "nós",
                        "assunto": m.subject,
                        "texto": m.body,
                    }
                    for m in historico
                ],
                limite=6000,
            ),
            "\nResponda a última mensagem do lead no formato pedido.",
        ]
        return "\n".join(partes)

    def _answer(
        self, model: str, context: AgentContext, prompt: str
    ) -> tuple[ConversationReply, dict]:
        client = self._client_factory()
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
            output_format=ConversationReply,
        )
        _accumulate(usage, getattr(response, "usage", None))

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            # Recusa do modelo é sinal de que um humano deve olhar, não de erro.
            return (
                ConversationReply(
                    reply="",
                    escalate=True,
                    escalation_reason="o modelo recusou responder a esta mensagem",
                ),
                usage,
            )
        if stop == "max_tokens":
            raise ResearchFailed("Resposta truncada; aumente ai_max_output_tokens")

        resposta = getattr(response, "parsed_output", None)
        if not isinstance(resposta, ConversationReply):
            raise ResearchFailed("A resposta do modelo não veio no formato esperado")
        return resposta, usage

    # ---------------------------------------------------------------- persistência
    def _persist(
        self,
        session: Session,
        envelope: JobEnvelope,
        conversa: Conversation,
        prospect: Prospect,
        contato: Contact,
        resposta: ConversationReply,
    ) -> Message:
        if resposta.opt_out_requested:
            # Obedecer não depende de o texto seguinte ser bom.
            contato.opted_out = True
            conversa.status = "opted_out"
            prospect.status = ProspectStatus.DISQUALIFIED.value
        elif resposta.escalate:
            conversa.status = "needs_human"
        elif resposta.meeting_intent:
            conversa.status = "meeting_intent"

        if prospect.status in (
            ProspectStatus.CONTACTED.value,
            ProspectStatus.SCORED.value,
        ) and not resposta.opt_out_requested:
            prospect.status = ProspectStatus.ENGAGED.value

        mensagem = Message(
            tenant_id=envelope.tenant_id,
            conversation_id=conversa.id,
            direction=MessageDirection.OUTBOUND.value,
            status=MessageStatus.DRAFT.value,
            channel=conversa.channel,
            subject=conversa.subject,
            body=resposta.reply,
            metrics={
                "escalate": resposta.escalate,
                "escalation_reason": resposta.escalation_reason,
                "meeting_intent": resposta.meeting_intent,
                "grounded_in": resposta.grounded_in,
                "to": contato.email,
            },
        )
        session.add(mensagem)
        session.flush()
        return mensagem
