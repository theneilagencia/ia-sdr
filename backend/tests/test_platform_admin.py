"""O painel da plataforma e a marca que o destranca.

O painel existia desde o primeiro commit com quatro rotas e nenhuma forma de
alcançá-las: nada no código definia `is_platform_admin`, e a coluna nasce
`false`. Estas verificações cobrem as duas metades do problema — a marca passa a
existir, e ela concede exatamente o que deve conceder.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models.platform import User
from app.db.session import unscoped_session
from scripts.promover_admin import main as promover

ROTAS_DO_PAINEL = (
    "/api/v1/admin/tenants",
    "/api/v1/admin/usage",
    "/api/v1/admin/system",
)


def _email_de(tenant: dict) -> str:
    return tenant["email"]


def test_sem_a_marca_o_painel_recusa(client, make_tenant, auth_headers):
    """O estado em que a plataforma nasce: painel inalcançável."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    for rota in ROTAS_DO_PAINEL:
        assert client.get(rota, headers=headers).status_code == 403, rota


def test_promover_destranca_o_painel(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    assert promover(["--email", t["email"]]) == 0

    for rota in ROTAS_DO_PAINEL:
        resposta = client.get(rota, headers=headers)
        assert resposta.status_code == 200, f"{rota}: {resposta.text}"

    empresas = client.get("/api/v1/admin/tenants", headers=headers).json()
    assert any(e["slug"] == t["slug"] for e in empresas)


def test_vale_para_o_token_que_ja_esta_na_mao(client, make_tenant, auth_headers):
    """O resolvedor lê o usuário no banco a cada pedido, então promover não
    exige entrar de novo — e revogar vale no ato."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    assert client.get("/api/v1/admin/usage", headers=headers).status_code == 403

    promover(["--email", t["email"]])
    assert client.get("/api/v1/admin/usage", headers=headers).status_code == 200

    promover(["--email", t["email"], "--revogar"])
    assert client.get("/api/v1/admin/usage", headers=headers).status_code == 403


def test_promover_nao_da_acesso_ao_dado_de_outra_empresa(client, make_tenant, auth_headers):
    """A distinção que sustenta a promessa da plataforma: ver a conta de um
    cliente e ler as conversas dele são coisas diferentes.

    Se esta verificação falhar, a marca virou um mestre de chaves — e o
    isolamento entre empresas, que é o produto, deixou de existir.
    """
    operador, cliente_a = make_tenant(), make_tenant()
    headers = auth_headers(operador["email"], operador["password"])
    promover(["--email", operador["email"]])

    # Enxerga que a empresa existe, no painel.
    empresas = client.get("/api/v1/admin/tenants", headers=headers).json()
    assert any(e["slug"] == cliente_a["slug"] for e in empresas)

    # E não enxerga nada de dentro dela: o token do operador tem o tenant dele.
    campanha = client.post(
        "/api/v1/campaigns",
        json={"name": "Só do cliente", "slug": f"c-{uuid.uuid4().hex[:8]}", "channels": ["email"]},
        headers=auth_headers(cliente_a["email"], cliente_a["password"]),
    )
    assert campanha.status_code == 201
    assert client.get("/api/v1/campaigns", headers=headers).json() == []
    assert client.get("/api/v1/knowledge/documents", headers=headers).json() == []
    assert client.get("/api/v1/conversations", headers=headers).json() == []


def test_promover_email_que_nao_existe_falha(capsys):
    """A promoção não cria conta: quem digitou errado precisa saber."""
    assert promover(["--email", f"ninguem-{uuid.uuid4().hex[:6]}@example.com"]) == 1
    saida = capsys.readouterr().out
    assert "não existe usuário" in saida
    assert "criar_empresa" in saida


def test_promover_duas_vezes_nao_reclama(make_tenant, capsys):
    t = make_tenant()
    assert promover(["--email", t["email"]]) == 0
    assert promover(["--email", t["email"]]) == 0
    assert "já é" in capsys.readouterr().out


def test_email_com_maiusculas_encontra_o_usuario(make_tenant):
    t = make_tenant()
    assert promover(["--email", t["email"].upper()]) == 0
    with unscoped_session(reason="test:conferir") as session:
        user = session.execute(select(User).where(User.email == t["email"])).scalar_one()
        assert user.is_platform_admin is True


def test_listar_mostra_quem_tem_a_marca(make_tenant, capsys):
    t = make_tenant()
    promover(["--email", t["email"]])
    assert promover(["--listar"]) == 0
    saida = capsys.readouterr().out
    assert t["email"] in saida
    assert "empresa(s)" in saida


def test_sem_argumento_mostra_ajuda(capsys):
    assert promover([]) == 1
    assert "--listar" in capsys.readouterr().out


def test_painel_devolve_os_limites_para_poder_editar_sem_apagar(
    client, make_tenant, auth_headers
):
    """O PATCH substitui o dicionário inteiro de overrides.

    Sem receber de volta o que já está guardado, um painel que edita limites
    edita no escuro: salvar uma alteração apagaria em silêncio os outros
    overrides do contrato. `effective_limits` vem junto porque é o número que a
    cota consulta — o que responde "por que este cliente travou".
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    assert promover(["--email", t["email"]]) == 0

    salvo = client.patch(
        f"/api/v1/admin/tenants/{t['tenant_id']}",
        headers=headers,
        json={"plan": "starter", "limit_overrides": {"campaigns": 7, "users": -1}},
    )
    assert salvo.status_code == 200, salvo.text
    assert salvo.json()["limit_overrides"] == {"campaigns": 7, "users": -1}
    # O plano Starter dá 1 campanha; o override manda.
    assert salvo.json()["effective_limits"]["campaigns"] == 7
    assert salvo.json()["effective_limits"]["users"] == -1
    # O que o override não toca continua vindo do plano.
    assert salvo.json()["effective_limits"]["prospects_per_month"] == 1_000

    na_lista = next(
        e
        for e in client.get("/api/v1/admin/tenants", headers=headers).json()
        if e["id"] == str(t["tenant_id"])
    )
    assert na_lista["limit_overrides"] == {"campaigns": 7, "users": -1}
    assert na_lista["effective_limits"]["campaigns"] == 7
