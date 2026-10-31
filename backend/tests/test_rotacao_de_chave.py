"""Rotação da chave de cifra: o que ela precisa preservar, e o que não pode esquecer.

A pergunta que este arquivo responde é "e se a chave vazar?". Antes, a resposta
era inaceitável: trocar a chave tornava ilegível toda credencial de email e chave
de API já salva — o que, na prática, significa nunca trocar, e é como um
incidente de segurança vira permanente.

O teste que mais importa aqui é o tripwire: uma rotação que esquece uma coluna
não falha, ela **apaga em silêncio** a única cópia daquele segredo, e o estrago
aparece semanas depois, no meio de um disparo.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.core import crypto
from app.core.config import settings
from app.core.segredos_guardados import COLUNAS_CIFRADAS
from app.db.base import Base
from app.db.models.platform import User
from app.db.session import AdminSessionFactory, unscoped_session
from scripts.rotacionar_chave import rotacionar

CHAVE_A = Fernet.generate_key().decode()
CHAVE_B = Fernet.generate_key().decode()

BACKEND = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def com_chaves(monkeypatch):
    """Troca as chaves em tempo de execução, como um `.env` novo faria."""

    def _usar(atual: str, anteriores: str = ""):
        monkeypatch.setattr(settings, "secrets_encryption_key", atual)
        monkeypatch.setattr(settings, "secrets_encryption_keys_previous", anteriores)

    return _usar


# ------------------------------------------------------------------ o mecanismo


def test_a_chave_nova_cifra_e_a_antiga_ainda_decifra(com_chaves):
    """É o que permite subir a chave nova sem parar a plataforma."""
    com_chaves(CHAVE_A)
    antigo = crypto.encrypt_secret("senha-do-email-do-cliente")

    com_chaves(CHAVE_B, CHAVE_A)
    # O que foi cifrado antes continua legível...
    assert crypto.decrypt_secret(antigo) == "senha-do-email-do-cliente"
    # ...e o que se cifra agora usa a chave nova: sem a B, não abre.
    novo = crypto.encrypt_secret("token-do-ravi")
    com_chaves(CHAVE_A)
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt_secret(novo)


def test_trocar_a_chave_em_execucao_nao_fica_preso_no_cache(com_chaves):
    """O cache antes era `lru_cache` sem argumento: a troca não tinha efeito.

    Um processo que seguisse cifrando com a chave que acabou de sair produziria
    segredos que ninguém consegue abrir depois — e o defeito só apareceria na
    hora de usar a credencial.
    """
    com_chaves(CHAVE_A)
    com_a = crypto.encrypt_secret("x")
    com_chaves(CHAVE_B)
    com_b = crypto.encrypt_secret("x")
    assert com_a != com_b
    assert crypto.decrypt_secret(com_b) == "x"
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt_secret(com_a)


def test_recifrar_nao_precisa_do_conteudo(com_chaves):
    com_chaves(CHAVE_A)
    antes = crypto.encrypt_secret("chave-da-anthropic")
    com_chaves(CHAVE_B, CHAVE_A)
    depois = crypto.recifrar(antes)

    assert depois != antes
    # Agora abre só com a nova — a antiga já pode sair da configuração.
    com_chaves(CHAVE_B)
    assert crypto.decrypt_secret(depois) == "chave-da-anthropic"


def test_recifrar_o_que_nenhuma_chave_abre_recusa_em_voz_alta(com_chaves):
    """Silêncio aqui seria o pior desfecho: o script diria "pronto" e o dado estaria perdido."""
    com_chaves(CHAVE_A)
    perdido = crypto.encrypt_secret("algo")
    com_chaves(CHAVE_B)  # sem a A na lista
    with pytest.raises(crypto.SecretDecryptionError, match="SECRETS_ENCRYPTION_KEYS_PREVIOUS"):
        crypto.recifrar(perdido)


# ------------------------------------------------------------------ o script


def test_o_script_rotaciona_as_credenciais_e_o_segundo_fator(
    client, make_tenant, auth_headers, com_chaves
):
    """O caminho completo, com dado de verdade nas duas colunas cifradas."""
    com_chaves(CHAVE_A)
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    # Uma credencial de integração pela rota genérica: `PUT /settings/ai` valida a
    # chave contra a Anthropic antes de salvar (de propósito — chave que não
    # funciona não deve ficar guardada), e aqui o que se quer é o texto cifrado.
    salvo = client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "provider": "smtp",
            "account_ref": "vendas@empresa-do-cliente.com",
            "credentials": {"username": "vendas", "password": "senha-do-email-do-cliente"},
        },
    )
    assert salvo.status_code == 201, salvo.text
    # ...e um segredo de segundo fator.
    assert client.post("/api/v1/auth/mfa/setup", headers=headers).status_code == 200

    with AdminSessionFactory() as session:
        antes = {
            c.tabela: [
                v
                for _, v in session.execute(
                    text(f"SELECT id, {c.coluna} FROM {c.tabela} WHERE {c.coluna} IS NOT NULL")  # noqa: S608
                ).all()
            ]
            for c in COLUNAS_CIFRADAS
        }
    assert all(len(v) >= 1 for v in antes.values()), antes

    # Chave nova entra, antiga fica na lista: é o estado em que a rotação roda.
    com_chaves(CHAVE_B, CHAVE_A)
    assert rotacionar() == 0

    # Agora tudo abre **só** com a nova — que é o que autoriza apagar a antiga.
    com_chaves(CHAVE_B)
    assert rotacionar(apenas_verificar=True) == 0
    with AdminSessionFactory() as session:
        depois = {
            c.tabela: [
                v
                for _, v in session.execute(
                    text(f"SELECT id, {c.coluna} FROM {c.tabela} WHERE {c.coluna} IS NOT NULL")  # noqa: S608
                ).all()
            ]
            for c in COLUNAS_CIFRADAS
        }
    for tabela, valores in depois.items():
        assert valores != antes[tabela], f"{tabela} não foi recifrada"

    # E a credencial continua legível de verdade, não só diferente: o que
    # importa da rotação é o conteúdo sobreviver.
    with AdminSessionFactory() as session:
        cifrado = session.execute(
            text("SELECT credentials_encrypted FROM integrations LIMIT 1")
        ).scalar_one()
    assert crypto.decrypt_json(cifrado)["password"] == "senha-do-email-do-cliente"


def test_a_verificacao_acusa_segredo_orfao(client, make_tenant, auth_headers, com_chaves):
    """Quem apagar a chave antiga antes de rotacionar precisa descobrir aqui.

    Este é o cenário que o `--verificar` existe para pegar — e ele tem de sair
    com código diferente de zero, senão um script de publicação segue adiante.
    """
    com_chaves(CHAVE_A)
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    criado = client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "provider": "smtp",
            "account_ref": "orfa@empresa.com",
            "credentials": {"password": "vai-ficar-orfa"},
        },
    )
    assert criado.status_code == 201, criado.text

    com_chaves(CHAVE_B)  # a antiga saiu sem a rotação ter rodado
    assert rotacionar(apenas_verificar=True) == 1


def test_rotacao_pendente_e_dito_em_voz_alta(com_chaves):
    com_chaves(CHAVE_B)
    assert crypto.rotacao_pendente() is False
    com_chaves(CHAVE_B, CHAVE_A)
    assert crypto.rotacao_pendente() is True


# ------------------------------------------------------------------ o tripwire


def test_toda_coluna_cifrada_existe_no_modelo():
    tabelas = Base.metadata.tables
    for coluna in COLUNAS_CIFRADAS:
        assert coluna.tabela in tabelas, coluna.tabela
        assert coluna.coluna in tabelas[coluna.tabela].columns, coluna
        assert coluna.chave in tabelas[coluna.tabela].columns, coluna


def test_nenhuma_coluna_cifrada_ficou_fora_da_lista():
    """O teste que impede a próxima rotação de apagar dado em silêncio.

    Procura no código toda atribuição que cifra e confere que o destino está
    declarado. Quem adicionar uma coluna cifrada nova vê este teste falhar com o
    nome do atributo que esqueceu — em vez de descobrir meses depois, quando a
    chave antiga já saiu do `.env` e o segredo virou ruído.
    """
    registradas = {c.coluna for c in COLUNAS_CIFRADAS}
    escritas: dict[str, str] = {}
    for arquivo in (BACKEND / "app").rglob("*.py"):
        if arquivo.name == "crypto.py":
            continue
        for achado in re.finditer(
            r"(?:\.|\b)(\w+)\s*=\s*encrypt_(?:secret|json)\(", arquivo.read_text()
        ):
            escritas[achado.group(1)] = str(arquivo.relative_to(BACKEND))

    faltando = {
        atributo: onde for atributo, onde in escritas.items() if atributo not in registradas
    }
    assert not faltando, (
        "Coluna cifrada fora de COLUNAS_CIFRADAS (app/core/segredos_guardados.py): "
        f"{faltando}. Sem registrar, a próxima rotação de chave deixa esse segredo "
        "para trás — e ele fica ilegível quando a chave antiga sair da configuração."
    )


def test_o_segundo_fator_entra_na_rotacao(client, make_tenant, auth_headers, com_chaves):
    """Explícito porque `mfa_secret` não segue o padrão de nome `*_encrypted`.

    Uma lista baseada em convenção de nome teria deixado esta coluna fora — e o
    estrago seria todo mundo com segundo fator trancado fora da conta.
    """
    com_chaves(CHAVE_A)
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.post("/api/v1/auth/mfa/setup", headers=headers)

    com_chaves(CHAVE_B, CHAVE_A)
    assert rotacionar() == 0
    com_chaves(CHAVE_B)

    with unscoped_session(reason="test:conferir-mfa") as session:
        user = session.execute(select(User).where(User.email == t["email"])).scalar_one()
        # Decifra com a chave nova, sozinha.
        assert len(crypto.decrypt_secret(user.mfa_secret)) >= 16
