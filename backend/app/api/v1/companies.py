"""Contas-alvo e o resultado da pesquisa sobre elas.

A empresa pesquisada pertence ao tenant que a pesquisou — não é um cadastro
global compartilhado entre clientes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import NotFound
from app.db.models.sales import Company, Research
from app.rbac.roles import Permission
from app.services import audit
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=list[schemas.CompanyResponse])
def list_companies(
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    return list(
        db.execute(select(Company).order_by(Company.created_at.desc()).limit(limit)).scalars()
    )


@router.post("", response_model=schemas.CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: schemas.CompanyCreate,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
):
    company = Company(tenant_id=ctx.tenant_id, **payload.model_dump())
    db.add(company)
    db.flush()
    audit.record(
        db,
        action="company.created",
        resource_type="company",
        resource_id=company.id,
        payload={"name": company.name, "domain": company.domain},
        context=ctx,
    )
    return company


@router.get("/{company_id}", response_model=schemas.CompanyResponse)
def get_company(
    company_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    company = db.get(Company, company_id)
    if company is None:
        raise NotFound("Empresa não encontrada")
    return company


@router.get("/{company_id}/research", response_model=list[schemas.ResearchResponse])
def list_research(
    company_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    """O que o Research Agent já produziu sobre esta conta."""
    if db.get(Company, company_id) is None:
        raise NotFound("Empresa não encontrada")
    return list(
        db.execute(
            select(Research)
            .where(Research.entity_id == company_id)
            .order_by(Research.created_at.desc())
        ).scalars()
    )
