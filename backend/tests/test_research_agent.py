"""Research Agent com execução real — exercitado com um cliente falso.

A suíte cobre o caminho inteiro sem rede: contexto, chamada, retomada de
turno pausado, teto de custo, persistência e consumo. O que não dá para testar
aqui é a qualidade da pesquisa; isso se mede com a chave de verdade.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from app.ai.pricing import cost_micro_usd, price_for
from app.core.errors import NotFound
from app.db.models.ai import AgentRun
from app.db.models.platform import UsageEvent
from app.db.models.sales import Campaign, Company, Research
from app.db.session import tenant_session
from app.orchestrator.executors.research import (
    CostCeilingExceeded,
    ResearchExecutor,
    ResearchFailed,
    ResearchOutput,
)
from app.orchestrator.runner import new_job, register_executor, run_job

SAIDA_VALIDA = {
    "summary": "Mineradora de médio porte em expansão no Canadá.",
    "icp_fit": "forte",
    "icp_rationale": "Porte e geografia batem com o ICP da campanha.",
    "findings": [
        {
            "claim": "Abriu duas vagas de gerente de operações em Sudbury",
            "evidence": "Página de carreiras, duas vagas publicadas em março",
            "source_url": "https://exemplo.ca/carreiras",
        }
    ],
    "signals": ["contratação em operações"],
    "unknowns": ["Não foi possível confirmar o sistema de gestão atual"],
    "recommended_angle": "Entrar pela dor de controle de turno em expansão.",
}


@dataclass
class FakeUsage:
    input_tokens: int = 1_000
    output_tokens: int = 500
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: ResearchOutput | None = None
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)
    content: list = field(default_factory=list)
    stop_details: object | None = None


class FakeMessages:
    def __init__(self, respostas: list[FakeResponse]):
        self._respostas = list(respostas)
        self.chamadas: list[dict] = []

    def parse(self, **params):
        self.chamadas.append(params)
        if not self._respostas:
            raise AssertionError("cliente falso chamado mais vezes do que o previsto")
        return self._respostas.pop(0)


class FakeClient:
    def __init__(self, respostas: list[FakeResponse]):
        self.messages = FakeMessages(respostas)


def _executor(respostas: list[FakeResponse]) -> tuple[ResearchExecutor, FakeClient]:
    cliente = FakeClient(respostas)
    return ResearchExecutor(client_factory=lambda: cliente), cliente


def _resposta_ok(**kwargs) -> FakeResponse:
    return FakeResponse(parsed_output=ResearchOutput.model_validate(SAIDA_VALIDA), **kwargs)


@pytest.fixture
def cenario(make_tenant):
    """Tenant com campanha e uma conta-alvo pronta para pesquisar."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(
            tenant_id=t["tenant_id"],
            name="Mining Canada",
            slug="mining-canada",
            icp={"pais": "CA", "porte": "100-500"},
            offer={"produto": "gestão de turnos"},
        )
        empresa = Company(
            tenant_id=t["tenant_id"],
            name="Northern Ore",
            domain="northernore.ca",
            industry="mineração",
            country="CA",
        )
        session.add_all([campanha, empresa])
        session.flush()
        return {**t, "campaign_id": campanha.id, "company_id": empresa.id}


def test_pesquisa_grava_resultado_consumo_e_custo(cenario):
    executor, cliente = _executor([_resposta_ok()])
    register_executor("research", executor)

    envelope = new_job(
        tenant_id=cenario["tenant_id"],
        agent="research",
        campaign_id=cenario["campaign_id"],
        entity_type="company",
        entity_id=cenario["company_id"],
        user_id=cenario["user_id"],
    )
    with tenant_session(cenario["tenant_id"]) as session:
        run = run_job(session, envelope)
        assert run.status == "succeeded"
        assert run.input_tokens == 1_000 and run.output_tokens == 500
        assert run.output["icp_fit"] == "forte"

    with tenant_session(cenario["tenant_id"]) as session:
        pesquisa = session.execute(select(Research)).scalars().one()
        assert pesquisa.entity_id == cenario["company_id"]
        assert pesquisa.findings["signals"] == ["contratação em operações"]
        assert pesquisa.sources == ["https://exemplo.ca/carreiras"]

        evento = session.execute(select(UsageEvent)).scalars().one()
        assert evento.kind == "research" and evento.units == 1
        # US$ 5/MTok de entrada e US$ 25/MTok de saída no Opus 5
        assert evento.cost_micro_usd == 1_000 * 5 + 500 * 25

    # O prompt levou o ICP da campanha e a conta-alvo — e nada mais.
    prompt = cliente.messages.chamadas[0]["messages"][0]["content"]
    assert "Northern Ore" in prompt and "Mining Canada" in prompt


def test_turno_pausado_e_retomado(cenario):
    """Busca na web pode pausar o turno; a execução continua de onde parou."""
    pausada = FakeResponse(stop_reason="pause_turn", content=[], usage=FakeUsage(800, 100))
    executor, cliente = _executor([pausada, _resposta_ok()])
    register_executor("research", executor)

    envelope = new_job(
        tenant_id=cenario["tenant_id"],
        agent="research",
        campaign_id=cenario["campaign_id"],
        entity_type="company",
        entity_id=cenario["company_id"],
    )
    with tenant_session(cenario["tenant_id"]) as session:
        run = run_job(session, envelope)

    assert len(cliente.messages.chamadas) == 2
    # O custo soma os dois turnos, não só o último.
    assert run.input_tokens == 1_800 and run.output_tokens == 600
    assert run.status == "succeeded"


def test_teto_de_custo_recusa_a_execucao(cenario):
    caro = _resposta_ok(usage=FakeUsage(input_tokens=200_000, output_tokens=100_000))
    executor, _ = _executor([caro])
    register_executor("research", executor)

    envelope = new_job(
        tenant_id=cenario["tenant_id"],
        agent="research",
        entity_type="company",
        entity_id=cenario["company_id"],
    )
    with pytest.raises(CostCeilingExceeded):
        with tenant_session(cenario["tenant_id"]) as session:
            run_job(session, envelope)

    with tenant_session(cenario["tenant_id"]) as session:
        # Recusa fica registrada, e nada de pesquisa pela metade no banco.
        run = session.execute(select(AgentRun)).scalars().one()
        assert run.status == "rejected"
        assert session.execute(select(Research)).scalars().all() == []


def test_recusa_do_modelo_vira_falha_explicita(cenario):
    recusada = FakeResponse(stop_reason="refusal")
    executor, _ = _executor([recusada])
    register_executor("research", executor)

    envelope = new_job(
        tenant_id=cenario["tenant_id"],
        agent="research",
        entity_type="company",
        entity_id=cenario["company_id"],
    )
    with pytest.raises(ResearchFailed):
        with tenant_session(cenario["tenant_id"]) as session:
            run_job(session, envelope)


def test_conta_de_outro_tenant_nao_e_pesquisada(cenario, make_tenant):
    """O alvo precisa ser do tenant dono do job — id alheio não serve."""
    outro = make_tenant()
    with tenant_session(outro["tenant_id"]) as session:
        alheia = Company(tenant_id=outro["tenant_id"], name="Empresa Alheia")
        session.add(alheia)
        session.flush()
        alheia_id = alheia.id

    executor, cliente = _executor([_resposta_ok()])
    register_executor("research", executor)
    envelope = new_job(
        tenant_id=cenario["tenant_id"],
        agent="research",
        entity_type="company",
        entity_id=alheia_id,
    )
    with pytest.raises(Exception) as exc:
        with tenant_session(cenario["tenant_id"]) as session:
            run_job(session, envelope)

    assert exc.value.code in {"not_found", "cross_tenant_access"}
    assert cliente.messages.chamadas == []  # nem chegou a chamar o modelo


def test_entidade_inexistente_nao_chama_o_modelo(cenario):
    executor, cliente = _executor([_resposta_ok()])
    register_executor("research", executor)
    envelope = new_job(
        tenant_id=cenario["tenant_id"],
        agent="research",
        entity_type="company",
        entity_id=uuid.uuid4(),
    )
    with pytest.raises(NotFound):
        with tenant_session(cenario["tenant_id"]) as session:
            run_job(session, envelope)
    assert cliente.messages.chamadas == []


def test_preco_por_modelo_e_custo_em_micro_dolar():
    assert price_for("claude-opus-5").input_per_mtok == 5.00
    assert cost_micro_usd("claude-opus-5", input_tokens=1_000_000, output_tokens=0) == 5_000_000
    assert cost_micro_usd("claude-sonnet-5", input_tokens=0, output_tokens=1_000_000) == 10_000_000
    # Modelo fora da tabela não pode custar zero: superestima, não some.
    assert cost_micro_usd("modelo-novo", input_tokens=1_000_000, output_tokens=0) == 10_000_000
    # Tokens de cache entram no custo de entrada.
    assert cost_micro_usd(
        "claude-opus-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    ) == 5_000_000
