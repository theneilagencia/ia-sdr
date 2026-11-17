"""O comando que cria a primeira empresa de um cliente.

É o primeiro contato de quem compra com a plataforma, e o único passo do
runbook que roda uma vez e não tem segunda chance: a senha aparece na tela e
não é recuperável. Um erro aqui não aparece aqui — aparece no primeiro login,
como "Email ou senha inválidos", que é a mesma frase de senha errada porque
ela é única de propósito.

Estes testes existem por causa de dois defeitos encontrados subindo a stack de
produção de verdade: o script aceitava email que a API recusa, e guardava o
endereço como digitado enquanto o login procura em minúsculas.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.platform import Membership, Tenant, User
from app.db.session import unscoped_session
from scripts.criar_empresa import criar
from scripts.criar_empresa import main as criar_empresa


def _usuario(email: str) -> User | None:
    with unscoped_session(reason="test:provisionamento") as session:
        return session.execute(select(User).where(User.email == email)).scalar_one_or_none()


def test_a_senha_impressa_entra_pela_api(client: TestClient):
    """O teste que fecha o laço: o que o script imprime é o que o login aceita."""
    resultado = criar(
        nome="Empresa do Primeiro Dia",
        email="dono@primeirodia.com",
        slug=None,
        plano="starter",
        nome_completo="Dono",
    )
    assert resultado is not None
    _, senha, email = resultado

    r = client.post("/api/v1/auth/login", json={"email": email, "password": senha})
    assert r.status_code == 200, r.text
    # Entrou já na empresa criada, como owner: é o que o runbook promete.
    assert r.json()["role"] == "owner"
    vinculos = client.get(
        "/api/v1/auth/me", headers={"authorization": f"Bearer {r.json()['access_token']}"}
    ).json()["memberships"]
    assert [v["tenant_name"] for v in vinculos] == ["Empresa do Primeiro Dia"]


def test_email_com_maiuscula_ainda_entra(client: TestClient):
    """O login compara `email.lower()`; guardar como digitado trancava o dono fora.

    Antes da correção, criar com `Dono@Empresa.com` gravava a maiúscula, o login
    procurava `dono@empresa.com`, não achava, e respondia "Email ou senha
    inválidos" — indistinguível de senha errada. O cliente ficava sem entrar na
    própria empresa no primeiro dia, e a mensagem apontava para o lugar errado.
    """
    resultado = criar(
        nome="Empresa Com Maiuscula",
        email="  Dono@EmpresaMaiuscula.com  ",
        slug=None,
        plano="starter",
        nome_completo="Dono",
    )
    assert resultado is not None
    _, senha, email = resultado
    assert email == "dono@empresamaiuscula.com"

    # Entra pelo endereço normalizado...
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": senha}).status_code
        == 200
    )
    # ...e também digitando como digitou na hora de criar.
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "Dono@EmpresaMaiuscula.com", "password": senha},
        ).status_code
        == 200
    )


def test_email_que_a_api_recusa_nao_cria_nada(capsys):
    """TLD reservado (`.test`, `.local`) é o caso real: o script criava, o login não deixava.

    O importante não é a recusa, é o que **não** acontece: sem empresa criada,
    sem usuário, sem vínculo. Criar metade e falhar depois deixaria um slug
    ocupado e um usuário que nunca entra — e o próximo `criar_empresa` com o
    endereço certo reclamaria de nome já existente.
    """
    antes = _contagens()
    assert (
        criar(
            nome="Empresa Invalida",
            email="dono@validacao.test",
            slug=None,
            plano="starter",
            nome_completo="Dono",
        )
        is None
    )
    saida = capsys.readouterr().out
    assert "não serve para entrar na aplicação" in saida
    assert "nada foi criado" in saida
    assert _contagens() == antes


def test_o_comando_devolve_erro_no_email_invalido(capsys):
    """Código de saída importa: o runbook tem passos depois deste."""
    assert criar_empresa(["--nome", "X", "--email", "sem-arroba"]) == 1
    assert "não serve" in capsys.readouterr().out


def test_o_comando_imprime_o_login_normalizado(capsys):
    """Imprimir o que foi digitado, e não o que funciona, é entregar a senha errada."""
    assert criar_empresa(["--nome", "Empresa Impressa", "--email", "DONO@Impressa.com"]) == 0
    saida = capsys.readouterr().out
    assert "login: dono@impressa.com" in saida
    assert "DONO@Impressa.com" not in saida


def test_nao_sobrescreve_empresa_existente(capsys):
    """Rodar duas vezes é engano comum de quem provisiona; trocar a senha de quem já entra, não."""
    assert criar_empresa(["--nome", "Empresa Repetida", "--email", "dono@repetida.com"]) == 0
    assert criar_empresa(["--nome", "Empresa Repetida", "--email", "outro@repetida.com"]) == 1
    assert "já existe uma empresa" in capsys.readouterr().out


def test_email_ja_usado_nao_vira_segunda_conta(capsys):
    """Mesma pessoa em duas empresas é convite, não provisionamento: a senha é dela."""
    assert criar_empresa(["--nome", "Primeira Casa", "--email", "consultor@duas.com"]) == 0
    assert criar_empresa(["--nome", "Segunda Casa", "--email", "consultor@duas.com"]) == 1
    assert "já tem usuário" in capsys.readouterr().out


def _contagens() -> tuple[int, int, int]:
    with unscoped_session(reason="test:provisionamento") as session:
        return (
            len(session.execute(select(Tenant)).scalars().all()),
            len(session.execute(select(User)).scalars().all()),
            len(session.execute(select(Membership)).scalars().all()),
        )
