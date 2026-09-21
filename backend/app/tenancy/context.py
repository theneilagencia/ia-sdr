"""Contexto de tenant.

Toda unidade de trabalho — requisição HTTP, job assíncrono, run de agente —
carrega explicitamente quem está executando e para qual tenant. Nada é
inferido do frontend e nada é herdado por acidente entre jobs.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.core.errors import TenantContextMissing
from app.rbac.roles import Role


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    role: Role
    is_platform_admin: bool = False
    request_id: str | None = None
    source: str = "api"  # api | worker | system
    metadata: dict = field(default_factory=dict)

    def as_log_fields(self) -> dict:
        return {
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "role": self.role.value,
            "request_id": self.request_id,
            "source": self.source,
        }


_current: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)


def get_current_context() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise TenantContextMissing("Nenhum contexto de tenant ativo nesta execução")
    return ctx


def get_current_context_or_none() -> TenantContext | None:
    return _current.get()


@contextmanager
def use_context(ctx: TenantContext) -> Iterator[TenantContext]:
    previous = _current.get()
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        try:
            _current.reset(token)
        except ValueError:
            # Dependência do FastAPI: entrada e saída podem acontecer em
            # contextos diferentes (threadpool). Restaurar pelo valor resolve.
            _current.set(previous)


def system_context(tenant_id: uuid.UUID, *, source: str = "worker") -> TenantContext:
    """Contexto para execuções sem usuário humano (workers, schedulers)."""
    return TenantContext(
        tenant_id=tenant_id,
        user_id=None,
        role=Role.OWNER,
        is_platform_admin=False,
        source=source,
    )
