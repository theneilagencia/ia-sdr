"""Disparo e consulta de execuções de agente.

Todo disparo passa pelo orquestrador — não existe rota que fale com um modelo
direto.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import AppError
from app.db.models.ai import AgentKind, AgentRun
from app.orchestrator.runner import new_job, run_job
from app.rbac.roles import Permission
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/agents", tags=["agents"])


class UnknownAgent(AppError):
    code = "unknown_agent"
    status_code = 400


@router.get("/catalog")
def catalog(ctx: TenantContext = Depends(require(Permission.AGENT_READ))) -> list[dict]:
    from app.orchestrator.agents import AGENT_DEFINITIONS
    from app.services.usage import UNIT_COST

    return [
        {
            "kind": d.kind.value,
            "name": d.default_name,
            "model": d.default_model,
            "tools": list(d.tools),
            "units_per_run": UNIT_COST[d.usage_kind],
        }
        for d in AGENT_DEFINITIONS.values()
    ]


@router.post("/runs", response_model=schemas.AgentRunResponse, status_code=status.HTTP_201_CREATED)
def dispatch_run(
    payload: schemas.AgentRunRequest,
    ctx: TenantContext = Depends(require(Permission.AGENT_RUN)),
    db: Session = Depends(get_db),
):
    if payload.agent not in {k.value for k in AgentKind}:
        raise UnknownAgent(f"Agente desconhecido: {payload.agent}")
    # tenant_id vem do contexto autenticado, nunca do corpo da requisição.
    envelope = new_job(
        tenant_id=ctx.tenant_id,
        agent=payload.agent,
        campaign_id=payload.campaign_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        user_id=ctx.user_id,
        params=payload.params,
    )
    return run_job(db, envelope)


@router.get("/runs", response_model=list[schemas.AgentRunResponse])
def list_runs(
    limit: int = Query(default=50, le=200),
    agent: str | None = None,
    ctx: TenantContext = Depends(require(Permission.AGENT_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    if agent:
        stmt = stmt.where(AgentRun.agent_kind == agent)
    return list(db.execute(stmt).scalars())
