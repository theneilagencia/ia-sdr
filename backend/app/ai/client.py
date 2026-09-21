"""Acesso ao modelo.

A chave da Anthropic é da plataforma, não do tenant: fica no backend, sai de
variável de ambiente (ou de um secrets manager) e nunca é serializada em
resposta de API. Se um cliente trouxer a própria chave no futuro, ela entra
pelo caminho de `integrations`, cifrada, e é resolvida aqui dentro.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.errors import AppError


class AIUnavailable(AppError):
    code = "ai_unavailable"
    status_code = 503


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


@lru_cache
def get_client():
    """Cliente da Anthropic, criado uma vez por processo.

    O import é tardio de propósito: quem não roda agente não precisa do SDK
    instalado para subir a API.
    """
    if not is_configured():
        raise AIUnavailable(
            "ANTHROPIC_API_KEY não configurada: o executor real dos agentes está desligado"
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependência declarada
        raise AIUnavailable("Pacote `anthropic` não instalado") from exc

    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.ai_timeout_seconds,
        max_retries=settings.ai_max_retries,
    )
