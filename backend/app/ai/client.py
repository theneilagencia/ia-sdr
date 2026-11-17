"""Acesso ao modelo, com a chave da empresa que está executando.

A chave nunca aparece em resposta de API, log ou frontend: ela é decifrada
aqui dentro, usada na chamada e descartada com o cliente.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.services.ai_credentials import resolve_api_key

logger = logging.getLogger("ia_sdr.ai")


class AIUnavailable(AppError):
    code = "ai_unavailable"
    status_code = 503


def _anthropic():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependência declarada
        raise AIUnavailable("Pacote `anthropic` não instalado") from exc
    return anthropic


def build_client(api_key: str):
    anthropic = _anthropic()
    return anthropic.Anthropic(
        api_key=api_key,
        timeout=settings.ai_timeout_seconds,
        max_retries=settings.ai_max_retries,
    )


def client_for(session: Session, tenant_id: uuid.UUID):
    """O cliente do tenant que está executando — nunca um cliente global."""
    return build_client(resolve_api_key(session, tenant_id))


def validate_key(api_key: str) -> tuple[bool, str | None]:
    """Testa a chave sem gastar token.

    Lista os modelos disponíveis: é uma leitura, não uma geração, então o
    botão "testar" da tela não cobra nada de ninguém.
    """
    anthropic = _anthropic()
    try:
        build_client(api_key).models.list(limit=1)
        return True, None
    except anthropic.AuthenticationError:
        return False, "A Anthropic recusou esta chave."
    except anthropic.PermissionDeniedError:
        return False, "Esta chave existe, mas não tem permissão para usar a API de mensagens."
    except anthropic.APIConnectionError:
        return False, "Não foi possível falar com a Anthropic agora. Tente de novo em instantes."
    except anthropic.APIStatusError as exc:
        return False, f"A Anthropic respondeu com erro {exc.status_code}."
