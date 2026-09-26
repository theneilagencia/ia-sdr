"""Conversation Agent: responder é fácil; saber a hora de calar é o que importa."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.engagement import Conversation, Message
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session
from app.orchestrator.executors.conversation import (
    ConversationBlocked,
    ConversationExecutor,
    ConversationReply,
)
from app.orchestrator.runner import new_job, register_executor, run_job

RESPOSTA_SIMPLES = {
    "reply": "Sim, o controle de turno cobre escalas 24/7. Faz sentido para Sudbury?",
    "escalate": False,
    "meeting_intent": False,
    "grounded_in": ["Playbook: escalas 24/7 suportadas"],
}


@dataclass
class FakeUsage:
    input_tokens: int = 3_000
    output_tokens: int = 200
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: ConversationReply | None = None
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)
    content: list = field(default_factory=list)


class FakeMessages:
    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = []

    def parse(self, **params):
        self.chamadas.append(params)
        return self._respostas.pop(0)


class FakeClient:
    def __init__(self, respostas):
        self.messages = FakeMessages(respostas)


def _registrar(payload: dict | None = None, stop_reason: str = "end_turn") -> FakeClient:
    parsed = ConversationReply.model_validate(payload) if payload is not None else None
    cliente = FakeClient([FakeResponse(parsed_output=parsed, stop_reason=stop_reason)])
    register_executor("conversation", ConversationExecutor(client_factory=lambda *_: cliente))
    return cliente


@pytest.fixture
def conversa_aberta(make_tenant):
    """Prospect que já recebeu abordagem e respondeu."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(tenant_id=t["tenant_id"], name="Mining Canada", slug="mc")
        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        doc = KnowledgeDocument(tenant_id=t["tenant_id"], title="Playbook", status="indexed")
        session.add_all([campanha, empresa, doc])
        session.flush()
        session.add(
            KnowledgeChunk(
                tenant_id=t["tenant_id"],
                document_id=doc.id,
                ordinal=0,
                content="Escalas 24/7 são suportadas desde a versão 3.",
            )
        )
        contato = Contact(
            tenant_id=t["tenant_id"],
            company_id=empresa.id,
            full_name="Alice Tremblay",
            email="alice@northernore.ca",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="contacted",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=t["tenant_id"],
            prospect_id=prospect.id,
            campaign_id=campanha.id,
            channel="email",
            subject="Turnos em Sudbury",
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=t["tenant_id"],
                conversation_id=conversa.id,
                direction="inbound",
                status="replied",
                channel="email",
                body="Vocês cobrem escala 24/7?",
            )
        )
        session.flush()
        return {
            **t,
            "campaign_id": campanha.id,
            "prospect_id": prospect.id,
            "conversation_id": conversa.id,
            "contact_id": contato.id,
        }


def _job(cenario):
    return new_job(
        tenant_id=cenario["tenant_id"],
        agent="conversation",
        campaign_id=cenario["campaign_id"],
        entity_type="conversation",
        entity_id=cenario["conversation_id"],
    )


def test_resposta_vira_rascunho_e_move_para_engajado(conversa_aberta):
    _registrar(RESPOSTA_SIMPLES)
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run = run_job(session, _job(conversa_aberta))
    assert run.status == "succeeded"

    with tenant_session(conversa_aberta["tenant_id"]) as session:
        saida = (
            session.execute(select(Message).where(Message.direction == "outbound")).scalars().one()
        )
        assert saida.status == "draft" and saida.sent_at is None
        assert session.get(Prospect, conversa_aberta["prospect_id"]).status == "engaged"


def test_base_de_conhecimento_e_historico_entram_no_prompt(conversa_aberta):
    cliente = _registrar(RESPOSTA_SIMPLES)
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run_job(session, _job(conversa_aberta))

    prompt = cliente.messages.chamadas[0]["messages"][0]["content"]
    assert "Escalas 24/7 são suportadas" in prompt
    assert "Vocês cobrem escala 24/7?" in prompt


def test_assunto_fora_da_base_escala_para_humano(conversa_aberta):
    _registrar(
        {
            "reply": "Vou confirmar internamente e te respondo.",
            "escalate": True,
            "escalation_reason": "pergunta sobre desconto por volume",
        }
    )
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run = run_job(session, _job(conversa_aberta))

    assert run.output["escalate"] is True
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        conversa = session.get(Conversation, conversa_aberta["conversation_id"])
        assert conversa.status == "needs_human"


def test_recusa_do_modelo_vira_escalonamento_e_nao_erro(conversa_aberta):
    """Recusar é sinal de que um humano deve olhar, não motivo para quebrar."""
    _registrar(payload=None, stop_reason="refusal")
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run = run_job(session, _job(conversa_aberta))

    assert run.status == "succeeded"
    assert run.output["escalate"] is True
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        assert session.get(Conversation, conversa_aberta["conversation_id"]).status == "needs_human"


def test_descadastro_e_obedecido_no_ato(conversa_aberta):
    _registrar(
        {
            "reply": "Entendido, não entro mais em contato. Obrigado pelo retorno.",
            "escalate": False,
            "opt_out_requested": True,
        }
    )
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run_job(session, _job(conversa_aberta))

    with tenant_session(conversa_aberta["tenant_id"]) as session:
        assert session.get(Contact, conversa_aberta["contact_id"]).opted_out is True
        assert session.get(Prospect, conversa_aberta["prospect_id"]).status == "disqualified"
        assert session.get(Conversation, conversa_aberta["conversation_id"]).status == "opted_out"


def test_interesse_em_reuniao_fica_marcado(conversa_aberta):
    _registrar({**RESPOSTA_SIMPLES, "meeting_intent": True})
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run_job(session, _job(conversa_aberta))
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        assert (
            session.get(Conversation, conversa_aberta["conversation_id"]).status == "meeting_intent"
        )


def test_conversa_sem_mensagem_do_lead_nao_gera_replica(conversa_aberta):
    """Sem pergunta na mesa, o agente estava respondendo a si mesmo.

    O histórico só tinha o nosso próprio email frio, e o prompt manda "responder
    a última mensagem do lead". A réplica ia para a fila de revisão como se
    alguém tivesse escrito algo.
    """
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        session.execute(
            select(Message).where(Message.direction == "inbound")
        ).scalars().one().direction = "outbound"

    cliente = _registrar(RESPOSTA_SIMPLES)
    with pytest.raises(ConversationBlocked):
        with tenant_session(conversa_aberta["tenant_id"]) as session:
            run_job(session, _job(conversa_aberta))
    assert cliente.messages.chamadas == []


def test_mensagem_do_lead_ja_respondida_nao_gera_segunda_replica(conversa_aberta):
    """Despachar a conversa duas vezes mandava dois emails sobre a mesma frase."""
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        entrada = (
            session.execute(select(Message).where(Message.direction == "inbound")).scalars().one()
        )
        session.add(
            Message(
                tenant_id=conversa_aberta["tenant_id"],
                conversation_id=conversa_aberta["conversation_id"],
                direction="outbound",
                status="sent",
                channel="email",
                body="Sim, cobrimos escala 24/7.",
                created_at=entrada.created_at + timedelta(minutes=5),
                sent_at=datetime.now(UTC),
            )
        )

    cliente = _registrar(RESPOSTA_SIMPLES)
    with pytest.raises(ConversationBlocked):
        with tenant_session(conversa_aberta["tenant_id"]) as session:
            run_job(session, _job(conversa_aberta))
    assert cliente.messages.chamadas == []


def test_rascunho_pendente_nao_impede_gerar_de_novo(conversa_aberta):
    """Enquanto ninguém aprovou, a última palavra continua sendo a do lead.

    Quem revisa precisa poder pedir outra versão; o freio é para réplica que já
    saiu, não para rascunho na fila.
    """
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        entrada = (
            session.execute(select(Message).where(Message.direction == "inbound")).scalars().one()
        )
        session.add(
            Message(
                tenant_id=conversa_aberta["tenant_id"],
                conversation_id=conversa_aberta["conversation_id"],
                direction="outbound",
                status="draft",
                channel="email",
                body="Primeira versão, ainda por aprovar.",
                created_at=entrada.created_at + timedelta(minutes=5),
            )
        )

    _registrar(RESPOSTA_SIMPLES)
    with tenant_session(conversa_aberta["tenant_id"]) as session:
        run = run_job(session, _job(conversa_aberta))
    assert run.status == "succeeded"


def test_conversa_de_outro_tenant_nao_e_respondida(conversa_aberta, make_tenant):
    outro = make_tenant()
    cliente = _registrar(RESPOSTA_SIMPLES)
    envelope = new_job(
        tenant_id=outro["tenant_id"],
        agent="conversation",
        entity_type="conversation",
        entity_id=conversa_aberta["conversation_id"],
    )
    with pytest.raises(Exception) as exc:
        with tenant_session(outro["tenant_id"]) as session:
            run_job(session, envelope)
    assert exc.value.code in {"not_found", "cross_tenant_access"}
    assert cliente.messages.chamadas == []


def test_resposta_recebida_pela_api_engaja_o_prospect(client, conversa_aberta, auth_headers):
    headers = auth_headers(conversa_aberta["email"], conversa_aberta["password"])
    r = client.post(
        f"/api/v1/prospects/{conversa_aberta['prospect_id']}/messages/inbound",
        headers=headers,
        json={"body": "Interessante, me manda mais detalhes."},
    )
    assert r.status_code == 201, r.text
    assert r.json()["direction"] == "inbound"

    mensagens = client.get(
        f"/api/v1/prospects/{conversa_aberta['prospect_id']}/messages", headers=headers
    ).json()
    assert len(mensagens) == 2
    funil = client.get("/api/v1/prospects/funnel", headers=headers).json()
    assert funil["engaged"] == 1
