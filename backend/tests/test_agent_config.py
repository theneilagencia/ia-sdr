"""Configuração por agente: o que existia no banco e não tinha porta.

A tabela `ai_agents` sempre foi lida pelo orquestrador — modelo, instruções e
teto de saída por empresa. Só não havia como escrever nela sem `INSERT` na mão,
o que significa que a configuração existia no papel e não na plataforma.

O que estes testes protegem é a ligação entre as duas pontas: o que a tela salva
é o que chega ao modelo na próxima execução.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import settings
from app.db.models.ai import AIAgent
from app.db.session import tenant_session
from app.orchestrator.executors.research import ResearchExecutor, ResearchOutput
from app.orchestrator.runner import _EXECUTORS, new_job, run_job

SAIDA = {
    "summary": "Mineradora de médio porte em expansão.",
    "icp_fit": "forte",
    "icp_rationale": "Porte e geografia batem com o ICP.",
    "findings": [],
    "signals": [],
    "unknowns": [],
    "recommended_angle": "Entrar pela dor de controle de turno.",
}


@dataclass
class FakeUsage:
    input_tokens: int = 500
    output_tokens: int = 100
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
    def __init__(self):
        self.chamadas: list[dict] = []

    def parse(self, **params):
        self.chamadas.append(params)
        return FakeResponse(parsed_output=ResearchOutput.model_validate(SAIDA))


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


def _registrar(monkeypatch) -> FakeClient:
    """Registra o executor **só para este teste**.

    `register_executor` escreve num dicionário de módulo que vive o processo
    inteiro: registrar direto aqui vazava um executor de verdade para os testes
    que rodam depois — e o do orquestrador, que conta com o eco, quebrava sem ter
    nada a ver com isto.
    """
    cliente = FakeClient()
    monkeypatch.setitem(_EXECUTORS, "research", ResearchExecutor(client_factory=lambda *_: cliente))
    return cliente


# ------------------------------------------------------------------ leitura


def test_config_comeca_no_padrao_da_plataforma(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.get("/api/v1/agents/config", headers=headers)
    assert r.status_code == 200, r.text
    config = {a["kind"]: a for a in r.json()}
    assert set(config) == {"research", "outreach", "conversation", "qualification"}

    pesquisa = config["research"]
    # Nada configurado ainda: a tela precisa poder dizer "este é o padrão", e
    # não "isto foi você quem escolheu".
    assert pesquisa["configured"] is False
    assert pesquisa["model"] == pesquisa["default_model"]
    assert pesquisa["instructions"] == pesquisa["default_instructions"]
    assert pesquisa["units_per_run"] == 1
    assert config["qualification"]["units_per_run"] == 2


def test_o_teto_de_saida_padrao_e_o_da_plataforma(client, make_tenant, auth_headers, monkeypatch):
    """O 2000 fixo escondia o ajuste da plataforma.

    O contexto devolvia 2000 quando não havia configuração, e o executor faz
    `... or settings.ai_max_output_tokens`: o 2000 sempre ganhava. O ajuste de
    8000 nunca valeu para ninguém — e a mensagem de "resposta truncada" mandava
    aumentar justamente esse número sem efeito.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    config = {a["kind"]: a for a in client.get("/api/v1/agents/config", headers=headers).json()}
    assert config["research"]["max_output_tokens"] == settings.ai_max_output_tokens

    cliente = _registrar(monkeypatch)
    with tenant_session(t["tenant_id"]) as session:
        from app.db.models.sales import Company

        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        session.add(empresa)
        session.flush()
        alvo = empresa.id

    with tenant_session(t["tenant_id"]) as session:
        run_job(
            session,
            new_job(
                tenant_id=t["tenant_id"],
                agent="research",
                entity_type="company",
                entity_id=alvo,
            ),
        )
    assert cliente.messages.chamadas[0]["max_tokens"] == settings.ai_max_output_tokens


# ------------------------------------------------------------------ escrita


def test_o_que_a_tela_salva_chega_ao_modelo(client, make_tenant, auth_headers, monkeypatch):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    salvo = client.put(
        "/api/v1/agents/config/research",
        headers=headers,
        json={
            "model": "claude-haiku-4-5",
            "instructions": "Foque em mineração canadense e cite a fonte de tudo.",
            "max_output_tokens": 1_500,
        },
    )
    assert salvo.status_code == 200, salvo.text
    assert salvo.json()["configured"] is True
    assert salvo.json()["model"] == "claude-haiku-4-5"

    cliente = _registrar(monkeypatch)
    with tenant_session(t["tenant_id"]) as session:
        from app.db.models.sales import Company

        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        session.add(empresa)
        session.flush()
        alvo = empresa.id

    with tenant_session(t["tenant_id"]) as session:
        run = run_job(
            session,
            new_job(
                tenant_id=t["tenant_id"],
                agent="research",
                entity_type="company",
                entity_id=alvo,
            ),
        )

    chamada = cliente.messages.chamadas[0]
    assert chamada["model"] == "claude-haiku-4-5"
    assert chamada["max_tokens"] == 1_500
    assert "mineração canadense" in chamada["system"]
    # E o custo é calculado pelo preço do modelo escolhido, não pelo do padrão.
    assert run.model == "claude-haiku-4-5"
    assert run.status == "succeeded"


def test_editar_de_novo_nao_apaga_o_que_nao_veio(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.put(
        "/api/v1/agents/config/outreach",
        headers=headers,
        json={"model": "claude-sonnet-5", "instructions": "Duas frases, sem adjetivo."},
    )
    segundo = client.put(
        "/api/v1/agents/config/outreach",
        headers=headers,
        json={"max_output_tokens": 900},
    )
    assert segundo.status_code == 200, segundo.text
    assert segundo.json()["model"] == "claude-sonnet-5"
    assert segundo.json()["instructions"] == "Duas frases, sem adjetivo."
    assert segundo.json()["max_output_tokens"] == 900

    with tenant_session(t["tenant_id"]) as session:
        linhas = session.execute(select(AIAgent)).scalars().all()
    # Uma linha por agente, não uma por edição.
    assert len(linhas) == 1


def test_instrucao_vazia_volta_ao_padrao(client, make_tenant, auth_headers):
    """O caminho de saída de quem escreveu uma instrução ruim."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.put(
        "/api/v1/agents/config/conversation",
        headers=headers,
        json={"instructions": "Prometa qualquer prazo que o lead pedir."},
    )
    limpo = client.put(
        "/api/v1/agents/config/conversation",
        headers=headers,
        json={"instructions": ""},
    )
    assert limpo.status_code == 200, limpo.text
    assert limpo.json()["instructions"] == limpo.json()["default_instructions"]


def test_modelo_desconhecido_e_recusado(client, make_tenant, auth_headers):
    """Modelo fora da tabela de preços custaria pelo teto — ou nem existiria.

    Descobrir isso no primeiro disparo do cliente é o pior momento possível.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.put(
        "/api/v1/agents/config/research",
        headers=headers,
        json={"model": "gpt-4o"},
    )
    assert r.status_code == 422, r.text
    assert "desconhecido" in r.text

    with tenant_session(t["tenant_id"]) as session:
        assert session.execute(select(AIAgent)).scalars().all() == []


def test_teto_de_saida_fora_da_faixa_e_recusado(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    for valor in (10, 100_000):
        r = client.put(
            "/api/v1/agents/config/research",
            headers=headers,
            json={"max_output_tokens": valor},
        )
        assert r.status_code == 422, f"{valor} deveria ter sido recusado"


def test_agente_inexistente_nao_cria_configuracao(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.put(
        "/api/v1/agents/config/vendedor-magico",
        headers=headers,
        json={"model": "claude-opus-5"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "unknown_agent"


def test_config_de_uma_empresa_nao_vaza_para_outra(client, make_tenant, auth_headers):
    a = make_tenant()
    b = make_tenant()
    client.put(
        "/api/v1/agents/config/research",
        headers=auth_headers(a["email"], a["password"]),
        json={"model": "claude-haiku-4-5", "instructions": "Segredo da empresa A."},
    )

    config = {
        item["kind"]: item
        for item in client.get(
            "/api/v1/agents/config", headers=auth_headers(b["email"], b["password"])
        ).json()
    }
    assert config["research"]["configured"] is False
    assert "Segredo" not in config["research"]["instructions"]


def test_quem_so_le_nao_configura_agente(client, make_tenant, auth_headers, membro):
    """Trocar o modelo é decisão de custo: não é para quem só acompanha."""
    t = make_tenant()
    viewer = membro(t, role="viewer")
    headers = auth_headers(viewer["email"], viewer["password"])
    assert client.get("/api/v1/agents/config", headers=headers).status_code == 200
    negado = client.put(
        "/api/v1/agents/config/research",
        headers=headers,
        json={"model": "claude-haiku-4-5"},
    )
    assert negado.status_code == 403, negado.text
