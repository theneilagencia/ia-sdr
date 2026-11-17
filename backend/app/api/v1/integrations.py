"""Integrações do tenant. Credenciais entram, cifradas; nunca saem."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.crypto import encrypt_json
from app.core.errors import ConflictError, NotFound
from app.db.models.ai import Integration
from app.rbac.roles import Permission
from app.services import audit, limits
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/integrations", tags=["integrations"])

_EMAIL_PROVIDERS = {"gmail", "outlook", "smtp"}


def _to_response(item: Integration) -> schemas.IntegrationResponse:
    return schemas.IntegrationResponse(
        id=item.id,
        provider=item.provider,
        account_ref=item.account_ref,
        display_name=item.display_name,
        status=item.status,
        config=item.config,
        created_at=item.created_at,
    )


@router.get("", response_model=list[schemas.IntegrationResponse])
def list_integrations(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_READ)),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(Integration).order_by(Integration.created_at)).scalars().all()
    return [_to_response(item) for item in rows]


@router.post("", response_model=schemas.IntegrationResponse, status_code=status.HTTP_201_CREATED)
def create_integration(
    payload: schemas.IntegrationCreate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    if payload.provider in _EMAIL_PROVIDERS:
        limits.check_can_add_email_account(db, ctx.tenant_id)
    item = Integration(
        tenant_id=ctx.tenant_id,
        provider=payload.provider,
        account_ref=payload.account_ref,
        display_name=payload.display_name,
        credentials_encrypted=encrypt_json(payload.credentials),
        config=payload.config,
        created_by=ctx.user_id,
    )
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Esta conta já está conectada") from exc
    audit.record(
        db,
        action="integration.connected",
        resource_type="integration",
        resource_id=item.id,
        # Nunca logar credencial — só o provedor e a conta.
        payload={"provider": item.provider, "account_ref": item.account_ref},
        context=ctx,
    )
    return _to_response(item)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_integration(
    integration_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    item = db.get(Integration, integration_id)
    if item is None:
        raise NotFound("Integração não encontrada")
    db.delete(item)
    audit.record(
        db,
        action="integration.disconnected",
        resource_type="integration",
        resource_id=integration_id,
        payload={"provider": item.provider},
        context=ctx,
    )
