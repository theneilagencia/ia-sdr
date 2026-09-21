"""Tenant atual, membros e trilha de auditoria."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_context, get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.core.security import hash_password
from app.db.models.platform import Membership, Tenant, User
from app.db.session import unscoped_session
from app.rbac.roles import Permission, Role
from app.services import audit, export, limits
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
    rows = (
        db.execute(select(Membership).where(Membership.tenant_id == ctx.tenant_id)).scalars().all()
    )
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
        identity.add(Membership(tenant_id=ctx.tenant_id, user_id=user.id, role=payload.role.value))
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


@router.patch("/me/members/{user_id}", response_model=schemas.MemberResponse)
def update_member(
    user_id: uuid.UUID,
    payload: schemas.MemberUpdate,
    ctx: TenantContext = Depends(require(Permission.USER_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.MemberResponse:
    """Muda o papel de um membro, ou o desativa.

    Duas travas, e as duas existem para o mesmo motivo — uma empresa sem ninguém
    que possa administrar é um chamado de suporte que só o operador da
    plataforma resolve:

    * ninguém rebaixa nem desativa a si mesmo;
    * o último owner ativo não sai nem é rebaixado.
    """
    if user_id == ctx.user_id:
        raise ConflictError(
            "Você não pode mudar o próprio papel nem se desativar. Peça a outro "
            "administrador desta empresa."
        )

    with unscoped_session(reason="tenant:update-member") as identity:
        membership = identity.execute(
            select(Membership)
            .where(Membership.tenant_id == ctx.tenant_id)
            .where(Membership.user_id == user_id)
        ).scalar_one_or_none()
        if membership is None:
            raise NotFound("Membro não encontrado nesta empresa")

        perde_owner = membership.role == Role.OWNER.value and (
            (payload.role is not None and payload.role != Role.OWNER) or payload.is_active is False
        )
        if perde_owner and _owners_ativos(identity, ctx.tenant_id) <= 1:
            raise ConflictError(
                "Esta é a única pessoa com papel de owner. Promova outra antes de mudar esta."
            )

        if payload.role is not None:
            membership.role = payload.role.value
        if payload.is_active is not None:
            membership.is_active = payload.is_active
        identity.flush()

        user = identity.get(User, user_id)
        resultado = schemas.MemberResponse(
            user_id=user_id,
            email=user.email,
            full_name=user.full_name,
            role=Role(membership.role),
            is_active=membership.is_active,
        )

    audit.record(
        db,
        action="member.updated",
        resource_type="user",
        resource_id=user_id,
        payload={"role": resultado.role.value, "is_active": resultado.is_active},
        context=ctx,
    )
    return resultado


@router.delete("/me/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.USER_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    """Remove o membro desta empresa.

    Remove o *vínculo*, não a identidade: `users` é global, e a mesma pessoa
    pode trabalhar em outras empresas da plataforma. Apagar o usuário aqui
    tiraria o acesso dela a clientes que não têm nada com este.
    """
    if user_id == ctx.user_id:
        raise ConflictError("Você não pode remover a si mesmo desta empresa.")

    with unscoped_session(reason="tenant:remove-member") as identity:
        membership = identity.execute(
            select(Membership)
            .where(Membership.tenant_id == ctx.tenant_id)
            .where(Membership.user_id == user_id)
        ).scalar_one_or_none()
        if membership is None:
            raise NotFound("Membro não encontrado nesta empresa")
        if membership.role == Role.OWNER.value and _owners_ativos(identity, ctx.tenant_id) <= 1:
            raise ConflictError(
                "Esta é a única pessoa com papel de owner. Promova outra antes de remover esta."
            )
        identity.delete(membership)

    audit.record(
        db,
        action="member.removed",
        resource_type="user",
        resource_id=user_id,
        context=ctx,
    )


def _owners_ativos(identity: Session, tenant_id: uuid.UUID) -> int:
    return int(
        identity.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.tenant_id == tenant_id)
            .where(Membership.role == Role.OWNER.value)
            .where(Membership.is_active.is_(True))
        ).scalar()
        or 0
    )


@router.get("/me/export")
def export_tenant_data(
    ctx: TenantContext = Depends(require(Permission.TENANT_WRITE)),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Leva os dados desta empresa embora, num JSON só.

    Uma plataforma da qual não se sai é uma plataforma na qual não se entra —
    quem avalia contratar pergunta "e se eu quiser sair?", e "abre um chamado"
    é pior resposta do que um endpoint. Também é o que a LGPD pede.

    Nenhum segredo vai junto: integrações saem pelo provedor e pela data, e as
    credenciais do cliente não são serializadas aqui nem em lugar nenhum.
    """
    pacote = export.export_tenant(db, ctx.tenant_id)
    audit.record(
        db,
        action="tenant.exported",
        resource_type="tenant",
        resource_id=ctx.tenant_id,
        payload={"counts": pacote["counts"], "truncated": pacote["truncated"]},
        context=ctx,
    )
    nome = f"{pacote['tenant']['slug'] or 'empresa'}-{date.today().isoformat()}.json"
    return JSONResponse(
        content=pacote,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


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
