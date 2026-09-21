"""Trilha de auditoria.

Toda ação relevante de escrita registra quem fez, em qual tenant e sobre o quê.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.platform import AuditLog
from app.tenancy.context import TenantContext, get_current_context_or_none


def record(
    session: Session,
    *,
    action: str,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    payload: dict | None = None,
    context: TenantContext | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    ctx = context or get_current_context_or_none()
    if ctx is None:
        raise ValueError("Auditoria exige contexto de tenant explícito")
    entry = AuditLog(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_role=ctx.role.value,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        source=ctx.source,
        request_id=ctx.request_id,
        ip_address=ip_address,
        payload=payload or {},
    )
    session.add(entry)
    session.flush()
    return entry


def list_recent(session: Session, tenant_id: uuid.UUID, limit: int = 100) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())
