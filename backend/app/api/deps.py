"""Dependências de requisição: identidade, contexto de tenant e sessão de banco.

O caminho é sempre o mesmo: token -> usuário -> membership válido -> contexto ->
sessão de banco com `app.tenant_id` setado. O tenant nunca vem do corpo da
requisição.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, PermissionDenied
from app.core.security import decode_access_token
from app.db.models.platform import Membership, Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.rbac.roles import Permission, Role, has_permission
from app.tenancy.context import TenantContext, use_context

bearer_scheme = HTTPBearer(auto_error=False)


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def get_current_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TenantContext:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Credenciais ausentes")

    claims = decode_access_token(credentials.credentials)
    user_id = uuid.UUID(claims["sub"])
    tenant_raw = claims.get("tid")
    if not tenant_raw:
        raise AuthenticationError("Token sem tenant ativo; selecione um tenant")
    tenant_id = uuid.UUID(tenant_raw)

    # Revalida a associação no banco: o papel no token é uma pista, não a fonte.
    with unscoped_session(reason="auth:resolve-membership") as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Usuário inválido ou inativo")
        _reject_token_older_than_password(claims, user)
        membership = session.execute(
            select(Membership)
            .where(Membership.user_id == user_id)
            .where(Membership.tenant_id == tenant_id)
            .where(Membership.is_active.is_(True))
        ).scalar_one_or_none()
        if membership is None:
            raise PermissionDenied("Usuário não pertence a este tenant")
        tenant = session.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise PermissionDenied("Tenant inativo")
        role = Role(membership.role)
        is_platform_admin = user.is_platform_admin

    ctx = TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        is_platform_admin=is_platform_admin,
        request_id=get_request_id(request),
        source="api",
    )
    request.state.tenant_context = ctx
    return ctx


def _reject_token_older_than_password(claims: dict, user: User) -> None:
    """Trocar a senha encerra as sessões abertas.

    Sem isso, trocar a senha não tira ninguém de dentro: o token que já estava
    na mão de quem invadiu continua valendo até expirar — doze horas, por
    padrão. O `iat` é truncado em segundos, então a comparação usa o mesmo
    truncamento para não invalidar o token que a própria troca acabou de emitir.
    """
    if user.password_changed_at is None:
        return
    emitido_em = claims.get("iat")
    if emitido_em is None or int(emitido_em) < int(user.password_changed_at.timestamp()):
        raise AuthenticationError("Sessão encerrada porque a senha foi alterada. Entre de novo.")


def get_db(ctx: TenantContext = Depends(get_current_context)) -> Iterator[Session]:
    """Sessão já restrita ao tenant do contexto — é o único jeito normal de ler dados."""
    with use_context(ctx), tenant_session(ctx.tenant_id) as session:
        yield session


def require(*permissions: Permission):
    """Exige todas as permissões informadas para o papel do usuário."""

    def _dependency(ctx: TenantContext = Depends(get_current_context)) -> TenantContext:
        missing = [p.value for p in permissions if not has_permission(ctx.role, p)]
        if missing:
            raise PermissionDenied(
                "Permissão insuficiente para esta operação",
                details={"role": ctx.role.value, "missing": missing},
            )
        return ctx

    return _dependency


def require_platform_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """Painel da plataforma: enxerga todos os tenants, e por isso é separado."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Credenciais ausentes")
    claims = decode_access_token(credentials.credentials)
    with unscoped_session(reason="platform-admin:resolve-user") as session:
        user = session.get(User, uuid.UUID(claims["sub"]))
        if user is None or not user.is_active or not user.is_platform_admin:
            raise PermissionDenied("Acesso restrito a administradores da plataforma")
        _reject_token_older_than_password(claims, user)
        session.expunge(user)
        return user
