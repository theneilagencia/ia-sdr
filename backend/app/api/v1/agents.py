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
from app.core.errors import AppError, ConflictError
from app.db.models.ai import AgentKind, AgentRun
from app.db.models.jobs import Job, JobKind
from app.orchestrator.runner import new_job, run_job
from app.rbac.roles import Permission
from app.services import jobs as fila
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


@router.post("/jobs", response_model=schemas.JobResponse, status_code=status.HTTP_201_CREATED)
def enqueue_run(
    payload: schemas.AgentRunRequest,
    ctx: TenantContext = Depends(require(Permission.AGENT_RUN)),
    db: Session = Depends(get_db),
):
    """Enfileira a execução em vez de rodar na requisição.

    É o caminho certo para pesquisa: uma busca na web leva minutos, e nenhuma
    tela deve ficar pendurada esperando. `POST /runs` continua existindo para
    quando se quer o resultado na hora.
    """
    if payload.agent not in {k.value for k in AgentKind}:
        raise UnknownAgent(f"Agente desconhecido: {payload.agent}")

    envelope = new_job(
        tenant_id=ctx.tenant_id,
        agent=payload.agent,
        campaign_id=payload.campaign_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        user_id=ctx.user_id,
        params=payload.params,
    )
    job = fila.enqueue(
        db,
        tenant_id=ctx.tenant_id,
        kind=JobKind.AGENT_RUN,
        payload=envelope.to_dict(),
        # Pedir duas vezes a mesma pesquisa da mesma conta é desperdício.
        dedupe_key=f"{payload.agent}:{payload.entity_id}" if payload.entity_id else None,
    )
    if job is None:
        raise ConflictError("Já existe uma execução deste agente na fila para este alvo")
    return job


@router.get("/jobs", response_model=list[schemas.JobResponse])
def list_jobs(
    limit: int = Query(default=50, le=200),
    status_filter: str | None = Query(default=None, alias="status"),
    ctx: TenantContext = Depends(require(Permission.AGENT_READ)),
    db: Session = Depends(get_db),
):
    """O que está na fila desta empresa, e o que falhou."""
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    return list(db.execute(stmt).scalars())


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
