"""Disparo, configuração e consulta de execuções de agente.

Todo disparo passa pelo orquestrador — não existe rota que fale com um modelo
direto.

A configuração por agente (modelo, instruções, teto de saída) existia no modelo
de dados e era lida pelo orquestrador, mas não tinha porta de entrada: mudar o
modelo de um cliente exigia `INSERT` no banco. Agora é uma tela, e o que ela
guarda é exatamente o que o `context_builder` já consultava.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import AppError, ConflictError
from app.db.models.ai import AgentKind, AgentRun, AIAgent
from app.db.models.jobs import Job, JobKind
from app.orchestrator.runner import new_job, run_job
from app.rbac.roles import Permission
from app.services import audit
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


def _config_efetiva(linha: AIAgent | None, definition, units: int) -> dict:
    """A configuração que o orquestrador vai usar, com o padrão à vista."""
    from app.core.config import settings

    return {
        "kind": definition.kind.value,
        "name": linha.name if linha else definition.default_name,
        "model": linha.model if linha else definition.default_model,
        "instructions": (
            linha.instructions if linha and linha.instructions else definition.base_instructions
        ),
        "max_output_tokens": (linha.max_output_tokens if linha else settings.ai_max_output_tokens),
        "is_active": linha.is_active if linha else True,
        "configured": linha is not None,
        "units_per_run": units,
        "default_name": definition.default_name,
        "default_model": definition.default_model,
        "default_instructions": definition.base_instructions,
    }


@router.get("/config", response_model=list[schemas.AgentConfigResponse])
def list_config(
    ctx: TenantContext = Depends(require(Permission.AGENT_READ)),
    db: Session = Depends(get_db),
):
    """Os quatro agentes com o que vale hoje nesta empresa."""
    from app.orchestrator.agents import AGENT_DEFINITIONS
    from app.services.usage import UNIT_COST

    linhas = {
        linha.kind: linha
        for linha in db.execute(select(AIAgent).order_by(AIAgent.created_at)).scalars()
    }
    return [
        _config_efetiva(linhas.get(d.kind.value), d, UNIT_COST[d.usage_kind])
        for d in AGENT_DEFINITIONS.values()
    ]


@router.put("/config/{kind}", response_model=schemas.AgentConfigResponse)
def upsert_config(
    kind: str,
    payload: schemas.AgentConfigUpdate,
    ctx: TenantContext = Depends(require(Permission.AGENT_WRITE)),
    db: Session = Depends(get_db),
):
    """Cria ou ajusta a configuração deste agente nesta empresa.

    `PUT` porque o endereço é o agente, não a linha: a primeira chamada cria, as
    seguintes ajustam. O que vem é o que muda — campo omitido fica como estava —
    e a tela manda o formulário inteiro, que é o que mantém as duas leituras
    iguais.
    """
    from app.orchestrator.agents import AGENT_DEFINITIONS
    from app.services.usage import UNIT_COST

    try:
        agent_kind = AgentKind(kind)
    except ValueError as exc:
        aceitos = ", ".join(k.value for k in AGENT_DEFINITIONS)
        raise UnknownAgent(f"Agente desconhecido: {kind}. Os aceitos são: {aceitos}") from exc

    definition = AGENT_DEFINITIONS[agent_kind]
    linha = db.execute(
        select(AIAgent).where(AIAgent.kind == agent_kind.value).limit(1)
    ).scalar_one_or_none()
    if linha is None:
        linha = AIAgent(
            tenant_id=ctx.tenant_id,
            kind=agent_kind.value,
            name=definition.default_name,
            model=definition.default_model,
            instructions="",
        )
        db.add(linha)

    dados = payload.model_dump(exclude_unset=True)
    for campo, valor in dados.items():
        # Instrução vazia não é "sem instrução": é volta ao padrão do agente, e
        # é o caminho de saída de quem escreveu algo ruim e quer desfazer.
        setattr(linha, campo, "" if campo == "instructions" and not valor else valor)
    db.flush()

    audit.record(
        db,
        action="agent.config.updated",
        resource_type="ai_agent",
        resource_id=linha.id,
        payload={"kind": agent_kind.value, "campos": sorted(dados)},
        context=ctx,
    )
    return _config_efetiva(linha, definition, UNIT_COST[definition.usage_kind])


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
