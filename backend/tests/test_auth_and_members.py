"""Troca de senha e gestão de membros.

O provisionamento de uma empresa nova imprime uma senha gerada e pede para
trocar no primeiro acesso: sem troca de senha, a senha do owner é para sempre
conhecida por quem provisionou.

Quem entra depois nunca tem senha escolhida por outra pessoa — os testes usam a
fixture `membro`, que passa pelo convite e pelo aceite, o mesmo caminho da tela.
Aqui o que se verifica é o depois: mudar papel, suspender, remover, e as travas
que impedem uma empresa de ficar sem ninguém que possa administrar.
"""

from __future__ import annotations

import time

SENHA_NOVA = "senha-nova-de-verdade-123"


def _login(client, email, senha):
    return client.post("/api/v1/auth/login", json={"email": email, "password": senha})


# ------------------------------------------------------------- troca de senha
def test_troca_de_senha_troca_a_senha(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": t["password"], "new_password": SENHA_NOVA},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]

    assert _login(client, t["email"], SENHA_NOVA).status_code == 200
    assert _login(client, t["email"], t["password"]).status_code == 401


def test_senha_atual_errada_nao_troca(client, make_tenant, auth_headers):
    """Token válido não é senha: token roubado não deve virar conta roubada."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "chute-errado-mas-longo", "new_password": SENHA_NOVA},
        headers=headers,
    )
    assert r.status_code == 401
    assert _login(client, t["email"], t["password"]).status_code == 200


def test_nova_senha_igual_a_atual_e_recusada(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": t["password"], "new_password": t["password"]},
        headers=headers,
    )
    assert r.status_code == 409


def test_troca_de_senha_encerra_as_sessoes_abertas(client, make_tenant, auth_headers):
    """O ponto todo: sem isto, o token que já estava na mão de quem invadiu
    continuaria valendo até expirar — doze horas, por padrão."""
    t = make_tenant()
    antigo = auth_headers(t["email"], t["password"])
    assert client.get("/api/v1/tenants/me", headers=antigo).status_code == 200

    # `iat` tem resolução de segundo: sem esperar, o token anterior cairia na
    # janela de um segundo em que ele ainda é considerado posterior à troca.
    time.sleep(1.1)
    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": t["password"], "new_password": SENHA_NOVA},
        headers=antigo,
    )
    assert r.status_code == 200

    recusado = client.get("/api/v1/tenants/me", headers=antigo)
    assert recusado.status_code == 401
    assert "senha" in recusado.json()["error"]["message"].lower()

    # O token devolvido pela própria troca continua servindo.
    novo = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/v1/tenants/me", headers=novo).status_code == 200


def test_senha_curta_nao_passa_da_validacao(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": t["password"], "new_password": "curta"},
        headers=headers,
    )
    assert r.status_code == 422


# ----------------------------------------------------------------- membros
def test_muda_papel_do_membro(client, make_tenant, auth_headers, membro):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    pessoa = membro(t)
    user_id, email, senha = pessoa["user_id"], pessoa["email"], pessoa["password"]

    r = client.patch(
        f"/api/v1/tenants/me/members/{user_id}", json={"role": "admin"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"

    # E o papel novo vale na borda, não só na resposta.
    do_membro = auth_headers(email, senha)
    assert client.get("/api/v1/tenants/me/members", headers=do_membro).status_code == 200


def test_desativar_membro_tira_o_acesso(client, make_tenant, auth_headers, membro):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    pessoa = membro(t)
    user_id, email, senha = pessoa["user_id"], pessoa["email"], pessoa["password"]
    do_membro = auth_headers(email, senha)
    assert client.get("/api/v1/prospects/funnel", headers=do_membro).status_code == 200

    r = client.patch(
        f"/api/v1/tenants/me/members/{user_id}", json={"is_active": False}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert client.get("/api/v1/prospects/funnel", headers=do_membro).status_code == 403


def test_remover_membro_tira_o_vinculo_e_nao_a_identidade(
    client, make_tenant, auth_headers, membro
):
    """`users` é global: a mesma pessoa pode trabalhar em outras empresas."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    pessoa = membro(t)
    user_id, email, senha = pessoa["user_id"], pessoa["email"], pessoa["password"]

    assert (
        client.delete(f"/api/v1/tenants/me/members/{user_id}", headers=headers).status_code == 204
    )
    assert user_id not in [
        m["user_id"] for m in client.get("/api/v1/tenants/me/members", headers=headers).json()
    ]

    # A identidade sobrevive: o login ainda autentica, só não tem tenant aqui.
    assert _login(client, email, senha).status_code in (200, 403)


def test_nao_se_rebaixa_nem_se_desativa(client, make_tenant, auth_headers):
    """Uma empresa sem ninguém que possa administrar é chamado de suporte."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.patch(
        f"/api/v1/tenants/me/members/{t['user_id']}", json={"role": "viewer"}, headers=headers
    )
    assert r.status_code == 409
    assert (
        client.delete(f"/api/v1/tenants/me/members/{t['user_id']}", headers=headers).status_code
        == 409
    )


def test_ultimo_owner_nao_e_rebaixado(client, make_tenant, auth_headers, membro):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    # Um admin para fazer o pedido, e o owner original como alvo.
    pessoa = membro(t, role="admin")
    admin_id = pessoa["user_id"]
    do_admin = auth_headers(pessoa["email"], pessoa["password"])

    r = client.patch(
        f"/api/v1/tenants/me/members/{t['user_id']}", json={"role": "admin"}, headers=do_admin
    )
    assert r.status_code == 409
    assert "owner" in r.json()["error"]["message"].lower()

    # Com dois owners, a mesma operação passa.
    assert (
        client.patch(
            f"/api/v1/tenants/me/members/{admin_id}", json={"role": "owner"}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/tenants/me/members/{t['user_id']}", json={"role": "admin"}, headers=do_admin
        ).status_code
        == 200
    )


def test_operator_nao_mexe_em_membro(client, make_tenant, auth_headers, membro):
    t = make_tenant()
    pessoa = membro(t)
    user_id = pessoa["user_id"]
    do_operator = auth_headers(pessoa["email"], pessoa["password"])

    assert (
        client.patch(
            f"/api/v1/tenants/me/members/{user_id}", json={"role": "owner"}, headers=do_operator
        ).status_code
        == 403
    )


def test_membro_de_outra_empresa_nao_e_encontrado(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])

    r = client.patch(
        f"/api/v1/tenants/me/members/{b['user_id']}", json={"role": "viewer"}, headers=headers_a
    )
    assert r.status_code == 404
