"""Tenant atual, membros e trilha de auditoria."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_context, get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.core.security import hash_password
from app.db.models.platform import Membership, Tenant, User
from app.db.session import unscoped_session
from app.rbac.roles import Permission, Role
from app.services import audit, limits
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/tenants", tags=["tenant"])


@router.get("/me", response_model=schemas.TenantResponse)
def get_my_tenant(
    ctx: TenantContext = Depends(require(Permission.TENANT_READ)),
    db: Session = Depends(get_db),
) -> Tenant:
    tenant = db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise NotFound("Tenant não encontrado")
    return tenant


@router.patch("/me", response_model=schemas.TenantResponse)
def update_my_tenant(
    payload: schemas.TenantUpdate,
    ctx: TenantContext = Depends(require(Permission.TENANT_WRITE)),
    db: Session = Depends(get_db),
) -> Tenant:
    tenant = db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise NotFound("Tenant não encontrado")
    if payload.name is not None:
        tenant.name = payload.name
    if payload.settings is not None:
        tenant.settings = payload.settings
    audit.record(
        db,
        action="tenant.updated",
        resource_type="tenant",
        resource_id=tenant.id,
        payload=payload.model_dump(exclude_none=True),
        context=ctx,
    )
    return tenant


@router.get("/me/members", response_model=list[schemas.MemberResponse])
def list_members(
    ctx: TenantContext = Depends(require(Permission.USER_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.MemberResponse]:
    # `users` é global (uma identidade pode servir vários tenants), então a
    # consulta parte sempre dos memberships deste tenant.
    rows = db.execute(
        select(Membership).where(Membership.tenant_id == ctx.tenant_id)
    ).scalars().all()
    user_ids = [m.user_id for m in rows]
    with unscoped_session(reason="tenant:list-members") as identity:
        users = {
            u.id: u
            for u in identity.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
        }
    return [
        schemas.MemberResponse(
            user_id=m.user_id,
            email=users[m.user_id].email,
            full_name=users[m.user_id].full_name,
            role=Role(m.role),
            is_active=m.is_active,
        )
        for m in rows
        if m.user_id in users
    ]


@router.post(
    "/me/members", response_model=schemas.MemberResponse, status_code=status.HTTP_201_CREATED
)
def add_member(
    payload: schemas.MemberCreate,
    ctx: TenantContext = Depends(require(Permission.USER_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.MemberResponse:
    limits.check_can_add_user(db, ctx.tenant_id)

    with unscoped_session(reason="tenant:add-member") as identity:
        user = identity.execute(
            select(User).where(User.email == payload.email.lower())
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=payload.email.lower(),
                password_hash=hash_password(payload.password),
                full_name=payload.full_name,
            )
            identity.add(user)
            identity.flush()
        existing = identity.execute(
            select(Membership)
            .where(Membership.tenant_id == ctx.tenant_id)
            .where(Membership.user_id == user.id)
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("Usuário já faz parte deste tenant")
        identity.add(
            Membership(tenant_id=ctx.tenant_id, user_id=user.id, role=payload.role.value)
        )
        identity.flush()
        result = schemas.MemberResponse(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=payload.role,
            is_active=True,
        )

    audit.record(
        db,
        action="member.added",
        resource_type="user",
        resource_id=result.user_id,
        payload={"role": payload.role.value},
        context=ctx,
    )
    return result


@router.get("/me/audit", response_model=list[schemas.AuditLogResponse])
def list_audit(
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(require(Permission.AUDIT_READ)),
    db: Session = Depends(get_db),
):
    return audit.list_recent(db, ctx.tenant_id, limit=limit)


@router.get("/me/usage")
def get_usage(
    ctx: TenantContext = Depends(require(Permission.USAGE_READ)),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.usage import usage_summary

    return usage_summary(db, ctx.tenant_id)


# Mantém `get_current_context` no módulo para uso por rotas futuras sem RBAC.
__all__ = ["router", "get_current_context"]
