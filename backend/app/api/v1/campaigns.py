"""Campanhas — o contexto operacional de tudo que a IA faz."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.db.models.sales import Campaign, CampaignStatus
from app.rbac.roles import Permission
from app.services import audit, limits
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _get_or_404(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        # O RLS já garante que campanha de outro tenant não chega aqui;
        # de fora, "não existe" e "não é seu" são a mesma resposta.
        raise NotFound("Campanha não encontrada")
    return campaign


@router.get("", response_model=list[schemas.CampaignResponse])
def list_campaigns(
    status_filter: str | None = Query(default=None, alias="status"),
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(Campaign).order_by(Campaign.created_at.desc())
    if status_filter:
        stmt = stmt.where(Campaign.status == status_filter)
    return list(db.execute(stmt).scalars())


@router.post("", response_model=schemas.CampaignResponse, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: schemas.CampaignCreate,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
):
    limits.check_can_create_campaign(db, ctx.tenant_id)
    campaign = Campaign(
        tenant_id=ctx.tenant_id,
        created_by=ctx.user_id,
        status=CampaignStatus.DRAFT.value,
        **payload.model_dump(),
    )
    db.add(campaign)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Já existe uma campanha com este slug neste tenant") from exc
    audit.record(
        db,
        action="campaign.created",
        resource_type="campaign",
        resource_id=campaign.id,
        payload={"slug": campaign.slug},
        context=ctx,
    )
    return campaign


@router.get("/{campaign_id}", response_model=schemas.CampaignResponse)
def get_campaign(
    campaign_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_READ)),
    db: Session = Depends(get_db),
):
    return _get_or_404(db, campaign_id)


@router.patch("/{campaign_id}", response_model=schemas.CampaignResponse)
def update_campaign(
    campaign_id: uuid.UUID,
    payload: schemas.CampaignUpdate,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
):
    campaign = _get_or_404(db, campaign_id)
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(campaign, key, value)
    audit.record(
        db,
        action="campaign.updated",
        resource_type="campaign",
        resource_id=campaign.id,
        payload={"fields": sorted(changes)},
        context=ctx,
    )
    return campaign


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_campaign(
    campaign_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    campaign = _get_or_404(db, campaign_id)
    campaign.status = CampaignStatus.ARCHIVED.value
    audit.record(
        db,
        action="campaign.archived",
        resource_type="campaign",
        resource_id=campaign.id,
        context=ctx,
    )
