"""Configuração por empresa: chave da Anthropic e conta de email.

O que esses testes protegem não é a tela, é a promessa: segredo de cliente
entra, é testado, fica cifrado e nunca mais sai.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.ai import Integration
from app.db.session import tenant_session
from app.services import ai_credentials, email_accounts

CHAVE = "sk-ant-api03-chave-de-teste-1234"


@pytest.fixture
def chave_valida(monkeypatch):
    """A validação fala com a Anthropic; aqui ela é simulada."""
    import app.api.v1.settings as rotas

    monkeypatch.setattr(rotas, "validate_key", lambda chave: (True, None))
    return CHAVE


@pytest.fixture
def chave_recusada(monkeypatch):
    import app.api.v1.settings as rotas

    monkeypatch.setattr(
        rotas, "validate_key", lambda chave: (False, "A Anthropic recusou esta chave.")
    )


# ------------------------------------------------------------------ chave de IA


def test_empresa_comeca_sem_chave(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    corpo = client.get("/api/v1/settings/ai", headers=headers).json()
    assert corpo == {
        "configured": False,
        "key_hint": None,
        "status": "missing",
        "using_platform_key": False,
        "updated_at": None,
    }


def test_chave_salva_volta_mascarada_e_nunca_em_claro(
    client, make_tenant, auth_headers, chave_valida
):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.put("/api/v1/settings/ai", headers=headers, json={"api_key": CHAVE})
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True
    assert r.json()["key_hint"] == "…1234"
    assert CHAVE not in r.text

    # Nem na leitura, nem na auditoria.
    assert CHAVE not in client.get("/api/v1/settings/ai", headers=headers).text
    assert CHAVE not in client.get("/api/v1/tenants/me/audit", headers=headers).text

    # No banco, cifrada.
    with tenant_session(t["tenant_id"]) as session:
        integracao = session.execute(select(Integration)).scalars().one()
        assert CHAVE not in integracao.credentials_encrypted
        assert ai_credentials.resolve_api_key(session, t["tenant_id"]) == CHAVE


def test_chave_recusada_nao_e_salva(client, make_tenant, auth_headers, chave_recusada):
    """Testar antes de salvar evita a descoberta tardia do erro."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.put("/api/v1/settings/ai", headers=headers, json={"api_key": CHAVE})
    assert r.status_code == 400
    assert "recusou" in r.json()["error"]["message"]
    assert client.get("/api/v1/settings/ai", headers=headers).json()["configured"] is False


def test_botao_testar_nao_salva(client, make_tenant, auth_headers, chave_valida):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post("/api/v1/settings/ai/test", headers=headers, json={"api_key": CHAVE})
    assert r.json()["ok"] is True
    assert client.get("/api/v1/settings/ai", headers=headers).json()["configured"] is False


def test_cada_empresa_tem_a_propria_chave(client, make_tenant, auth_headers, chave_valida):
    a = make_tenant()
    b = make_tenant()
    client.put(
        "/api/v1/settings/ai",
        headers=auth_headers(a["email"], a["password"]),
        json={"api_key": CHAVE},
    )

    headers_b = auth_headers(b["email"], b["password"])
    assert client.get("/api/v1/settings/ai", headers=headers_b).json()["configured"] is False
    with tenant_session(b["tenant_id"]) as session:
        with pytest.raises(ai_credentials.AIKeyMissing):
            ai_credentials.resolve_api_key(session, b["tenant_id"])


def test_sem_chave_o_agente_diz_o_que_fazer(make_tenant):
    """A falha precisa ser acionável, não um 500 genérico."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        with pytest.raises(ai_credentials.AIKeyMissing) as exc:
            ai_credentials.resolve_api_key(session, t["tenant_id"])
    assert "Configurações" in exc.value.message


def test_operator_nao_configura_chave(client, make_tenant, auth_headers, chave_valida):
    from app.core.security import hash_password
    from app.db.models.platform import Membership, User
    from app.db.session import unscoped_session

    t = make_tenant()
    with unscoped_session(reason="test:add-operator") as session:
        user = User(
            email="op@example.com",
            password_hash=hash_password("senha-forte-123"),
            full_name="Operador",
        )
        session.add(user)
        session.flush()
        session.add(Membership(tenant_id=t["tenant_id"], user_id=user.id, role="operator"))

    headers = auth_headers("op@example.com", "senha-forte-123")
    r = client.put("/api/v1/settings/ai", headers=headers, json={"api_key": CHAVE})
    assert r.status_code == 403


# ------------------------------------------------------------------ email


def test_presets_trazem_host_porta_e_instrucao(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    presets = {
        p["provider"]: p
        for p in client.get("/api/v1/settings/email/presets", headers=headers).json()
    }

    assert presets["gmail"]["host"] == "smtp.gmail.com"
    assert presets["gmail"]["port"] == 587
    # A instrução sobre senha de app é o que salva o usuário leigo.
    assert "app" in presets["gmail"]["help"].lower()
    assert presets["smtp"]["host"] == ""


def test_conta_de_email_salva_testada_e_sem_senha_na_resposta(
    client, make_tenant, auth_headers, monkeypatch
):
    import app.api.v1.settings as rotas

    monkeypatch.setattr(rotas.email_accounts, "test_connection", lambda **kw: (True, "Conectado."))
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.put(
        "/api/v1/settings/email",
        headers=headers,
        json={
            "provider": "gmail",
            "from_email": "vendas@apymine.com",
            "from_name": "Vendas Apy Mine",
            "password": "senha-de-app-secreta",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["host"] == "smtp.gmail.com" and r.json()["port"] == 587
    assert "senha-de-app-secreta" not in r.text
    assert "senha-de-app-secreta" not in client.get("/api/v1/settings/email", headers=headers).text

    with tenant_session(t["tenant_id"]) as session:
        credenciais = email_accounts.credentials(session, t["tenant_id"])
    # Usuário assume o remetente quando não informado — um campo a menos para errar.
    assert credenciais["username"] == "vendas@apymine.com"
    assert credenciais["password"] == "senha-de-app-secreta"


def test_conta_que_nao_conecta_nao_e_salva(client, make_tenant, auth_headers, monkeypatch):
    import app.api.v1.settings as rotas

    monkeypatch.setattr(
        rotas.email_accounts,
        "test_connection",
        lambda **kw: (False, "O servidor recusou usuário ou senha."),
    )
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.put(
        "/api/v1/settings/email",
        headers=headers,
        json={"provider": "gmail", "from_email": "vendas@apymine.com", "password": "errada"},
    )
    assert r.status_code == 400
    assert "recusou" in r.json()["error"]["message"]
    assert client.get("/api/v1/settings/email", headers=headers).json()["configured"] is False


def test_servidor_proprio_exige_endereco(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.put(
        "/api/v1/settings/email",
        headers=headers,
        json={"provider": "smtp", "from_email": "vendas@apymine.com", "password": "x"},
    )
    assert r.status_code == 400
    assert "servidor" in r.json()["error"]["message"].lower()


# ------------------------------------------------------------------ volume


def test_politica_de_volume_nasce_conservadora(client, make_tenant, auth_headers):
    """Padrão seguro: domínio novo com volume alto vira spam."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    politica = client.get("/api/v1/settings/sending", headers=headers).json()

    assert politica["warmup_enabled"] is True
    assert politica["warmup_start"] == 10
    assert politica["daily_limit"] == 30
    assert politica["business_hours_only"] is True


def test_politica_de_volume_e_por_empresa(client, make_tenant, auth_headers):
    a = make_tenant()
    b = make_tenant()
    client.put(
        "/api/v1/settings/sending",
        headers=auth_headers(a["email"], a["password"]),
        json={
            "daily_limit": 200,
            "warmup_enabled": False,
            "warmup_start": 50,
            "warmup_daily_increment": 10,
            "business_hours_only": False,
            "timezone": "Europe/Lisbon",
        },
    )

    de_a = client.get(
        "/api/v1/settings/sending", headers=auth_headers(a["email"], a["password"])
    ).json()
    de_b = client.get(
        "/api/v1/settings/sending", headers=auth_headers(b["email"], b["password"])
    ).json()
    assert de_a["daily_limit"] == 200 and de_a["warmup_enabled"] is False
    assert de_b["daily_limit"] == 30 and de_b["warmup_enabled"] is True
