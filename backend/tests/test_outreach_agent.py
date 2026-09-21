"""Outreach Agent: rascunho ancorado na pesquisa, e os limites em volta dele."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from app.db.models.engagement import Conversation, Message
from app.db.models.sales import Campaign, Company, Contact, Prospect, Research
from app.db.session import tenant_session
from app.orchestrator.executors.outreach import OutreachBlocked, OutreachDraft, OutreachExecutor
from app.orchestrator.runner import new_job, register_executor, run_job

RASCUNHO = {
    "subject": "Turnos em Sudbury",
    "body": "Vi as duas vagas de gerente de operações em Sudbury...",
    "anchors": [{"fact": "Duas vagas em Sudbury", "how_used": "abertura da mensagem"}],
    "call_to_action": "Vale 15 minutos na semana que vem?",
    "rationale": "Expansão operacional é o gatilho; entrei por controle de turno.",
}


@dataclass
class FakeUsage:
    input_tokens: int = 2_000
    output_tokens: int = 400
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: OutreachDraft | None = None
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


def _registrar(respostas=None) -> FakeClient:
    respostas = respostas or [
        FakeResponse(parsed_output=OutreachDraft.model_validate(RASCUNHO))
    ]
    cliente = FakeClient(respostas)
    register_executor("outreach", OutreachExecutor(client_factory=lambda *_: cliente))
    return cliente


@pytest.fixture
def prospect_pesquisado(make_tenant):
    """Prospect com pesquisa feita — o estado em que a abordagem é permitida."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(
            tenant_id=t["tenant_id"],
            name="Mining Canada",
            slug="mining-canada",
            offer={"produto": "gestão de turnos"},
            daily_limits={"emails": 50},
        )
        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore", country="CA")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"],
            company_id=empresa.id,
            full_name="Alice Tremblay",
            email="alice@northernore.ca",
            title="CFO",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="scored",
        )
        session.add(prospect)
        session.add(
            Research(
                tenant_id=t["tenant_id"],
                entity_type="company",
                entity_id=empresa.id,
                campaign_id=campanha.id,
                summary="Mineradora em expansão",
                findings={"icp_fit": "forte", "signals": ["contratação em Sudbury"]},
            )
        )
        session.flush()
        return {
            **t,
            "campaign_id": campanha.id,
            "prospect_id": prospect.id,
            "contact_id": contato.id,
        }


def _job(cenario):
    return new_job(
        tenant_id=cenario["tenant_id"],
        agent="outreach",
        campaign_id=cenario["campaign_id"],
        entity_type="prospect",
        entity_id=cenario["prospect_id"],
    )


def test_rascunho_nasce_como_rascunho_e_nao_enviado(prospect_pesquisado):
    """O limite mais importante: a IA escreve, ninguém dispara sozinho."""
    _registrar()
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run = run_job(session, _job(prospect_pesquisado))

    assert run.status == "succeeded"
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        mensagem = session.execute(select(Message)).scalars().one()
        assert mensagem.status == "draft"
        assert mensagem.sent_at is None
        assert mensagem.direction == "outbound"
        assert mensagem.metrics["to"] == "alice@northernore.ca"
        # Os ganchos usados ficam gravados: dá para revisar de onde veio cada
        # personalização sem reabrir a pesquisa.
        assert mensagem.metrics["anchors"][0]["fact"] == "Duas vagas em Sudbury"
        assert session.execute(select(Conversation)).scalars().one().channel == "email"


def test_prospect_continua_no_mesmo_estagio_ate_o_envio(prospect_pesquisado):
    _registrar()
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run_job(session, _job(prospect_pesquisado))

    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        prospect = session.get(Prospect, prospect_pesquisado["prospect_id"])
    # "contacted" é depois do envio, não depois do rascunho — senão o funil
    # contaria como abordado quem ninguém abordou.
    assert prospect.status == "scored"


def test_a_pesquisa_da_conta_entra_no_prompt(prospect_pesquisado):
    cliente = _registrar()
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run_job(session, _job(prospect_pesquisado))

    prompt = cliente.messages.chamadas[0]["messages"][0]["content"]
    assert "contratação em Sudbury" in prompt
    assert "Alice Tremblay" in prompt


def test_sem_pesquisa_nao_escreve(make_tenant):
    """Abordagem sem pesquisa é spam com nome próprio."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(tenant_id=t["tenant_id"], name="C", slug="c")
        empresa = Company(tenant_id=t["tenant_id"], name="Sem Pesquisa SA")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"],
            company_id=empresa.id,
            full_name="Bruno",
            email="bruno@sempesquisa.com",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
        )
        session.add(prospect)
        session.flush()
        cenario = {**t, "campaign_id": campanha.id, "prospect_id": prospect.id}

    cliente = _registrar()
    with pytest.raises(OutreachBlocked) as exc:
        with tenant_session(t["tenant_id"]) as session:
            run_job(session, _job(cenario))

    assert "pesquisa" in exc.value.message.lower()
    assert cliente.messages.chamadas == []  # não gastou token


def test_descadastro_e_respeitado(prospect_pesquisado):
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        session.get(Contact, prospect_pesquisado["contact_id"]).opted_out = True

    cliente = _registrar()
    with pytest.raises(OutreachBlocked):
        with tenant_session(prospect_pesquisado["tenant_id"]) as session:
            run_job(session, _job(prospect_pesquisado))
    assert cliente.messages.chamadas == []


def test_limite_diario_da_campanha_trava_o_volume(prospect_pesquisado):
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        session.get(Campaign, prospect_pesquisado["campaign_id"]).daily_limits = {"emails": 1}

    _registrar([FakeResponse(parsed_output=OutreachDraft.model_validate(RASCUNHO))] * 2)
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run_job(session, _job(prospect_pesquisado))

    with pytest.raises(OutreachBlocked) as exc:
        with tenant_session(prospect_pesquisado["tenant_id"]) as session:
            run_job(session, _job(prospect_pesquisado))
    assert exc.value.details["limit"] == 1


def test_campanha_pausada_nao_aborda(prospect_pesquisado):
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        session.get(Campaign, prospect_pesquisado["campaign_id"]).status = "paused"

    _registrar()
    with pytest.raises(OutreachBlocked):
        with tenant_session(prospect_pesquisado["tenant_id"]) as session:
            run_job(session, _job(prospect_pesquisado))


def test_prospect_de_outro_tenant_nao_e_abordado(prospect_pesquisado, make_tenant):
    outro = make_tenant()
    cliente = _registrar()
    envelope = new_job(
        tenant_id=outro["tenant_id"],
        agent="outreach",
        entity_type="prospect",
        entity_id=prospect_pesquisado["prospect_id"],
    )
    with pytest.raises(Exception) as exc:
        with tenant_session(outro["tenant_id"]) as session:
            run_job(session, envelope)
    assert exc.value.code in {"not_found", "cross_tenant_access"}
    assert cliente.messages.chamadas == []
