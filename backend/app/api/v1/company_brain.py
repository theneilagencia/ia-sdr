"""Company Brain: o que a IA sabe sobre o negócio do cliente."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.db.models.knowledge import CompanyProfile
from app.rbac.roles import Permission
from app.services import audit
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/company-brain", tags=["company-brain"])


def _get_or_create(db: Session, tenant_id) -> CompanyProfile:
    profile = db.execute(
        select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_id)
    ).scalar_one_or_none()
    if profile is None:
        profile = CompanyProfile(tenant_id=tenant_id)
        db.add(profile)
        db.flush()
    return profile


@router.get("", response_model=schemas.CompanyBrainResponse)
def get_brain(
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_READ)),
    db: Session = Depends(get_db),
):
    return _get_or_create(db, ctx.tenant_id)


@router.put("", response_model=schemas.CompanyBrainResponse)
def update_brain(
    payload: schemas.CompanyBrainUpdate,
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_WRITE)),
    db: Session = Depends(get_db),
):
    profile = _get_or_create(db, ctx.tenant_id)
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(profile, key, value)
    audit.record(
        db,
        action="company_brain.updated",
        resource_type="company_profile",
        resource_id=profile.id,
        payload={"fields": sorted(changes)},
        context=ctx,
    )
    return profile
