"""Painel da plataforma.

O cliente enxerga o próprio tenant. Aqui se enxerga a plataforma inteira — e
por isso toda rota deste módulo atravessa o RLS de forma explícita e auditada.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import require_platform_admin
from app.api.v1 import schemas
from app.billing.plans import limits_for
from app.core.errors import NotFound
from app.db.models.ai import AgentRun
from app.db.models.platform import AuditLog, Membership, Tenant, UsageEvent, User
from app.db.models.sales import Campaign
from app.db.session import unscoped_session

router = APIRouter(prefix="/admin", tags=["platform-admin"])


def _month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/tenants", response_model=list[schemas.AdminTenantResponse])
def list_tenants(
    limit: int = Query(default=100, le=500),
    admin: User = Depends(require_platform_admin),
):
    since = _month_start()
    with unscoped_session(reason="platform-admin:list-tenants") as session:
        users_by_tenant = dict(
            session.execute(
                select(Membership.tenant_id, func.count(Membership.id))
                .where(Membership.is_active.is_(True))
                .group_by(Membership.tenant_id)
            ).all()
        )
        campaigns_by_tenant = dict(
            session.execute(
                select(Campaign.tenant_id, func.count(Campaign.id)).group_by(Campaign.tenant_id)
            ).all()
        )
        usage_by_tenant = {
            tid: (int(units or 0), int(cost or 0))
            for tid, units, cost in session.execute(
                select(
                    UsageEvent.tenant_id,
                    func.sum(UsageEvent.units),
                    func.sum(UsageEvent.cost_micro_usd),
                )
                .where(UsageEvent.created_at >= since)
                .group_by(UsageEvent.tenant_id)
            ).all()
        }
        tenants = (
            session.execute(select(Tenant).order_by(Tenant.created_at.desc()).limit(limit))
            .scalars()
            .all()
        )
        return [
            schemas.AdminTenantResponse(
                id=t.id,
                name=t.name,
                slug=t.slug,
                plan=t.plan,
                subscription_status=t.subscription_status,
                is_active=t.is_active,
                users=users_by_tenant.get(t.id, 0),
                campaigns=campaigns_by_tenant.get(t.id, 0),
                ai_units_this_month=usage_by_tenant.get(t.id, (0, 0))[0],
                estimated_cost_usd=round(usage_by_tenant.get(t.id, (0, 0))[1] / 1e6, 4),
                created_at=t.created_at,
                limit_overrides=t.limit_overrides or {},
                effective_limits=limits_for(t.plan, t.limit_overrides),
            )
            for t in tenants
        ]


@router.patch("/tenants/{tenant_id}", response_model=schemas.AdminTenantResponse)
def update_tenant(
    tenant_id: uuid.UUID,
    payload: schemas.AdminTenantUpdate,
    admin: User = Depends(require_platform_admin),
):
    """Plano, status de assinatura e overrides de limite — o que o suporte precisa mexer."""
    with unscoped_session(reason="platform-admin:update-tenant") as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFound("Tenant não encontrado")
        if payload.plan is not None:
            tenant.plan = payload.plan.value
        if payload.subscription_status is not None:
            tenant.subscription_status = payload.subscription_status
        if payload.is_active is not None:
            tenant.is_active = payload.is_active
        if payload.limit_overrides is not None:
            tenant.limit_overrides = payload.limit_overrides
        # A ação do admin da plataforma fica registrada no tenant afetado.
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_user_id=admin.id,
                actor_role="platform_admin",
                action="tenant.updated_by_platform_admin",
                resource_type="tenant",
                resource_id=str(tenant_id),
                source="admin",
                payload=payload.model_dump(exclude_none=True, mode="json"),
            )
        )
        session.flush()
        return schemas.AdminTenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            plan=tenant.plan,
            subscription_status=tenant.subscription_status,
            is_active=tenant.is_active,
            users=0,
            campaigns=0,
            ai_units_this_month=0,
            estimated_cost_usd=0.0,
            created_at=tenant.created_at,
            limit_overrides=tenant.limit_overrides or {},
            effective_limits=limits_for(tenant.plan, tenant.limit_overrides),
        )


@router.get("/usage")
def platform_usage(admin: User = Depends(require_platform_admin)) -> dict:
    since = _month_start()
    with unscoped_session(reason="platform-admin:usage") as session:
        by_kind = {
            kind: {"units": int(units or 0), "cost_usd": round((cost or 0) / 1e6, 4)}
            for kind, units, cost in session.execute(
                select(
                    UsageEvent.kind,
                    func.sum(UsageEvent.units),
                    func.sum(UsageEvent.cost_micro_usd),
                )
                .where(UsageEvent.created_at >= since)
                .group_by(UsageEvent.kind)
            ).all()
        }
        tenants = int(session.execute(select(func.count(Tenant.id))).scalar_one())
        active = int(
            session.execute(
                select(func.count(Tenant.id)).where(Tenant.is_active.is_(True))
            ).scalar_one()
        )
        return {
            "period_start": since.isoformat(),
            "tenants": tenants,
            "active_tenants": active,
            "total_units": sum(v["units"] for v in by_kind.values()),
            "estimated_cost_usd": round(sum(v["cost_usd"] for v in by_kind.values()), 4),
            "by_kind": by_kind,
        }


@router.get("/system")
def system_health(admin: User = Depends(require_platform_admin)) -> dict:
    """Erros, jobs e agentes — o suficiente para saber se a plataforma está de pé."""
    with unscoped_session(reason="platform-admin:system") as session:
        runs_by_status = dict(
            session.execute(
                select(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status)
            ).all()
        )
        failures = (
            session.execute(
                select(AgentRun)
                .where(AgentRun.status.in_(("failed", "rejected")))
                .order_by(AgentRun.created_at.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        return {
            "agent_runs": {str(k): int(v) for k, v in runs_by_status.items()},
            "recent_failures": [
                {
                    "id": str(r.id),
                    "tenant_id": str(r.tenant_id),
                    "agent": r.agent_kind,
                    "status": r.status,
                    "error": r.error,
                    "created_at": r.created_at.isoformat(),
                }
                for r in failures
            ],
        }
