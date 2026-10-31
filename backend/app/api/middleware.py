"""Request ID, logging estruturado e rate limiting."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core import limitador as limitador_mod
from app.core.config import settings

logger = logging.getLogger("ia_sdr.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        ctx = getattr(request.state, "tenant_context", None)
        logger.info(
            "%s %s %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={"request_id": request_id, **(ctx.as_log_fields() if ctx else {})},
        )
        response.headers["x-request-id"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Janela deslizante por cliente, no backend que a configuração pedir.

    A contagem em si vive em `app/core/limitador.py`, em dois backends com a
    mesma interface: em memória (uma réplica) e Redis (balde compartilhado). Com
    duas réplicas e o balde em memória, o teto anunciado vale o dobro — cada
    processo conta o seu —, e é por isso que o de Redis existe.
    """

    def __init__(
        self,
        app,
        *,
        limit: int | None = None,
        window: int | None = None,
        backend: limitador_mod.Limitador | None = None,
    ):
        super().__init__(app)
        # `is None`, não `or`: zero é falsy, e `limit=0` (bloquear tudo, numa
        # emergência) ou `window=0` cairiam no padrão em silêncio — o parâmetro
        # aceito e ignorado é pior do que o parâmetro recusado.
        self.limit = settings.rate_limit_requests if limit is None else limit
        self.window = settings.rate_limit_window_seconds if window is None else window
        self.backend = backend or limitador_mod.construir(settings.redis_url)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/health/ready"):
            return await call_next(request)

        chave = limitador_mod.chave_do_cliente(
            request.headers.get("authorization", ""),
            request.client.host if request.client else None,
        )
        permitido, espera = self.backend.consumir(chave, limite=self.limit, janela=self.window)
        if not permitido:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Muitas requisições; tente novamente em instantes",
                    }
                },
                headers={"retry-after": str(max(1, espera))},
            )
        return await call_next(request)
