"""Papéis: quem pode o quê."""

from __future__ import annotations

import pytest

from app.rbac.roles import Permission, Role, has_permission, permissions_for


@pytest.mark.parametrize(
    ("role", "permission", "esperado"),
    [
        (Role.VIEWER, Permission.CAMPAIGN_READ, True),
        (Role.VIEWER, Permission.CAMPAIGN_WRITE, False),
        (Role.VIEWER, Permission.AGENT_RUN, False),
        (Role.OPERATOR, Permission.CAMPAIGN_WRITE, True),
        (Role.OPERATOR, Permission.AGENT_RUN, True),
        (Role.OPERATOR, Permission.USER_WRITE, False),
        (Role.OPERATOR, Permission.INTEGRATION_WRITE, False),
        (Role.ADMIN, Permission.USER_WRITE, True),
        (Role.ADMIN, Permission.INTEGRATION_WRITE, True),
        (Role.ADMIN, Permission.BILLING_MANAGE, False),
        (Role.OWNER, Permission.BILLING_MANAGE, True),
    ],
)
def test_matriz_de_permissoes(role, permission, esperado):
    assert has_permission(role, permission) is esperado


def test_papeis_sao_cumulativos():
    for menor, maior in (
        (Role.VIEWER, Role.OPERATOR),
        (Role.OPERATOR, Role.ADMIN),
        (Role.ADMIN, Role.OWNER),
    ):
        assert set(permissions_for(menor)) < set(permissions_for(maior))


def test_operator_nao_convida_usuario_pela_api(client, make_tenant, auth_headers):
    """O teste vale mais na borda: o papel precisa barrar na rota, não só na matriz."""
    from app.core.security import hash_password
    from app.db.models.platform import Membership, User
    from app.db.session import unscoped_session

    t = make_tenant()
    with unscoped_session(reason="test:add-operator") as session:
        operador = User(
            email="operador@example.com",
            password_hash=hash_password("senha-forte-123"),
            full_name="Operador",
        )
        session.add(operador)
        session.flush()
        session.add(
            Membership(tenant_id=t["tenant_id"], user_id=operador.id, role=Role.OPERATOR.value)
        )

    headers = auth_headers("operador@example.com", "senha-forte-123")
    criar_campanha = client.post(
        "/api/v1/campaigns", headers=headers, json={"name": "Ok", "slug": "ok"}
    )
    assert criar_campanha.status_code == 201

    convidar = client.post(
        "/api/v1/tenants/me/invitations",
        headers=headers,
        json={"email": "novo@example.com", "role": "viewer"},
    )
    assert convidar.status_code == 403
    assert convidar.json()["error"]["code"] == "permission_denied"


def test_platform_admin_e_separado_do_papel_do_tenant(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    # Owner do tenant não é admin da plataforma.
    assert client.get("/api/v1/admin/tenants", headers=headers).status_code == 403

    from app.db.models.platform import User
    from app.db.session import unscoped_session

    with unscoped_session(reason="test:promote-platform-admin") as session:
        session.get(User, t["user_id"]).is_platform_admin = True

    headers = auth_headers(t["email"], t["password"])
    resposta = client.get("/api/v1/admin/tenants", headers=headers)
    assert resposta.status_code == 200
    assert len(resposta.json()) >= 1
