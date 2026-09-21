"""Integração com o RAVI — o CRM que já existe.

O RAVI é atendido aqui por um `httpx.MockTransport`: nenhuma chamada sai para a
rede, e o contrato exercitado é o que foi lido no repositório do RAVI —
`POST /leads` autenticado por `Authorization` + `x-tenant-id`, upsert por email
ou telefone, 201 para lead novo e 200 para lead que já existia.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import NotFound
from app.db.models.engagement import Qualification
from app.db.models.sales import Campaign, Company, Contact, Prospect, Score
from app.db.session import tenant_session
from app.services import ravi

BASE = "https://api.ravi.exemplo.com"
TOKEN = "ravi-agent-token-abcd"
RAVI_TENANT = "tnt_ravi_123"


class RaviFalso:
    """Um RAVI de mentira que guarda o que recebeu."""

    def __init__(self, *, status_code: int = 201, corpo: dict | None = None):
        self.status_code = status_code
        self.corpo = corpo if corpo is not None else {"id": "lead_1", "temperature": "warm"}
        self.chamadas: list[httpx.Request] = []

    def _responder(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        if request.url.path == "/leads" and request.method == "GET":
            return httpx.Response(200, json={"leads": []})
        return httpx.Response(self.status_code, json=self.corpo)

    def fabrica(self, base_url: str, token: str, ravi_tenant_id: str) -> httpx.Client:
        return httpx.Client(
            base_url=base_url,
            transport=httpx.MockTransport(self._responder),
            headers={
                "authorization": f"Bearer {token}",
                "x-tenant-id": ravi_tenant_id,
                "content-type": "application/json",
            },
        )

    @property
    def ultimo_corpo(self) -> dict:
        return json.loads(self.chamadas[-1].content)


def _configurar(tenant_id, *, default_stage=None):
    with tenant_session(tenant_id) as session:
        ravi.store(
            session,
            tenant_id,
            base_url=BASE,
            token=TOKEN,
            ravi_tenant_id=RAVI_TENANT,
            default_stage=default_stage,
        )


def _prospect(
    tenant_id,
    *,
    score=None,
    email="alice@northernore.ca",
    phone=None,
    qualificacao=None,
):
    with tenant_session(tenant_id) as session:
        campanha = Campaign(tenant_id=tenant_id, name="Mining", slug=f"m-{uuid.uuid4().hex[:6]}")
        empresa = Company(tenant_id=tenant_id, name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=tenant_id,
            company_id=empresa.id,
            full_name="Alice Tremblay",
            email=email,
            phone=phone,
            title="CFO",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=tenant_id,
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="scored",
        )
        session.add(prospect)
        session.flush()
        if score is not None:
            session.add(
                Score(
                    tenant_id=tenant_id,
                    prospect_id=prospect.id,
                    value=score,
                    band="A",
                    rationale="Expansão operacional recente",
                )
            )
        if qualificacao is not None:
            session.add(
                Qualification(
                    tenant_id=tenant_id,
                    prospect_id=prospect.id,
                    outcome="qualified",
                    confidence=80,
                    rationale="Dor confirmada em duas unidades",
                    criteria_results=qualificacao,
                )
            )
        session.flush()
        return {"prospect_id": prospect.id, "contact_id": contato.id}


# ------------------------------------------------------------------ credencial
def test_token_nunca_volta_na_resposta(client, make_tenant, auth_headers, monkeypatch):
    """O token de agente do RAVI é credencial do cliente: não tem campo de
    leitura em nenhuma resposta desta API."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    falso = RaviFalso()
    monkeypatch.setattr(ravi, "_cliente", falso.fabrica)

    r = client.put(
        "/api/v1/settings/crm",
        json={"base_url": BASE, "token": TOKEN, "ravi_tenant_id": RAVI_TENANT},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True
    assert r.json()["token_hint"] == "…abcd"
    assert TOKEN not in r.text

    lido = client.get("/api/v1/settings/crm", headers=headers)
    assert TOKEN not in lido.text
    assert lido.json()["ravi_tenant_id"] == RAVI_TENANT


def test_url_sem_esquema_e_recusada(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.put(
        "/api/v1/settings/crm",
        json={"base_url": "api.ravi.exemplo.com", "token": TOKEN, "ravi_tenant_id": RAVI_TENANT},
        headers=headers,
    )
    assert r.status_code == 400
    assert "https://" in r.json()["error"]["message"]


def test_nao_salva_o_que_nao_conecta(client, make_tenant, auth_headers, monkeypatch):
    """Salvar sem testar deixaria a empresa achando que o CRM está ligado
    enquanto a fila acumula falha em silêncio."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    def recusa(base_url, token, ravi_tenant_id):
        return httpx.Client(
            base_url=base_url,
            transport=httpx.MockTransport(lambda _: httpx.Response(401, text="unauthorized")),
        )

    monkeypatch.setattr(ravi, "_cliente", recusa)
    r = client.put(
        "/api/v1/settings/crm",
        json={"base_url": BASE, "token": "errado", "ravi_tenant_id": RAVI_TENANT},
        headers=headers,
    )
    assert r.status_code == 400
    assert "token" in r.json()["error"]["message"].lower()
    assert client.get("/api/v1/settings/crm", headers=headers).json()["configured"] is False


def test_teste_de_conexao_nao_escreve_no_ravi(client, make_tenant, auth_headers, monkeypatch):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    falso = RaviFalso()
    monkeypatch.setattr(ravi, "_cliente", falso.fabrica)

    r = client.post(
        "/api/v1/settings/crm/test",
        json={"base_url": BASE, "token": TOKEN, "ravi_tenant_id": RAVI_TENANT},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert [c.method for c in falso.chamadas] == ["GET"]


def test_desligar_a_integracao(client, make_tenant, auth_headers, monkeypatch):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    monkeypatch.setattr(ravi, "_cliente", RaviFalso().fabrica)
    client.put(
        "/api/v1/settings/crm",
        json={"base_url": BASE, "token": TOKEN, "ravi_tenant_id": RAVI_TENANT},
        headers=headers,
    )
    r = client.delete("/api/v1/settings/crm", headers=headers)
    assert r.status_code == 200
    assert r.json()["configured"] is False


# ------------------------------------------------------------------- mapeamento
def test_payload_leva_o_que_o_ravi_espera(make_tenant, monkeypatch):
    """O Lead do RAVI é BANT, e é quase exatamente o que a qualificação produz."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(
        tenant_id,
        score=82,
        # A forma que o Qualification Agent grava de verdade: lista sob
        # "criteria", com o nome do critério em texto livre, como a campanha o
        # definiu.
        qualificacao={
            "criteria": [
                {
                    "criterion": "Orçamento aprovado para o ciclo",
                    "status": "met",
                    "evidence": "Verba aprovada para 2026",
                },
                {"criterion": "Autoridade de decisão", "status": "met", "evidence": None},
                {
                    "criterion": "Necessidade confirmada",
                    "status": "met",
                    "evidence": "Escala 24/7 sem controle",
                },
                {"criterion": "Prazo de implantação", "status": "unknown", "evidence": None},
            ],
            "next_step": "Reunião de 20 minutos",
        },
    )
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        resultado = ravi.push_prospect(session, tenant_id, prospect, client_factory=falso.fabrica)

    assert resultado["status"] == "created"
    assert resultado["lead_id"] == "lead_1"

    corpo = falso.ultimo_corpo
    assert corpo["email"] == "alice@northernore.ca"
    assert corpo["company"] == "Northern Ore"
    assert corpo["role"] == "CFO"
    assert corpo["score"] == 82
    assert corpo["source"] == "manual"
    assert "Verba aprovada" in corpo["budget"]
    # Critério atendido sem frase de evidência vira o status legível, não o
    # "met" cru do modelo.
    assert corpo["authority"] == "atendido"
    assert "Escala 24/7" in corpo["need"]
    # Critério não avaliado não vira campo: "desconhecido" como texto encheria a
    # tela do RAVI de ruído que parece informação.
    assert "timeline" not in corpo

    pedido = falso.chamadas[-1]
    assert pedido.headers["authorization"] == f"Bearer {TOKEN}"
    assert pedido.headers["x-tenant-id"] == RAVI_TENANT


def test_prospect_sem_pontuacao_nao_vai(make_tenant):
    """Lead sem nota nem pesquisa é linha que ninguém sabe de onde veio."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=None)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        resultado = ravi.push_prospect(session, tenant_id, prospect, client_factory=falso.fabrica)

    assert resultado["status"] == "skipped"
    # A razão diz o que falta **e** de onde vem: quem lê isto na tela precisa
    # saber qual é o próximo passo, não só que algo faltou.
    assert "nota" in resultado["reason"] and "pesquisa" in resultado["reason"]
    assert falso.chamadas == []


def test_prospect_sem_email_nem_telefone_nao_vai(make_tenant):
    """O upsert do RAVI é por email ou telefone: sem nenhum dos dois, cada
    envio criaria um lead novo — o oposto do que uma integração deve fazer."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=70, email=None, phone=None)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        resultado = ravi.push_prospect(session, tenant_id, prospect, client_factory=falso.fabrica)

    assert resultado["status"] == "skipped"
    assert falso.chamadas == []


def test_score_fora_da_faixa_entra_na_faixa(make_tenant):
    """O RAVI recusa score fora de 0..100 e calcula a temperatura a partir dele."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=140)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=falso.fabrica,
        )
    assert falso.ultimo_corpo["score"] == 100


def test_estagio_padrao_vai_quando_configurado(make_tenant):
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id, default_stage="prospeccao")
    dados = _prospect(tenant_id, score=50)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=falso.fabrica,
        )
    assert falso.ultimo_corpo["stage"] == "prospeccao"


# ------------------------------------------------------------------------ envio
def test_reenvio_sem_mudanca_nao_bate_no_ravi(make_tenant):
    """Reenviar é seguro — o RAVI faz upsert — mas gastar chamada à toa não é."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=60)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        primeiro = ravi.push_prospect(session, tenant_id, prospect, client_factory=falso.fabrica)
    assert primeiro["status"] == "created"
    assert len(falso.chamadas) == 1

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        segundo = ravi.push_prospect(session, tenant_id, prospect, client_factory=falso.fabrica)
    assert segundo["status"] == "unchanged"
    assert len(falso.chamadas) == 1

    # `force` existe para o caso de alguém ter mexido no lead do lado do RAVI.
    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        terceiro = ravi.push_prospect(
            session, tenant_id, prospect, force=True, client_factory=falso.fabrica
        )
    assert terceiro["status"] == "created"
    assert len(falso.chamadas) == 2


def test_qualificacao_nova_faz_o_lead_subir_de_novo(make_tenant):
    """O CRM tem que refletir o que o agente descobriu depois do primeiro envio."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=60)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=falso.fabrica,
        )

    with tenant_session(tenant_id) as session:
        session.add(
            Qualification(
                tenant_id=tenant_id,
                prospect_id=dados["prospect_id"],
                outcome="qualified",
                confidence=90,
                rationale="Confirmou a dor na reunião",
                criteria_results={
                    "criteria": [
                        {
                            "criterion": "Necessidade",
                            "status": "met",
                            "evidence": "confirmada na reunião",
                        }
                    ]
                },
            )
        )

    with tenant_session(tenant_id) as session:
        resultado = ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=falso.fabrica,
        )
    assert resultado["status"] == "created"
    assert "confirmada na reunião" in falso.ultimo_corpo["need"]
    assert "Confirmou a dor" in falso.ultimo_corpo["intent"]


def test_ravi_fora_do_ar_vira_erro_legivel(make_tenant):
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=60)

    def cai(base_url, token, ravi_tenant_id):
        def _erro(_request):
            raise httpx.ConnectError("connection refused")

        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(_erro))

    with (
        tenant_session(tenant_id) as session,
        pytest.raises(ravi.RaviUnavailable, match="alcançar"),
    ):
        ravi.push_prospect(
            session, tenant_id, session.get(Prospect, dados["prospect_id"]), client_factory=cai
        )


def test_recusa_do_ravi_carrega_o_corpo_da_resposta(make_tenant):
    """Se o contrato do RAVI mudar, é isso que vai dizer o que mudou — em vez de
    um 400 anônimo."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=60)
    falso = RaviFalso(status_code=400, corpo={"message": "score is required"})

    with (
        tenant_session(tenant_id) as session,
        pytest.raises(ravi.RaviUnavailable, match="score is required"),
    ):
        ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=falso.fabrica,
        )


def test_sem_ravi_configurado_o_worker_nao_reclama(make_tenant):
    """A integração é opcional: quem não usa o RAVI não deveria ver job
    falhando a cada ciclo."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _prospect(tenant_id, score=60)

    with tenant_session(tenant_id) as session:
        resultado = ravi.sync_pending(session, tenant_id)
    assert resultado["sent"] == 0
    assert "não configurado" in resultado["reason"]


def test_sync_manda_o_que_mudou(make_tenant):
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    _prospect(tenant_id, score=70, email="a@x.com")
    _prospect(tenant_id, score=None, email="b@x.com")  # sem nota: fica de fora
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        primeiro = ravi.sync_pending(session, tenant_id, client_factory=falso.fabrica)
    assert primeiro["sent"] == 1
    assert primeiro["skipped"] == 1

    with tenant_session(tenant_id) as session:
        segundo = ravi.sync_pending(session, tenant_id, client_factory=falso.fabrica)
    assert segundo["sent"] == 0
    assert segundo["unchanged"] == 1


def test_falha_geral_sobe_para_a_fila_tentar_de_novo(make_tenant):
    """Nada subiu e houve falha: é configuração ou disponibilidade, e a fila
    deve tentar de novo com backoff em vez de dar o ciclo por bem-sucedido."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    _prospect(tenant_id, score=70)
    falso = RaviFalso(status_code=500, corpo={"message": "boom"})

    with tenant_session(tenant_id) as session, pytest.raises(ravi.RaviUnavailable):
        ravi.sync_pending(session, tenant_id, client_factory=falso.fabrica)


# ------------------------------------------------------------------ isolamento
def test_credencial_do_ravi_nao_atravessa_empresas(client, make_tenant, auth_headers, monkeypatch):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    headers_b = auth_headers(b["email"], b["password"])
    monkeypatch.setattr(ravi, "_cliente", RaviFalso().fabrica)

    client.put(
        "/api/v1/settings/crm",
        json={"base_url": BASE, "token": TOKEN, "ravi_tenant_id": RAVI_TENANT},
        headers=headers_a,
    )
    assert client.get("/api/v1/settings/crm", headers=headers_b).json()["configured"] is False


def test_sync_de_uma_empresa_nao_manda_lead_da_outra(make_tenant):
    a, b = make_tenant(), make_tenant()
    _configurar(a["tenant_id"])
    _configurar(b["tenant_id"])
    _prospect(a["tenant_id"], score=70, email="da-a@x.com")
    _prospect(b["tenant_id"], score=70, email="da-b@x.com")
    falso = RaviFalso()

    with tenant_session(a["tenant_id"]) as session:
        ravi.sync_pending(session, a["tenant_id"], client_factory=falso.fabrica)

    enviados = [json.loads(c.content)["email"] for c in falso.chamadas if c.method == "POST"]
    assert enviados == ["da-a@x.com"]


def test_endpoint_manual_de_envio(client, make_tenant, auth_headers, monkeypatch):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    falso = RaviFalso()
    monkeypatch.setattr(ravi, "_cliente", falso.fabrica)
    _configurar(t["tenant_id"])
    dados = _prospect(t["tenant_id"], score=75)

    r = client.post(f"/api/v1/prospects/{dados['prospect_id']}/sync-crm", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert r.json()["lead_id"] == "lead_1"

    # A auditoria registra o envio, e sem o token no meio.
    trilha = client.get("/api/v1/tenants/me/audit", headers=headers).json()
    assert any(e["action"] == "crm.lead_pushed" for e in trilha)


def test_prospect_de_outra_empresa_nao_e_enviado(client, make_tenant, auth_headers, monkeypatch):
    a, b = make_tenant(), make_tenant()
    headers_b = auth_headers(b["email"], b["password"])
    monkeypatch.setattr(ravi, "_cliente", RaviFalso().fabrica)
    _configurar(b["tenant_id"])
    dados = _prospect(a["tenant_id"], score=75)

    r = client.post(f"/api/v1/prospects/{dados['prospect_id']}/sync-crm", headers=headers_b)
    assert r.status_code == 404


def test_vinculo_com_o_ravi_fica_no_contato(make_tenant):
    """O RAVI deduplica por email/telefone — ou seja, por pessoa, não por
    participação em campanha."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    dados = _prospect(tenant_id, score=60)

    with tenant_session(tenant_id) as session:
        ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=RaviFalso().fabrica,
        )

    with tenant_session(tenant_id) as session:
        contato = session.execute(select(Contact)).scalar_one()
        marca = contato.attributes["ravi"]
        assert marca["lead_id"] == "lead_1"
        assert marca["created"] is True
        assert marca["synced_at"]


def test_mapeamento_usa_a_forma_que_o_agente_produz(make_tenant):
    """Blindagem contra a divergência que já aconteceu uma vez.

    O mapeamento para o RAVI foi escrito supondo um dicionário chato e o agente
    grava uma lista sob "criteria" — o teste passava porque o fixture inventava
    a forma. Aqui os critérios são construídos pelo modelo do próprio agente,
    então mudar o contrato dele quebra este teste em vez de quebrar a
    integração em produção.
    """
    from app.orchestrator.executors.qualification import CriterionResult

    t = make_tenant()
    tenant_id = t["tenant_id"]
    _configurar(tenant_id)
    criterios = [
        CriterionResult(
            criterion="Orçamento disponível", status="met", evidence="Verba de R$ 400 mil"
        ),
        CriterionResult(criterion="Prazo para decidir", status="not_met", evidence=None),
    ]
    dados = _prospect(
        tenant_id,
        score=77,
        qualificacao={"criteria": [c.model_dump() for c in criterios]},
    )
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        ravi.push_prospect(
            session,
            tenant_id,
            session.get(Prospect, dados["prospect_id"]),
            client_factory=falso.fabrica,
        )

    corpo = falso.ultimo_corpo
    assert "R$ 400 mil" in corpo["budget"]
    assert corpo["timeline"] == "não atendido"


def test_sem_ravi_configurado_a_recusa_vem_antes_da_nota(make_tenant):
    """Primeiro o que o admin tem de resolver, depois o que falta no lead.

    Sem esta ordem, a empresa que nunca conectou o RAVI era mandada buscar a
    nota do lead para só então descobrir que não havia integração nenhuma —
    trabalho dobrado, na ordem errada.
    """
    t = make_tenant()
    tenant_id = t["tenant_id"]
    dados = _prospect(tenant_id, score=None)
    falso = RaviFalso()

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, dados["prospect_id"])
        with pytest.raises(NotFound, match="RAVI"):
            ravi.push_prospect(session, tenant_id, prospect, client_factory=falso.fabrica)
    assert falso.chamadas == []
