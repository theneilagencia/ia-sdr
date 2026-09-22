"""Convite com aceite: quem escolhe a senha é quem entra.

O fluxo anterior fazia duas coisas erradas de uma vez. A senha inicial de uma
pessoa era digitada por outra — e passava pelo navegador dela. E a resposta, para
explicar que a senha seria ignorada quando o email já existia, dizia exatamente
isso: enumeração da base de usuários entregue a quem convida.

Estes testes cobrem as duas pontas do conserto: o que o convite **não** conta, e
o que o aceite exige.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models.platform import Invitation, Membership
from app.db.session import tenant_session, unscoped_session
from app.services import invitations


def _convidar(client, headers, email: str, role: str = "operator"):
    r = client.post(
        "/api/v1/tenants/me/invitations", headers=headers, json={"email": email, "role": role}
    )
    assert r.status_code == 201, r.text
    return r.json(), r.json()["accept_url"].rsplit("/", 1)[-1]


def _erro(resposta) -> dict:
    return {k: v for k, v in resposta.json()["error"].items() if k != "request_id"}


def _aceitar(client, token: str, senha: str, nome: str = ""):
    return client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": senha, "full_name": nome},
    )


# ------------------------------------------------------------------ o convite


def test_convite_nao_revela_se_o_email_ja_tem_conta(client, make_tenant, auth_headers):
    """O ponto todo do fluxo novo.

    Duas empresas, dois convites: um para um email que já é dono de outra
    empresa, outro para um email que não existe em lugar nenhum. As duas
    respostas precisam ser indistinguíveis — é o que impede alguém de descobrir,
    de graça, quem já usa a plataforma.
    """
    # Growth porque são dois convites ao mesmo tempo, e o starter dá duas vagas
    # contando o owner: o que se compara aqui é a resposta, não o limite.
    a = make_tenant(plan="growth")
    b = make_tenant()
    headers = auth_headers(a["email"], a["password"])

    existente, _ = _convidar(client, headers, b["email"])
    novo, _ = _convidar(client, headers, f"ninguem-{uuid.uuid4().hex[:8]}@example.com")

    assert set(existente) == set(novo)
    assert "already_had_account" not in existente
    # Nenhum campo do corpo distingue os dois casos (fora o email e os ids).
    ignorar = {"id", "email", "accept_url", "created_at", "expires_at"}
    assert {k: v for k, v in existente.items() if k not in ignorar} == {
        k: v for k, v in novo.items() if k not in ignorar
    }


def test_o_link_aparece_uma_vez_e_o_banco_guarda_o_hash(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    corpo, token = _convidar(client, headers, "alguem@example.com")

    assert corpo["accept_url"].endswith(f"/convite/{token}")
    with tenant_session(t["tenant_id"]) as session:
        convite = session.execute(select(Invitation)).scalars().one()
        # O token não está no banco; o que está é o hash dele.
        assert token not in convite.token_hash
        assert len(convite.token_hash) == 64

    # E a listagem, que a tela usa, não devolve o link de novo.
    listado = client.get("/api/v1/tenants/me/invitations", headers=headers).json()
    assert len(listado) == 1
    assert "accept_url" not in listado[0]
    assert "token_hash" not in listado[0]


def test_convidar_o_mesmo_email_substitui_o_pendente(client, make_tenant, auth_headers):
    """Dois links válidos para a mesma pessoa é uma credencial a mais circulando.

    O plano starter aqui é de propósito: o owner mais um convite pendente já
    ocupam as duas vagas. Substituir o pendente não consome vaga nova, e a
    verificação de limite tem de saber disso — senão quem convida com o papel
    errado e tenta corrigir leva "limite do plano atingido" por causa do convite
    que a correção ia apagar.
    """
    t = make_tenant(plan="starter")
    headers = auth_headers(t["email"], t["password"])
    _, primeiro = _convidar(client, headers, "mesma@example.com", role="viewer")
    _, segundo = _convidar(client, headers, "mesma@example.com", role="admin")

    assert primeiro != segundo
    assert len(client.get("/api/v1/tenants/me/invitations", headers=headers).json()) == 1

    # O link antigo não vale mais; o novo vale, e com o papel novo.
    assert _aceitar(client, primeiro, "senha-forte-12345").status_code == 400
    assert _aceitar(client, segundo, "senha-forte-12345").status_code == 200
    membros = client.get("/api/v1/tenants/me/members", headers=headers).json()
    assert [m["role"] for m in membros if m["email"] == "mesma@example.com"] == ["admin"]


def test_convite_de_outra_empresa_nao_aparece_nem_e_revogavel(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    corpo, _ = _convidar(client, auth_headers(a["email"], a["password"]), "alvo@example.com")

    outros = auth_headers(b["email"], b["password"])
    assert client.get("/api/v1/tenants/me/invitations", headers=outros).json() == []
    assert (
        client.delete(f"/api/v1/tenants/me/invitations/{corpo['id']}", headers=outros).status_code
        == 404
    )


# ------------------------------------------------------------------ o aceite


def test_conta_nova_nasce_com_a_senha_de_quem_entra(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _, token = _convidar(client, headers, "nova@example.com", role="viewer")

    aceite = _aceitar(client, token, "a-senha-que-eu-escolhi", nome="Pessoa Nova")
    assert aceite.status_code == 200, aceite.text
    # Já entra: a prova de posse é a mesma que o login pede.
    assert aceite.json()["role"] == "viewer"
    assert aceite.json()["tenant_id"] == str(t["tenant_id"])

    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "nova@example.com", "password": "a-senha-que-eu-escolhi"},
        ).status_code
        == 200
    )
    membros = client.get("/api/v1/tenants/me/members", headers=headers).json()
    assert [m["full_name"] for m in membros if m["email"] == "nova@example.com"] == ["Pessoa Nova"]


def test_quem_ja_tem_conta_entra_com_a_senha_dela(client, make_tenant, auth_headers):
    """Identidade é global: a mesma pessoa serve duas empresas com uma conta.

    O convite anexa o vínculo; não troca a senha de ninguém — uma empresa
    trocando a senha de quem trabalha na outra é o contrário de isolamento.
    """
    dona = make_tenant()
    outra = make_tenant()
    headers = auth_headers(outra["email"], outra["password"])
    _, token = _convidar(client, headers, dona["email"], role="operator")

    aceite = _aceitar(client, token, dona["password"])
    assert aceite.status_code == 200, aceite.text
    assert aceite.json()["tenant_id"] == str(outra["tenant_id"])

    # A senha dela continua sendo a dela, e agora ela tem dois vínculos.
    eu = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {aceite.json()['access_token']}"}
    ).json()
    assert len(eu["memberships"]) == 2


def test_senha_errada_de_conta_existente_da_a_mesma_recusa(client, make_tenant, auth_headers):
    """A enumeração não pode voltar pela porta do aceite.

    Quem convida vê o link uma vez. Se o aceite com senha errada dissesse "esta
    conta existe, a senha está errada", quem convidou descobriria exatamente o
    que o convite se recusou a contar — e, sem a senha, não entraria: mas a
    informação já teria vazado.
    """
    dona = make_tenant()
    outra = make_tenant(plan="growth")
    headers = auth_headers(outra["email"], outra["password"])

    _, token_existente = _convidar(client, headers, dona["email"])
    errada = _aceitar(client, token_existente, "chute-de-quem-convidou")

    # E o mesmo para um email que não existe em lugar nenhum.
    _, token_novo = _convidar(client, headers, f"fantasma-{uuid.uuid4().hex[:8]}@example.com")
    curta = _aceitar(client, token_novo, "curta")

    assert errada.status_code == curta.status_code == 400
    # Fora o request_id, que é diferente em toda resposta por construção, as duas
    # recusas precisam ser a mesma resposta — mesma frase, mesmo código.
    assert _erro(errada) == _erro(curta)
    assert errada.json()["error"]["code"] == "invite_invalid"

    # Nenhum dos dois entrou.
    assert client.get("/api/v1/tenants/me/members", headers=headers).json().__len__() == 1


def test_token_expirado_nao_vale(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _, token = _convidar(client, headers, "atrasada@example.com")

    with tenant_session(t["tenant_id"]) as session:
        convite = session.execute(select(Invitation)).scalars().one()
        convite.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    assert _aceitar(client, token, "senha-forte-12345").status_code == 400
    # E a vaga do plano volta a ficar livre: convite expirado não conta.
    with tenant_session(t["tenant_id"]) as session:
        assert invitations.contar_pendentes(session, t["tenant_id"]) == 0


def test_token_vale_uma_vez(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _, token = _convidar(client, headers, "uma-vez@example.com")

    assert _aceitar(client, token, "senha-forte-12345").status_code == 200
    assert _aceitar(client, token, "senha-forte-12345").status_code == 400


def test_token_inventado_nao_vale(client):
    inventado = _aceitar(client, "token-que-ninguem-emitiu-1234567890", "senha-forte-12345")
    assert inventado.status_code == 400


def test_convite_reativa_quem_foi_desativado(client, make_tenant, auth_headers, membro):
    """Reentrada acontece: a pessoa saiu da empresa e voltou meses depois."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    pessoa = membro(t, role="operator")

    membros = client.get("/api/v1/tenants/me/members", headers=headers).json()
    user_id = next(m["user_id"] for m in membros if m["email"] == pessoa["email"])
    assert (
        client.patch(
            f"/api/v1/tenants/me/members/{user_id}", json={"is_active": False}, headers=headers
        ).status_code
        == 200
    )

    _, token = _convidar(client, headers, pessoa["email"], role="admin")
    assert _aceitar(client, token, pessoa["password"]).status_code == 200

    with unscoped_session(reason="test:conferir-vinculo") as identity:
        vinculo = identity.execute(
            select(Membership)
            .where(Membership.tenant_id == t["tenant_id"])
            .where(Membership.user_id == uuid.UUID(user_id))
        ).scalars().one()
        assert vinculo.is_active is True
        assert vinculo.role == "admin"


def test_o_aceite_fica_na_auditoria_da_empresa(client, make_tenant, auth_headers):
    """Quem entrou na empresa, e quando, é pergunta de auditoria."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _, token = _convidar(client, headers, "auditada@example.com")
    assert _aceitar(client, token, "senha-forte-12345").status_code == 200

    acoes = [e["action"] for e in client.get("/api/v1/tenants/me/audit", headers=headers).json()]
    assert "member.invited" in acoes
    assert "member.invite_accepted" in acoes


def test_convite_nao_cria_membro_antes_do_aceite(client, make_tenant, auth_headers):
    """Convite pendente não é pessoa dentro da empresa.

    Aparecer na lista de membros antes de aceitar daria a quem administra a
    impressão de que a pessoa já tem acesso — e de que dá para mexer no papel
    dela.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _convidar(client, headers, "ainda-nao@example.com")

    membros = client.get("/api/v1/tenants/me/members", headers=headers).json()
    assert [m["email"] for m in membros] == [t["email"]]
