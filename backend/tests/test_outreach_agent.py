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


def test_teto_de_uma_campanha_nao_come_a_cota_da_outra(prospect_pesquisado):
    """O teto diário é da campanha; contá-lo no tenant inteiro misturava as duas.

    Duas campanhas de 50 na mesma empresa entregavam 50 no total, e uma campanha
    pequena travava a abordagem de todas as demais — com a tela dizendo "limite
    diário atingido" numa campanha que não mandou nada hoje.
    """
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        session.get(Campaign, prospect_pesquisado["campaign_id"]).daily_limits = {"emails": 1}

    # Uma mensagem de hoje, de OUTRA campanha, para outro prospect.
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        outra = Campaign(
            tenant_id=prospect_pesquisado["tenant_id"], name="Agro BR", slug="agro-br"
        )
        session.add(outra)
        session.flush()
        contato = Contact(
            tenant_id=prospect_pesquisado["tenant_id"],
            full_name="Bruno Alves",
            email="bruno@agro.br",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=prospect_pesquisado["tenant_id"],
            campaign_id=outra.id,
            contact_id=contato.id,
            status="scored",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=prospect_pesquisado["tenant_id"],
            prospect_id=prospect.id,
            campaign_id=outra.id,
            channel="email",
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=prospect_pesquisado["tenant_id"],
                conversation_id=conversa.id,
                direction="outbound",
                status="sent",
                channel="email",
                subject="Outra campanha",
                body="Mensagem de outra campanha.",
            )
        )

    _registrar()
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run = run_job(session, _job(prospect_pesquisado))
    assert run.status == "succeeded"


def test_lead_que_ja_respondeu_nao_recebe_abordagem_fria(prospect_pesquisado):
    """A regra de parada também vale no executor, não só na cadência.

    Entre enfileirar o passo e executá-lo passa tempo — e o disparo manual pela
    tela de agentes não passa por cadência nenhuma. Sem a guarda aqui, um clique
    escreve primeira abordagem para quem já está conversando.
    """
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        conversa = Conversation(
            tenant_id=prospect_pesquisado["tenant_id"],
            prospect_id=prospect_pesquisado["prospect_id"],
            campaign_id=prospect_pesquisado["campaign_id"],
            channel="email",
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=prospect_pesquisado["tenant_id"],
                conversation_id=conversa.id,
                direction="inbound",
                status="replied",
                channel="email",
                body="Interessante, me manda mais detalhes.",
            )
        )

    cliente = _registrar()
    with pytest.raises(OutreachBlocked) as exc:
        with tenant_session(prospect_pesquisado["tenant_id"]) as session:
            run_job(session, _job(prospect_pesquisado))
    assert "respondeu" in exc.value.message
    assert cliente.messages.chamadas == []


def test_prospect_em_estagio_terminal_nao_e_abordado(prospect_pesquisado):
    """Bounce, desqualificado, reunião marcada: a abordagem fria não se aplica.

    Escrever de novo para um endereço que deu bounce é o tipo de disparo que o
    provedor lê como spam — e o domínio de quem manda é o que paga.
    """
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        session.get(Prospect, prospect_pesquisado["prospect_id"]).status = "bounced"

    cliente = _registrar()
    with pytest.raises(OutreachBlocked) as exc:
        with tenant_session(prospect_pesquisado["tenant_id"]) as session:
            run_job(session, _job(prospect_pesquisado))
    assert exc.value.details["status"] == "bounced"
    assert cliente.messages.chamadas == []


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


def test_follow_up_ve_o_que_ja_foi_mandado(prospect_pesquisado):
    """O segundo toque não pode sair igual ao primeiro.

    Um follow-up que repete a abordagem é pior do que não mandar nada: prova
    que do outro lado não tem ninguém lendo. O prompt do toque seguinte carrega
    o texto já enviado e a instrução daquele passo.
    """
    cliente = _registrar(
        [
            FakeResponse(parsed_output=OutreachDraft.model_validate(RASCUNHO)),
            FakeResponse(parsed_output=OutreachDraft.model_validate(RASCUNHO)),
        ]
    )
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run_job(session, _job(prospect_pesquisado))  # toque 1

    segundo = new_job(
        tenant_id=prospect_pesquisado["tenant_id"],
        agent="outreach",
        campaign_id=prospect_pesquisado["campaign_id"],
        entity_type="prospect",
        entity_id=prospect_pesquisado["prospect_id"],
        params={
            "sequence_step": {
                "order": 2,
                "instruction": "Lembrete curto citando um caso parecido",
                "total_steps": 3,
            }
        },
    )
    with tenant_session(prospect_pesquisado["tenant_id"]) as session:
        run = run_job(session, segundo)
    assert run.status == "succeeded"

    prompt = cliente.messages.chamadas[-1]["messages"][0]["content"]
    assert "toque 2 de 3" in prompt
    assert "Lembrete curto citando um caso parecido" in prompt
    assert "não repete o argumento já usado" in prompt
    # O corpo da primeira mensagem está no prompt do segundo toque.
    assert "Vi as duas vagas de gerente de operações" in prompt

    # O primeiro toque não tinha nada disso.
    primeiro_prompt = cliente.messages.chamadas[0]["messages"][0]["content"]
    assert "toque" not in primeiro_prompt
    assert "Escreva a primeira abordagem" in primeiro_prompt
