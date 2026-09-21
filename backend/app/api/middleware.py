"""Request ID, logging estruturado e rate limiting."""

from __future__ import annotations

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

    def __init__(self, app, *, limit: int | None = None, window: int | None = None):
        super().__init__(app)
        self.limit = limit or settings.rate_limit_requests
        self.window = window or settings.rate_limit_window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth:
            return f"token:{hash(auth) & 0xFFFFFFFF}"
        client = request.client.host if request.client else "unknown"
        return f"ip:{client}"

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/health/ready"):
            return await call_next(request)
        key = self._key(request)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry_after = max(1, int(self.window - (now - hits[0])))
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
