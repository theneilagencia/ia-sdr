"""Qualification Agent: a decisão mais cara de errar da plataforma.

Falso positivo ocupa a agenda de um vendedor e queima a confiança dele no
sistema; falso negativo descarta um cliente. Estes testes cobrem os freios.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from app.db.models.engagement import Conversation, Message, Qualification
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session
from app.orchestrator.executors.outreach import OutreachBlocked
from app.orchestrator.executors.qualification import (
    MIN_CONFIDENCE,
    QualificationExecutor,
    QualificationOutput,
    _enforce_evidence,
)
from app.orchestrator.runner import new_job, register_executor, run_job

QUALIFICADO = {
    "outcome": "qualified",
    "criteria_results": [
        {
            "criterion": "Mais de 100 funcionários",
            "status": "met",
            "evidence": "Pesquisa: 450 funcionários",
        },
        {
            "criterion": "Dor de controle de turno",
            "status": "met",
            "evidence": "Lead: 'hoje controlamos escala em planilha'",
        },
    ],
    "rationale": "Porte e dor confirmados na conversa.",
    "confidence": 85,
    "next_step": "Propor 30 minutos com o gerente de operações",
    "proposed_agenda": "Como a escala é feita hoje e onde trava",
}


@dataclass
class FakeUsage:
    input_tokens: int = 4_000
    output_tokens: int = 300
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: QualificationOutput | None = None
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


def _registrar(payload: dict) -> FakeClient:
    cliente = FakeClient([FakeResponse(parsed_output=QualificationOutput.model_validate(payload))])
    register_executor("qualification", QualificationExecutor(client_factory=lambda *_: cliente))
    return cliente


# ------------------------------------------------------------------ regra de evidência


def test_criterio_cumprido_sem_evidencia_e_rebaixado():
    """Afirmação sem lastro não vira sim — e a regra vive no código."""
    bruto = QualificationOutput.model_validate(
        {
            **QUALIFICADO,
            "criteria_results": [
                {"criterion": "Porte", "status": "met", "evidence": "Pesquisa: 450 pessoas"},
                {"criterion": "Orçamento", "status": "met", "evidence": "  "},
            ],
        }
    )
    resultado, rebaixados = _enforce_evidence(bruto)

    assert rebaixados == 1
    assert [c.status for c in resultado.criteria_results] == ["met", "unknown"]
    # Perdeu lastro: não sai daqui como qualificado.
    assert resultado.outcome == "needs_more_info"
    assert resultado.confidence == 65


def test_confianca_baixa_nao_vira_qualificado():
    bruto = QualificationOutput.model_validate({**QUALIFICADO, "confidence": MIN_CONFIDENCE - 1})
    resultado, _ = _enforce_evidence(bruto)
    assert resultado.outcome == "needs_more_info"


def test_desqualificacao_com_confianca_baixa_continua_desqualificacao():
    """O freio é para não qualificar à toa; descartar já é o lado seguro."""
    bruto = QualificationOutput.model_validate(
        {
            **QUALIFICADO,
            "outcome": "disqualified",
            "confidence": 30,
            "criteria_results": [
                {"criterion": "Porte", "status": "not_met", "evidence": "12 funcionários"}
            ],
        }
    )
    resultado, _ = _enforce_evidence(bruto)
    assert resultado.outcome == "disqualified"


# ------------------------------------------------------------------ execução


@pytest.fixture
def prospect_em_conversa(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(
            tenant_id=t["tenant_id"],
            name="Mining Canada",
            slug="mc",
            qualification_criteria={
                "porte": "Mais de 100 funcionários",
                "dor": "Controle de turno manual",
            },
        )
        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"],
            company_id=empresa.id,
            full_name="Alice",
            email="alice@northernore.ca",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="engaged",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=t["tenant_id"], prospect_id=prospect.id, campaign_id=campanha.id
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=t["tenant_id"],
                conversation_id=conversa.id,
                direction="inbound",
                status="replied",
                body="Hoje controlamos escala em planilha, somos 450 pessoas.",
            )
        )
        session.flush()
        return {**t, "campaign_id": campanha.id, "prospect_id": prospect.id}


def _job(cenario):
    return new_job(
        tenant_id=cenario["tenant_id"],
        agent="qualification",
        campaign_id=cenario["campaign_id"],
        entity_type="prospect",
        entity_id=cenario["prospect_id"],
    )


def test_lead_qualificado_move_o_funil_e_registra_o_porque(prospect_em_conversa):
    cliente = _registrar(QUALIFICADO)
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        run = run_job(session, _job(prospect_em_conversa))

    assert run.output["outcome"] == "qualified"
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        assert session.get(Prospect, prospect_em_conversa["prospect_id"]).status == "qualified"
        registro = session.execute(select(Qualification)).scalars().one()
        assert registro.confidence == 85
        # Critério a critério, com a evidência de cada um.
        assert len(registro.criteria_results["criteria"]) == 2

    prompt = cliente.messages.chamadas[0]["messages"][0]["content"]
    assert "Mais de 100 funcionários" in prompt
    assert "controlamos escala em planilha" in prompt


def test_sem_criterios_na_campanha_nao_qualifica(prospect_em_conversa):
    """Critério implícito é critério de quem escreveu o prompt, não do cliente."""
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        session.get(Campaign, prospect_em_conversa["campaign_id"]).qualification_criteria = {}

    cliente = _registrar(QUALIFICADO)
    with pytest.raises(OutreachBlocked):
        with tenant_session(prospect_em_conversa["tenant_id"]) as session:
            run_job(session, _job(prospect_em_conversa))
    assert cliente.messages.chamadas == []  # nem gastou token


def test_precisa_de_mais_informacao_nao_mexe_no_funil(prospect_em_conversa):
    _registrar({**QUALIFICADO, "outcome": "needs_more_info", "confidence": 40})
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        run_job(session, _job(prospect_em_conversa))

    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        # Continua engajado: só veredito com lastro move alguém de estágio.
        assert session.get(Prospect, prospect_em_conversa["prospect_id"]).status == "engaged"
        assert session.execute(select(Qualification)).scalars().one().outcome == "needs_more_info"


def test_qualificado_sem_evidencia_nao_chega_a_qualificado(prospect_em_conversa):
    """O freio do código atravessa até o banco, não só a função pura."""
    _registrar(
        {
            **QUALIFICADO,
            "criteria_results": [{"criterion": "Porte", "status": "met", "evidence": None}],
        }
    )
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        run = run_job(session, _job(prospect_em_conversa))

    assert run.output["outcome"] == "needs_more_info"
    assert [c["status"] for c in run.output["criteria_results"]] == ["unknown"]
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        assert session.get(Prospect, prospect_em_conversa["prospect_id"]).status == "engaged"


def test_desqualificado_sai_do_funil(prospect_em_conversa):
    _registrar(
        {
            **QUALIFICADO,
            "outcome": "disqualified",
            "criteria_results": [
                {"criterion": "Porte", "status": "not_met", "evidence": "12 pessoas"}
            ],
        }
    )
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        run_job(session, _job(prospect_em_conversa))
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        assert session.get(Prospect, prospect_em_conversa["prospect_id"]).status == "disqualified"


def test_descadastro_nao_se_desfaz_por_veredito_de_agente(prospect_em_conversa):
    """O lead que pediu para sair não volta ao funil como oportunidade.

    O caminho era este: o lead pede descadastro, o Conversation Agent obedece —
    marca `opted_out` e leva o prospect para `disqualified` — e depois a
    qualificação roda. O modelo lê a conversa, vê uma empresa que bate com o ICP
    e devolve "qualified"; o estágio era reescrito e um vendedor ligaria para
    quem acabou de pedir para não ser mais procurado.
    """
    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        prospect = session.get(Prospect, prospect_em_conversa["prospect_id"])
        prospect.status = "disqualified"
        session.get(Contact, prospect.contact_id).opted_out = True

    cliente = _registrar(QUALIFICADO)
    with pytest.raises(OutreachBlocked) as exc:
        with tenant_session(prospect_em_conversa["tenant_id"]) as session:
            run_job(session, _job(prospect_em_conversa))
    assert "descadastro" in exc.value.message

    with tenant_session(prospect_em_conversa["tenant_id"]) as session:
        assert session.get(Prospect, prospect_em_conversa["prospect_id"]).status == "disqualified"
        assert session.execute(select(Qualification)).scalars().all() == []
    # E nem pagou pelo veredito que não podia ter efeito.
    assert cliente.messages.chamadas == []


def test_prospect_de_outro_tenant_nao_e_qualificado(prospect_em_conversa, make_tenant):
    outro = make_tenant()
    cliente = _registrar(QUALIFICADO)
    envelope = new_job(
        tenant_id=outro["tenant_id"],
        agent="qualification",
        entity_type="prospect",
        entity_id=prospect_em_conversa["prospect_id"],
    )
    with pytest.raises(Exception) as exc:
        with tenant_session(outro["tenant_id"]) as session:
            run_job(session, envelope)
    assert exc.value.code in {"not_found", "cross_tenant_access"}
    assert cliente.messages.chamadas == []


# ------------------------------------------------------------------ reunião


def test_reuniao_fecha_o_funil(client, prospect_em_conversa, auth_headers):
    headers = auth_headers(prospect_em_conversa["email"], prospect_em_conversa["password"])
    r = client.post(
        f"/api/v1/prospects/{prospect_em_conversa['prospect_id']}/meetings",
        headers=headers,
        json={"scheduled_at": "2026-10-01T15:00:00Z", "duration_minutes": 30},
    )
    assert r.status_code == 201, r.text

    funil = client.get("/api/v1/prospects/funnel", headers=headers).json()
    assert funil["meetings"] == 1
    # Reunião marcada conta como qualificado e engajado: o funil é cumulativo.
    assert funil["qualified"] == 1 and funil["engaged"] == 1
