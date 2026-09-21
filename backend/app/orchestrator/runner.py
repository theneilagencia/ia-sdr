"""Orquestrador central.

Não há agente solto pelo código. Tudo entra por aqui:

    envelope -> contexto do tenant -> cota -> execução -> consumo -> auditoria

Duas decisões que valem explicar:

1. A contabilidade do run (estado, consumo, auditoria) é gravada em transação
   própria. Se a execução falhar e o trabalho de domínio for desfeito, o
   registro de que ela aconteceu — e quanto custou — continua lá. Sem isso,
   falha de agente vira buraco no histórico e na fatura.
2. A chamada ao modelo é um ponto de extensão (`AgentExecutor`). No Sprint 1 o
   executor registrado não chama LLM nenhum: o que está pronto e testado é a
   fronteira de isolamento em volta da chamada.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.ai import AgentRun, RunStatus
from app.db.session import tenant_session
from app.orchestrator.agents import AGENT_DEFINITIONS, AgentDefinition
from app.orchestrator.context_builder import AgentContext, build_context
from app.orchestrator.envelope import JobEnvelope
from app.services import audit, usage
from app.tenancy.context import TenantContext, use_context


class AgentExecutor(Protocol):
    def __call__(self, context: AgentContext, envelope: JobEnvelope) -> dict: ...


def _echo_executor(context: AgentContext, envelope: JobEnvelope) -> dict:
    """Placeholder determinístico até o Sprint 3 ligar o modelo de verdade."""
    return {
        "agent": envelope.agent.value,
        "context_digest": context.digest(),
        "knowledge_chunks": len(context.knowledge),
        "campaign": (context.campaign or {}).get("name"),
        "note": "executor de desenvolvimento: nenhuma chamada de modelo realizada",
    }


_EXECUTORS: dict[str, AgentExecutor] = {}


def register_executor(kind: str, executor: AgentExecutor) -> None:
    _EXECUTORS[kind] = executor


def get_executor(kind: str) -> AgentExecutor:
    return _EXECUTORS.get(kind, _echo_executor)


def _open_run(envelope: JobEnvelope, run_id: uuid.UUID) -> None:
    with tenant_session(envelope.tenant_id) as book:
        book.add(
            AgentRun(
                id=run_id,
                tenant_id=envelope.tenant_id,
                job_id=envelope.job_id,
                agent_kind=envelope.agent.value,
                campaign_id=envelope.campaign_id,
                entity_type=envelope.entity_type,
                entity_id=envelope.entity_id,
                requested_by=envelope.user_id,
                status=RunStatus.RUNNING.value,
                input=envelope.to_dict(),
                started_at=datetime.now(UTC),
            )
        )


def _close_run(
    envelope: JobEnvelope,
    run_id: uuid.UUID,
    ctx: TenantContext,
    *,
    status: RunStatus,
    output: dict | None = None,
    error: str | None = None,
    units: int = 0,
    model: str | None = None,
    context_digest: str | None = None,
) -> None:
    with tenant_session(envelope.tenant_id) as book:
        run = book.get(AgentRun, run_id)
        if run is None:  # pragma: no cover - só se alguém apagar o run no meio
            return
        run.status = status.value
        run.output = output or {}
        run.error = error
        run.units = units
        run.model = model
        run.context_digest = context_digest
        run.finished_at = datetime.now(UTC)

        if status is RunStatus.SUCCEEDED:
            definition = AGENT_DEFINITIONS[envelope.agent]
            usage.record_usage(
                book,
                tenant_id=envelope.tenant_id,
                kind=definition.usage_kind,
                campaign_id=envelope.campaign_id,
                agent_run_id=run_id,
                user_id=envelope.user_id,
            )
            action = "agent.run.succeeded"
            payload = {"agent": envelope.agent.value, "units": units}
        else:
            action = "agent.run.failed"
            payload = {"agent": envelope.agent.value, "error": error}

        audit.record(
            book,
            action=action,
            resource_type="agent_run",
            resource_id=run_id,
            payload=payload,
            context=ctx,
        )


def run_job(session: Session, envelope: JobEnvelope) -> AgentRun:
    """Executa um job dentro do contexto do tenant dono do envelope."""
    definition: AgentDefinition = AGENT_DEFINITIONS[envelope.agent]
    ctx = envelope.context()
    run_id = uuid.uuid4()

    with use_context(ctx):
        _open_run(envelope, run_id)
        try:
            units = usage.UNIT_COST[definition.usage_kind]
            usage.check_ai_budget(session, envelope.tenant_id, units)

            context = build_context(session, envelope)
            output = get_executor(envelope.agent.value)(context, envelope)
        except AppError as exc:
            # 402/403 são recusa de política (cota, isolamento); o resto é falha.
            status = (
                RunStatus.REJECTED if exc.status_code in (402, 403) else RunStatus.FAILED
            )
            _close_run(
                envelope,
                run_id,
                ctx,
                status=status,
                error=f"{exc.code}: {exc.message}",
            )
            raise

        _close_run(
            envelope,
            run_id,
            ctx,
            status=RunStatus.SUCCEEDED,
            output=output,
            units=units,
            model=context.agent["model"],
            context_digest=context.digest(),
        )

    session.expire_all()
    run = session.get(AgentRun, run_id)
    if run is None:  # pragma: no cover
        raise NotFound("Execução não encontrada após finalizar")
    return run


def enqueue(envelope: JobEnvelope) -> dict:
    """Ponto de entrada da fila.

    O broker (Sprint 3) recebe exatamente este payload — com tenant_id,
    job_id, user_id e campaign_id — e nunca um dicionário solto.
    """
    return envelope.to_dict()


def new_job(
    *,
    tenant_id: uuid.UUID,
    agent: str,
    campaign_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    params: dict | None = None,
) -> JobEnvelope:
    return JobEnvelope.from_dict(
        {
            "tenant_id": tenant_id,
            "agent": agent,
            "campaign_id": campaign_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "user_id": user_id,
            "params": params or {},
        }
    )
