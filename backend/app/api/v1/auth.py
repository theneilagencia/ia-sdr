"""Cadastro, login e troca de tenant ativo."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_context
from app.api.v1 import schemas
from app.core.config import settings
from app.core.errors import AuthenticationError, ConflictError, PermissionDenied
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models.knowledge import CompanyProfile
from app.db.models.platform import Membership, Plan, SubscriptionStatus, Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.rbac.roles import Role, permissions_for
from app.services import audit
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/auth", tags=["auth"])

TRIAL_DAYS = 14


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "tenant")[:60]


@router.post("/register", response_model=schemas.TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.RegisterRequest, request: Request) -> schemas.TokenResponse:
    """Cria tenant + usuário owner. É a porta de entrada de um novo cliente."""
    slug = payload.tenant_slug or _slugify(payload.tenant_name)

    with unscoped_session(reason="auth:register") as session:
        existing_user = session.execute(
            select(User).where(User.email == payload.email.lower())
        ).scalar_one_or_none()
        if existing_user is not None:
            raise ConflictError("Já existe uma conta com este email")
        if session.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        tenant = Tenant(
            name=payload.tenant_name,
            slug=slug,
            plan=Plan.STARTER.value,
            subscription_status=SubscriptionStatus.TRIAL.value,
            trial_ends_at=datetime.now(UTC) + timedelta(days=TRIAL_DAYS),
        )
        user = User(
            email=payload.email.lower(),
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
        )
        session.add_all([tenant, user])
        session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER.value))
        # Company Brain nasce junto com o tenant, vazio e pronto para preencher.
        session.add(CompanyProfile(tenant_id=tenant.id, legal_name=payload.tenant_name))
        try:
            session.flush()
        except IntegrityError as exc:
            raise ConflictError("Não foi possível criar o tenant") from exc

        tenant_id, user_id = tenant.id, user.id

    ctx = TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=Role.OWNER,
        request_id=getattr(request.state, "request_id", None),
    )
    with tenant_session(tenant_id) as session:
        audit.record(
            session,
            action="tenant.created",
            resource_type="tenant",
            resource_id=tenant_id,
            payload={"slug": slug},
            context=ctx,
        )

    token = create_access_token(user_id=user_id, tenant_id=tenant_id, role=Role.OWNER.value)
    return schemas.TokenResponse(
        access_token=token,
        expires_in_minutes=settings.access_token_ttl_minutes,
        tenant_id=tenant_id,
        role=Role.OWNER.value,
    )


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest) -> schemas.TokenResponse:
    with unscoped_session(reason="auth:login") as session:
        user = session.execute(
            select(User).where(User.email == payload.email.lower())
        ).scalar_one_or_none()
        # Mensagem única para não revelar se o email existe.
        if user is None or not verify_password(payload.password, user.password_hash):
            raise AuthenticationError("Email ou senha inválidos")
        if not user.is_active:
            raise AuthenticationError("Usuário inativo")

        memberships = list(
            session.execute(
                select(Membership, Tenant)
                .join(Tenant, Membership.tenant_id == Tenant.id)
                .where(Membership.user_id == user.id)
                .where(Membership.is_active.is_(True))
                .where(Tenant.is_active.is_(True))
                .order_by(Tenant.created_at)
            ).all()
        )
        if not memberships:
            raise PermissionDenied("Usuário sem tenant ativo")

        chosen = memberships[0]
        if payload.tenant_slug:
            match = [m for m in memberships if m[1].slug == payload.tenant_slug]
            if not match:
                raise PermissionDenied("Usuário não pertence a este tenant")
            chosen = match[0]

        membership, tenant = chosen
        user.last_login_at = datetime.now(UTC)
        token = create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            role=membership.role,
            is_platform_admin=user.is_platform_admin,
        )
        return schemas.TokenResponse(
            access_token=token,
            expires_in_minutes=settings.access_token_ttl_minutes,
            tenant_id=tenant.id,
            role=membership.role,
        )


@router.post("/switch-tenant", response_model=schemas.TokenResponse)
def switch_tenant(
    payload: schemas.SwitchTenantRequest,
    ctx: TenantContext = Depends(get_current_context),
) -> schemas.TokenResponse:
    """Emite um novo token para outro tenant do mesmo usuário."""
    with unscoped_session(reason="auth:switch-tenant") as session:
        membership = session.execute(
            select(Membership)
            .where(Membership.user_id == ctx.user_id)
            .where(Membership.tenant_id == payload.tenant_id)
            .where(Membership.is_active.is_(True))
        ).scalar_one_or_none()
        if membership is None:
            raise PermissionDenied("Usuário não pertence a este tenant")
        user = session.get(User, ctx.user_id)
        token = create_access_token(
            user_id=ctx.user_id,
            tenant_id=payload.tenant_id,
            role=membership.role,
            is_platform_admin=bool(user and user.is_platform_admin),
        )
        return schemas.TokenResponse(
            access_token=token,
            expires_in_minutes=settings.access_token_ttl_minutes,
            tenant_id=payload.tenant_id,
            role=membership.role,
        )


@router.get("/me", response_model=schemas.MeResponse)
def me(ctx: TenantContext = Depends(get_current_context)) -> schemas.MeResponse:
    with unscoped_session(reason="auth:me") as session:
        user = session.get(User, ctx.user_id)
        rows = session.execute(
            select(Membership, Tenant)
            .join(Tenant, Membership.tenant_id == Tenant.id)
            .where(Membership.user_id == ctx.user_id)
            .where(Membership.is_active.is_(True))
        ).all()
        return schemas.MeResponse(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_platform_admin=user.is_platform_admin,
            tenant_id=ctx.tenant_id,
            role=ctx.role,
            permissions=permissions_for(ctx.role),
            memberships=[
                schemas.MembershipInfo(
                    tenant_id=t.id, tenant_name=t.name, tenant_slug=t.slug, role=Role(m.role)
                )
                for m, t in rows
            ],
        )
