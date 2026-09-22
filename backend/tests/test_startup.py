"""As verificações de boot em produção.

O valor delas está em falhar. Um deploy que herda o `.env.example` funciona
perfeitamente — aceita login, salva credencial, manda email — e é justamente
por funcionar que ninguém descobre que o segredo de assinatura do token está
publicado no repositório.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid

import pytest

from app.core.startup import InsecureConfiguration, verify_production_secrets

FERNET_VALIDA = base64.urlsafe_b64encode(os.urandom(32)).decode()
JWT_FORTE = base64.urlsafe_b64encode(os.urandom(48)).decode()


@pytest.fixture
def producao(monkeypatch):
    """Configuração de produção correta. Cada teste estraga uma coisa só."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", JWT_FORTE)
    monkeypatch.setattr(settings, "secrets_encryption_key", FERNET_VALIDA)
    monkeypatch.setattr(settings, "public_base_url", "https://app.exemplo.com")
    monkeypatch.setattr(settings, "cors_origins", ["https://app.exemplo.com"])
    return settings


def test_configuracao_correta_passa(producao):
    verify_production_secrets()


def test_fora_de_producao_nao_exige_nada(monkeypatch):
    """Exigir chave Fernet para rodar um teste local é o que faz gente commitar .env."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "jwt_secret", "change-me-in-production")
    monkeypatch.setattr(settings, "secrets_encryption_key", "dev-only-key-not-for-production")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")

    verify_production_secrets()


@pytest.mark.parametrize("valor", ["change-me-in-production", "ci-secret", ""])
def test_jwt_secret_de_exemplo_impede_o_boot(producao, monkeypatch, valor):
    monkeypatch.setattr(producao, "jwt_secret", valor)

    with pytest.raises(InsecureConfiguration, match="JWT_SECRET"):
        verify_production_secrets()


def test_jwt_secret_curto_impede_o_boot(producao, monkeypatch):
    monkeypatch.setattr(producao, "jwt_secret", "curto-demas-mas-nao-de-exemplo")

    with pytest.raises(InsecureConfiguration, match="JWT_SECRET"):
        verify_production_secrets()


def test_chave_de_cifra_de_exemplo_impede_o_boot(producao, monkeypatch):
    monkeypatch.setattr(producao, "secrets_encryption_key", "dev-only-key-not-for-production")

    with pytest.raises(InsecureConfiguration, match="SECRETS_ENCRYPTION_KEY"):
        verify_production_secrets()


def test_chave_de_cifra_invalida_impede_o_boot(producao, monkeypatch):
    """Sem isso, a falha aparece quando a primeira empresa tenta salvar a conta
    de email — em runtime, para o usuário, e não na subida."""
    monkeypatch.setattr(producao, "secrets_encryption_key", "chave-longa-mas-nao-fernet-de-32")

    with pytest.raises(InsecureConfiguration, match="Fernet"):
        verify_production_secrets()


@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "https://127.0.0.1", "http://0.0.0.0:8000"]
)
def test_url_publica_local_impede_o_boot(producao, monkeypatch, url):
    monkeypatch.setattr(producao, "public_base_url", url)

    with pytest.raises(InsecureConfiguration, match="PUBLIC_BASE_URL"):
        verify_production_secrets()


def test_url_publica_sem_tls_impede_o_boot(producao, monkeypatch):
    monkeypatch.setattr(producao, "public_base_url", "http://app.exemplo.com")

    with pytest.raises(InsecureConfiguration, match="PUBLIC_BASE_URL"):
        verify_production_secrets()


def test_erro_junta_todos_os_problemas(producao, monkeypatch):
    """Quem está publicando não deveria descobrir um problema por subida."""
    monkeypatch.setattr(producao, "jwt_secret", "change-me-in-production")
    monkeypatch.setattr(producao, "secrets_encryption_key", "dev-only-key-not-for-production")
    monkeypatch.setattr(producao, "public_base_url", "http://localhost:8000")

    with pytest.raises(InsecureConfiguration) as erro:
        verify_production_secrets()

    mensagem = str(erro.value)
    assert "JWT_SECRET" in mensagem
    assert "SECRETS_ENCRYPTION_KEY" in mensagem
    assert "PUBLIC_BASE_URL" in mensagem


def test_cors_local_em_producao_avisa_mas_nao_impede(producao, monkeypatch, caplog):
    """Com token em Authorization o CORS não é a fronteira — mas origem de
    desenvolvimento liberada em produção é sinal de .env copiado sem revisar."""
    monkeypatch.setattr(
        producao, "cors_origins", ["https://app.exemplo.com", "http://localhost:3000"]
    )

    with caplog.at_level(logging.WARNING, logger="app.core.startup"):
        verify_production_secrets()

    assert "localhost:3000" in caplog.text


def test_link_de_descadastro_sobrevive_a_rotacao_do_jwt(monkeypatch):
    """Rotacionar o JWT_SECRET não pode matar descadastro já enviado.

    O link não expira de propósito, e rotacionar o segredo dos tokens de sessão
    é o que se faz depois de um vazamento. Enquanto os dois compartilhavam o
    mesmo segredo, a segunda ação desfazia a primeira — e quem clicasse num link
    antigo recebia "link inválido" no lugar de sair da lista.
    """
    from app.core.config import settings
    from app.services import unsubscribe

    tenant, contato = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(settings, "unsubscribe_secret", "segredo-proprio-do-descadastro")
    token = unsubscribe.make_token(tenant, contato)

    # O JWT roda; o link continua valendo.
    monkeypatch.setattr(settings, "jwt_secret", "outro-segredo-completamente-diferente")
    assert unsubscribe.parse_token(token) == (tenant, contato)


def test_sem_segredo_proprio_o_descadastro_herda_o_jwt(monkeypatch):
    """Instalação que nunca configurou a variável continua funcionando.

    É como os links já emitidos foram assinados; trocar isso de uma vez quebraria
    exatamente o que a separação existe para proteger.
    """
    from app.core.config import settings
    from app.services import unsubscribe

    tenant, contato = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(settings, "unsubscribe_secret", None)
    monkeypatch.setattr(settings, "jwt_secret", "o-segredo-de-sempre")
    token = unsubscribe.make_token(tenant, contato)
    assert unsubscribe.parse_token(token) == (tenant, contato)

    # E um token assinado com outro segredo não passa.
    monkeypatch.setattr(settings, "jwt_secret", "segredo-trocado")
    with pytest.raises(unsubscribe.InvalidUnsubscribeToken):
        unsubscribe.parse_token(token)
