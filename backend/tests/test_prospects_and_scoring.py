"""Funil: entrada de prospects, pontuação e os números da tela inicial."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.platform import Tenant
from app.db.models.sales import Company, Contact, Prospect, Score
from app.db.session import tenant_session
from app.services.scoring import band_for, score_research

# ------------------------------------------------------------------ pontuação


def test_aderencia_forte_com_sinais_vira_banda_a():
    valor, detalhe = score_research(
        {
            "icp_fit": "forte",
            "signals": ["contratação", "expansão"],
            "unknowns": [],
            "findings": [{"source_url": "https://exemplo.com"}],
        }
    )
    assert valor == 90.0 and band_for(valor) == "A"
    assert detalhe["base"] == 80.0 and detalhe["signal_bonus"] == 8.0


def test_muita_incerteza_derruba_a_nota():
    forte_e_incerto, _ = score_research(
        {"icp_fit": "forte", "unknowns": ["a", "b", "c", "d", "e", "f"]}
    )
    forte_e_certo, _ = score_research({"icp_fit": "forte"})
    assert forte_e_incerto < forte_e_certo
    # A penalidade tem teto: incerteza não zera uma conta que bate com o ICP.
    assert forte_e_incerto == 65.0


def test_fit_desconhecido_nao_vira_nota_alta():
    valor, _ = score_research({"icp_fit": "desconhecido", "signals": ["x", "y", "z", "w"]})
    assert valor <= 25.0 and band_for(valor) == "D"


def test_calculo_fica_aberto_para_auditoria():
    _, detalhe = score_research({"icp_fit": "parcial", "signals": ["rodada"], "unknowns": ["a"]})
    assert detalhe == {
        "icp_fit": "parcial",
        "base": 55.0,
        "signals": 1,
        "signal_bonus": 4.0,
        "sourced_findings": 0,
        "evidence_bonus": 0,
        "unknowns": 1,
        "unknown_penalty": 3.0,
    }


# ------------------------------------------------------------------ import


def _register(client, name: str, email: str) -> dict:
    r = client.post(
        "/api/v1/auth/register",
        json={"tenant_name": name, "email": email, "password": "senha-forte-123"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "_tenant": r.json()["tenant_id"]}


def _headers(reg: dict) -> dict:
    return {"Authorization": reg["Authorization"]}


def _campanha(client, reg: dict, slug: str = "mining-canada") -> str:
    r = client.post(
        "/api/v1/campaigns", headers=_headers(reg), json={"name": "Mining Canada", "slug": slug}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


LISTA = [
    {
        "company_name": "Northern Ore",
        "company_domain": "northernore.ca",
        "country": "CA",
        "full_name": "Alice Tremblay",
        "email": "alice@northernore.ca",
        "title": "CFO",
    },
    {
        "company_name": "Northern Ore",
        "company_domain": "northernore.ca",
        "full_name": "Bruno Lemay",
        "email": "bruno@northernore.ca",
        "title": "COO",
    },
]


def test_import_cria_empresa_uma_vez_e_prospects_por_pessoa(client):
    reg = _register(client, "Apy Mine", "apy@example.com")
    campanha = _campanha(client, reg)

    r = client.post(
        "/api/v1/prospects/import",
        headers=_headers(reg),
        json={"campaign_id": campanha, "items": LISTA, "source": "csv"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 2 and r.json()["duplicates"] == 0

    # Duas pessoas, uma empresa só: o domínio deduplicou.
    empresas = client.get("/api/v1/companies", headers=_headers(reg)).json()
    assert len(empresas) == 1 and empresas[0]["name"] == "Northern Ore"


def test_reimportar_a_mesma_lista_nao_duplica(client):
    reg = _register(client, "Apy Mine", "apy2@example.com")
    campanha = _campanha(client, reg)
    corpo = {"campaign_id": campanha, "items": LISTA}

    client.post("/api/v1/prospects/import", headers=_headers(reg), json=corpo)
    segunda = client.post("/api/v1/prospects/import", headers=_headers(reg), json=corpo)

    assert segunda.json() == {"imported": 0, "duplicates": 2, "prospect_ids": []}
    assert len(client.get("/api/v1/prospects", headers=_headers(reg)).json()) == 2


def test_import_respeita_a_cota_mensal_do_plano(client, make_tenant, auth_headers):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"prospects_per_month": 1}

    headers = auth_headers(t["email"], t["password"])
    campanha = client.post(
        "/api/v1/campaigns", headers=headers, json={"name": "Cota", "slug": "cota"}
    ).json()["id"]

    r = client.post(
        "/api/v1/prospects/import",
        headers=headers,
        json={"campaign_id": campanha, "items": LISTA},
    )
    assert r.status_code == 402
    assert r.json()["error"]["details"]["limit"] == 1


def test_prospects_de_outro_tenant_nao_aparecem(client):
    a = _register(client, "Empresa A", "pa@example.com")
    b = _register(client, "Empresa B", "pb@example.com")
    campanha = _campanha(client, a)
    client.post(
        "/api/v1/prospects/import",
        headers=_headers(a),
        json={"campaign_id": campanha, "items": LISTA},
    )

    assert len(client.get("/api/v1/prospects", headers=_headers(a)).json()) == 2
    assert client.get("/api/v1/prospects", headers=_headers(b)).json() == []
    assert client.get("/api/v1/prospects/funnel", headers=_headers(b)).json()["prospects"] == 0


# ------------------------------------------------------------------ funil


@pytest.fixture
def tenant_com_prospects(client):
    reg = _register(client, "Apy Mine", "funil@example.com")
    campanha = _campanha(client, reg, slug="funil")
    client.post(
        "/api/v1/prospects/import",
        headers=_headers(reg),
        json={"campaign_id": campanha, "items": LISTA},
    )
    return reg, campanha


def test_pesquisa_pontua_todos_os_prospects_da_conta(tenant_com_prospects, client):
    """Uma pesquisa por conta, nota para cada pessoa dela."""
    from app.db.models.sales import Research
    from app.services.scoring import apply_to_prospects

    reg, campanha = tenant_com_prospects
    tenant_id = reg["_tenant"]

    with tenant_session(tenant_id) as session:
        empresa = session.execute(select(Company)).scalars().one()
        pesquisa = Research(
            tenant_id=tenant_id,
            entity_type="company",
            entity_id=empresa.id,
            campaign_id=campanha,
            summary="…",
            findings={
                "icp_fit": "forte",
                "icp_rationale": "Porte e geografia batem",
                "signals": ["contratação"],
                "unknowns": [],
                "findings": [{"claim": "x", "evidence": "y", "source_url": "https://e.ca"}],
            },
        )
        session.add(pesquisa)
        session.flush()
        notas = apply_to_prospects(session, tenant_id=empresa.tenant_id, research=pesquisa)

    assert len(notas) == 2  # duas pessoas na mesma conta
    with tenant_session(tenant_id) as session:
        assert {p.status for p in session.execute(select(Prospect)).scalars()} == {"scored"}
        assert {float(s.value) for s in session.execute(select(Score)).scalars()} == {86.0}

    funil = client.get("/api/v1/prospects/funnel", headers=_headers(reg)).json()
    assert funil["prospects"] == 2 and funil["scored"] == 2 and funil["researched"] == 2
    assert funil["by_band"] == {"A": 2}


def test_prospect_ja_contatado_nao_volta_no_funil(tenant_com_prospects, client):
    """Pesquisar a conta de novo não pode empurrar ninguém para trás."""
    from app.db.models.sales import Research
    from app.services.scoring import apply_to_prospects

    reg, campanha = tenant_com_prospects
    tenant_id = reg["_tenant"]

    with tenant_session(tenant_id) as session:
        prospects = list(session.execute(select(Prospect)).scalars())
        prospects[0].status = "engaged"
        empresa_id = prospects[0].company_id
        pesquisa = Research(
            tenant_id=tenant_id,
            entity_type="company",
            entity_id=empresa_id,
            campaign_id=campanha,
            findings={"icp_fit": "forte"},
        )
        session.add(pesquisa)
        session.flush()
        apply_to_prospects(session, tenant_id=tenant_id, research=pesquisa)

    with tenant_session(tenant_id) as session:
        status = sorted(p.status for p in session.execute(select(Prospect)).scalars())
    assert status == ["engaged", "scored"]

    funil = client.get("/api/v1/prospects/funnel", headers=_headers(reg)).json()
    # Quem engajou também conta como pesquisado e contatado: o funil é
    # cumulativo, senão os números sumiriam conforme as pessoas avançam.
    assert funil["engaged"] == 1 and funil["contacted"] == 1 and funil["researched"] == 2


def test_contato_sem_email_ainda_entra(client):
    reg = _register(client, "Apy Mine", "semmail@example.com")
    campanha = _campanha(client, reg, slug="sem-email")
    r = client.post(
        "/api/v1/prospects/import",
        headers=_headers(reg),
        json={
            "campaign_id": campanha,
            "items": [{"company_name": "Sem Email SA", "full_name": "Carla Dias"}],
        },
    )
    assert r.status_code == 201 and r.json()["imported"] == 1
    with tenant_session(reg["_tenant"]) as session:
        contato = session.execute(select(Contact)).scalars().one()
    assert contato.email is None
