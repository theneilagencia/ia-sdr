"""Isolamento visto de fora: pela API, como o cliente usa."""

from __future__ import annotations


def _register(client, name: str, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "tenant_name": name,
            "email": email,
            "password": "senha-forte-123",
            "full_name": "Fundador",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(token: dict) -> dict:
    return {"Authorization": f"Bearer {token['access_token']}"}


def test_registro_cria_tenant_owner_e_company_brain(client):
    token = _register(client, "Apy Mine", "owner@apymine.com")
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert me["role"] == "owner"
    assert me["tenant_id"] == token["tenant_id"]
    assert "campaign:write" in me["permissions"]

    brain = client.get("/api/v1/company-brain", headers=_headers(token))
    assert brain.status_code == 200
    assert brain.json()["legal_name"] == "Apy Mine"


def test_campanha_de_um_tenant_nao_aparece_no_outro(client):
    a = _register(client, "Empresa A", "a@example.com")
    b = _register(client, "Empresa B", "b@example.com")

    created = client.post(
        "/api/v1/campaigns",
        headers=_headers(a),
        json={"name": "Mining Canada", "slug": "mining-canada", "target_geography": ["CA"]},
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]

    assert client.get("/api/v1/campaigns", headers=_headers(a)).json()[0]["id"] == campaign_id
    assert client.get("/api/v1/campaigns", headers=_headers(b)).json() == []

    # Buscar pelo id de outro tenant é 404, não 403: nem a existência vaza.
    leak = client.get(f"/api/v1/campaigns/{campaign_id}", headers=_headers(b))
    assert leak.status_code == 404


def test_requisicao_sem_token_e_recusada(client):
    assert client.get("/api/v1/campaigns").status_code == 401


def test_token_de_outro_tenant_nao_serve(client, make_tenant, auth_headers):
    """Trocar o tenant do token não é possível: o vínculo é revalidado no banco."""
    import uuid

    from app.core.security import create_access_token

    t = make_tenant()
    forjado = create_access_token(
        user_id=t["user_id"], tenant_id=uuid.uuid4(), role="owner"
    )
    response = client.get(
        "/api/v1/campaigns", headers={"Authorization": f"Bearer {forjado}"}
    )
    assert response.status_code == 403


def test_credencial_de_integracao_nunca_volta_na_resposta(client):
    a = _register(client, "Empresa Segredo", "seg@example.com")
    created = client.post(
        "/api/v1/integrations",
        headers=_headers(a),
        json={
            "provider": "smtp",
            "account_ref": "vendas@empresa.com",
            "credentials": {"username": "vendas", "password": "super-secreta"},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "super-secreta" not in created.text
    assert "credentials" not in body and "credentials_encrypted" not in body

    listed = client.get("/api/v1/integrations", headers=_headers(a))
    assert "super-secreta" not in listed.text


def test_conta_alvo_e_pesquisa_ficam_no_tenant(client):
    a = _register(client, "Empresa Alfa", "alfa@example.com")
    b = _register(client, "Empresa Beta", "beta@example.com")

    criada = client.post(
        "/api/v1/companies",
        headers=_headers(a),
        json={"name": "Northern Ore", "domain": "northernore.ca", "country": "CA"},
    )
    assert criada.status_code == 201, criada.text
    company_id = criada.json()["id"]

    assert client.get("/api/v1/companies", headers=_headers(a)).json()[0]["id"] == company_id
    assert client.get("/api/v1/companies", headers=_headers(b)).json() == []
    assert (
        client.get(f"/api/v1/companies/{company_id}", headers=_headers(b)).status_code == 404
    )
    # Sem pesquisa ainda, mas a rota existe e respeita a fronteira.
    assert client.get(f"/api/v1/companies/{company_id}/research", headers=_headers(a)).json() == []

    # Corrigir o que veio errado da planilha — domínio trocado é o caso comum, e
    # o agente pesquisaria a empresa errada com ele.
    corrigida = client.patch(
        f"/api/v1/companies/{company_id}",
        headers=_headers(a),
        json={"domain": "northern-ore.ca", "employee_count": 450},
    )
    assert corrigida.status_code == 200, corrigida.text
    assert corrigida.json()["domain"] == "northern-ore.ca"
    assert corrigida.json()["employee_count"] == 450
    # O que não veio no PATCH continua lá.
    assert corrigida.json()["name"] == "Northern Ore"

    # E a conta do vizinho não é editável nem por id.
    alheia = client.patch(
        f"/api/v1/companies/{company_id}",
        headers=_headers(b),
        json={"name": "Renomeada por fora"},
    )
    assert alheia.status_code == 404


def test_auditoria_registra_quem_fez_o_que(client):
    a = _register(client, "Empresa Auditada", "audit@example.com")
    client.post(
        "/api/v1/campaigns",
        headers=_headers(a),
        json={"name": "Campanha X", "slug": "campanha-x"},
    )
    logs = client.get("/api/v1/tenants/me/audit", headers=_headers(a)).json()
    acoes = {entry["action"] for entry in logs}
    assert {"tenant.created", "campaign.created"} <= acoes
