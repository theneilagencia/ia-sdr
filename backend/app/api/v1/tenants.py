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
from app.core.config import settings
from app.core.errors import ConflictError, NotFound
from app.db.models.platform import Membership, Tenant, User
from app.db.session import unscoped_session
from app.rbac.roles import Permission, Role
from app.services import audit, export, invitations, limits
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


@router.get("/me/invitations", response_model=list[schemas.InvitationResponse])
def list_invitations(
    ctx: TenantContext = Depends(require(Permission.USER_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.InvitationResponse]:
    """Os convites que ainda podem ser aceitos.

    Sem esta lista, um convite pendente é invisível: ninguém sabe que a vaga do
    plano já está ocupada, nem para quem o link foi mandado.
    """
    return [
        schemas.InvitationResponse(
            id=c.id,
            email=c.email,
            role=Role(c.role),
            expires_at=c.expires_at,
            created_at=c.created_at,
        )
        for c in invitations.pendentes(db, ctx.tenant_id)
    ]


@router.post(
    "/me/invitations",
    response_model=schemas.InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
def invite_member(
    payload: schemas.InvitationCreate,
    ctx: TenantContext = Depends(require(Permission.USER_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.InvitationCreated:
    """Convida alguém para esta empresa e devolve o link, uma vez.

    Substitui a criação direta de membro, que tinha dois problemas de uma vez: a
    senha inicial de outra pessoa passava pela mão de quem convidava, e a
    resposta precisava dizer se aquele email já existia na plataforma — para
    explicar que a senha seria ignorada. Agora quem escolhe a senha é quem entra,
    e a resposta é a mesma para qualquer email.

    O link não é enviado por email daqui: o email configurado na empresa é o
    canal de prospecção dela, com aquecimento e teto diário, e gastar essa cota
    com mensagem interna seria trocar reputação de domínio por conveniência.
    """
    limits.check_can_add_user(db, ctx.tenant_id, email=payload.email)

    convite, token = invitations.criar(
        db,
        tenant_id=ctx.tenant_id,
        email=payload.email,
        role=payload.role,
        invited_by=ctx.user_id,
    )
    audit.record(
        db,
        action="member.invited",
        resource_type="invitation",
        resource_id=convite.id,
        payload={"email": convite.email, "role": convite.role},
        context=ctx,
    )
    base = settings.app_base_url.rstrip("/")
    return schemas.InvitationCreated(
        id=convite.id,
        email=convite.email,
        role=Role(convite.role),
        expires_at=convite.expires_at,
        created_at=convite.created_at,
        accept_url=f"{base}/convite/{token}",
    )


@router.delete("/me/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    invitation_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.USER_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    """Cancela um convite que ainda não foi aceito.

    É o que transforma "mandei para o email errado" em um clique, em vez de uma
    credencial válida circulando por sete dias.
    """
    convite = invitations.revogar(db, ctx.tenant_id, invitation_id)
    if convite is None:
        raise NotFound("Convite não encontrado ou já aceito")
    audit.record(
        db,
        action="member.invite_revoked",
        resource_type="invitation",
        resource_id=invitation_id,
        payload={"email": convite.email},
        context=ctx,
    )


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
