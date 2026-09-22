"""Request ID, logging estruturado e rate limiting."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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
    """Janela deslizante por cliente.

    Em memória e por processo: suficiente para o MVP e para não deixar a API
    aberta. Com mais de uma réplica, trocar o backend por Redis sem mudar a
    interface.
    """

    #: A cada tantas requisições, varre as chaves que não têm mais nada dentro
    #: da janela. Sem isso o dicionário só cresce: cada login emite um token
    #: novo — logo, uma chave nova — e cada IP visto deixa a sua para trás. Uma
    #: API em pé por semanas acumularia uma entrada por token já emitido, e
    #: quem quisesse derrubá-la só precisaria variar o cabeçalho.
    LIMPEZA_A_CADA = 1000

    def __init__(self, app, *, limit: int | None = None, window: int | None = None):
        super().__init__(app)
        # `is None`, não `or`: zero é falsy, e `limit=0` (bloquear tudo, numa
        # emergência) ou `window=0` cairiam no padrão em silêncio — o parâmetro
        # aceito e ignorado é pior do que o parâmetro recusado.
        self.limit = settings.rate_limit_requests if limit is None else limit
        self.window = settings.rate_limit_window_seconds if window is None else window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._desde_a_limpeza = 0

    def _key(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth:
            # Digest, não `hash()`: o builtin é aleatorizado por processo e
            # truncá-lo em 32 bits faz dois tokens caírem no mesmo balde — uma
            # empresa gastando a cota de outra, sem nada na tela que explique.
            return "token:" + hashlib.sha256(auth.encode()).hexdigest()[:32]
        client = request.client.host if request.client else "unknown"
        return f"ip:{client}"

    def _limpar(self, agora: float) -> None:
        vencidas = [
            chave
            for chave, hits in self._hits.items()
            if not hits or agora - hits[-1] > self.window
        ]
        for chave in vencidas:
            del self._hits[chave]

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/health/ready"):
            return await call_next(request)
        key = self._key(request)
        now = time.monotonic()

        self._desde_a_limpeza += 1
        if self._desde_a_limpeza >= self.LIMPEZA_A_CADA:
            self._desde_a_limpeza = 0
            self._limpar(now)

        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            # A fila pode estar vazia aqui: com `limit=0` — bloquear tudo — nada
            # foi registrado e `hits[0]` estouraria, devolvendo 500 no lugar do
            # 429 justamente no momento em que alguém está contendo um incidente.
            espera = self.window - (now - hits[0]) if hits else self.window
            retry_after = max(1, int(espera))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Muitas requisições; tente novamente em instantes",
                    }
                },
                headers={"retry-after": str(retry_after)},
            )
        hits.append(now)
        return await call_next(request)
