"""Chave da Anthropic por empresa.

Cada tenant pode trazer a própria chave. Ela entra pelo mesmo caminho das
outras credenciais de cliente — `integrations`, cifrada com Fernet — e nunca
sai de lá em claro: a API devolve só os quatro últimos caracteres, o bastante
para alguém reconhecer qual chave configurou e nada além disso.

Ordem de resolução, e o porquê de cada passo:

1. **Chave do tenant.** É a dona do custo: quem configurou paga.
2. **Chave da plataforma**, só se `AI_PLATFORM_KEY_FALLBACK` estiver ligado.
   Desligado por padrão de propósito — com ele ligado, um tenant sem chave
   gasta no seu cartão, em silêncio.
3. **Nada.** O agente falha com uma mensagem que diz o que fazer, em vez de
   fingir que trabalhou.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import AppError, NotFound
from app.db.models.ai import Integration

PROVIDER = "anthropic"
ACCOUNT_REF = "default"


class AIKeyMissing(AppError):
    """Sem chave configurada não há como rodar agente — e isso precisa doer."""

    code = "ai_key_missing"
    status_code = 409


class AIKeyInvalid(AppError):
    code = "ai_key_invalid"
    status_code = 400


def _integration(session: Session, tenant_id: uuid.UUID) -> Integration | None:
    return session.execute(
        select(Integration)
        .where(Integration.tenant_id == tenant_id)
        .where(Integration.provider == PROVIDER)
        .limit(1)
    ).scalar_one_or_none()


def key_hint(chave: str) -> str:
    """O suficiente para reconhecer, insuficiente para usar."""
    return f"…{chave[-4:]}" if len(chave) > 4 else "…"


def resolve_api_key(session: Session, tenant_id: uuid.UUID) -> str:
    integracao = _integration(session, tenant_id)
    if integracao is not None and integracao.status == "connected":
        return decrypt_json(integracao.credentials_encrypted)["api_key"]

    if settings.ai_platform_key_fallback and settings.anthropic_api_key:
        return settings.anthropic_api_key

    raise AIKeyMissing(
        "Esta empresa ainda não tem uma chave da Anthropic configurada. "
        "Configure em Configurações → Inteligência artificial."
    )


def describe(session: Session, tenant_id: uuid.UUID) -> dict:
    """O que a tela mostra: se está configurada, qual é e desde quando."""
    integracao = _integration(session, tenant_id)
    if integracao is None:
        usando_plataforma = bool(
            settings.ai_platform_key_fallback and settings.anthropic_api_key
        )
        return {
            "configured": False,
            "key_hint": None,
            "status": "missing",
            "using_platform_key": usando_plataforma,
            "updated_at": None,
        }
    return {
        "configured": True,
        "key_hint": integracao.config.get("key_hint"),
        "status": integracao.status,
        "using_platform_key": False,
        "updated_at": integracao.updated_at.isoformat(),
    }


def store(
    session: Session, tenant_id: uuid.UUID, *, api_key: str, created_by: uuid.UUID | None
) -> Integration:
    chave = api_key.strip()
    if not chave:
        raise AIKeyInvalid("Chave vazia")

    integracao = _integration(session, tenant_id)
    if integracao is None:
        integracao = Integration(
            tenant_id=tenant_id,
            provider=PROVIDER,
            account_ref=ACCOUNT_REF,
            display_name="Anthropic",
            created_by=created_by,
            credentials_encrypted="",
        )
        session.add(integracao)

    integracao.credentials_encrypted = encrypt_json({"api_key": chave})
    integracao.config = {"key_hint": key_hint(chave)}
    integracao.status = "connected"
    integracao.last_error = None
    session.flush()
    return integracao


def remove(session: Session, tenant_id: uuid.UUID) -> None:
    integracao = _integration(session, tenant_id)
    if integracao is None:
        raise NotFound("Nenhuma chave configurada para esta empresa")
    session.delete(integracao)
    session.flush()


def mark_failure(session: Session, tenant_id: uuid.UUID, erro: str) -> None:
    """Chave que a Anthropic recusou fica marcada, para a tela poder avisar."""
    integracao = _integration(session, tenant_id)
    if integracao is not None:
        integracao.status = "error"
        integracao.last_error = erro[:500]
        session.flush()
