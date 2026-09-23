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

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.ai import AgentRun, RunStatus
from app.db.session import tenant_session
from app.orchestrator.agents import AGENT_DEFINITIONS, AgentDefinition
from app.orchestrator.context_builder import build_context
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors.base import AgentExecutor, ExecutionResult, gasto_de
from app.orchestrator.executors.echo import echo_executor
from app.services import audit, jobs, usage
from app.tenancy.context import TenantContext, use_context

logger = logging.getLogger("ia_sdr.orchestrator")

_EXECUTORS: dict[str, AgentExecutor] = {}


def register_executor(kind: str, executor: AgentExecutor) -> None:
    _EXECUTORS[kind] = executor


def get_executor(kind: str) -> AgentExecutor:
    """Sem executor registrado, o eco mantém o caminho exercitável."""
    return _EXECUTORS.get(kind, echo_executor)


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
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_micro_usd: int = 0,
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
        run.input_tokens = input_tokens
        run.output_tokens = output_tokens
        run.finished_at = datetime.now(UTC)

        definition = AGENT_DEFINITIONS[envelope.agent]
        if status is RunStatus.SUCCEEDED:
            usage.record_usage(
                book,
                tenant_id=envelope.tenant_id,
                kind=definition.usage_kind,
                campaign_id=envelope.campaign_id,
                agent_run_id=run_id,
                user_id=envelope.user_id,
                cost_micro_usd=cost_micro_usd,
            )
            action = "agent.run.succeeded"
            payload = {
                "agent": envelope.agent.value,
                "units": units,
                "cost_micro_usd": cost_micro_usd,
                "model": model,
            }
        else:
            if cost_micro_usd:
                # Falhou depois de chamar o modelo: o token já foi gasto e a
                # Anthropic já cobrou. Sem este evento, o gasto de execução que
                # falha desaparece da contabilidade — o run ficava com zero
                # token, o consumo do mês não registrava nada e a fila ainda
                # tentava o mesmo job mais duas vezes, cada tentativa invisível
                # do mesmo jeito. Custo real entra; **unidade não**, porque o
                # cliente não paga cota por trabalho que não foi entregue.
                usage.record_usage(
                    book,
                    tenant_id=envelope.tenant_id,
                    kind=definition.usage_kind,
                    quantity=0,
                    campaign_id=envelope.campaign_id,
                    agent_run_id=run_id,
                    user_id=envelope.user_id,
                    cost_micro_usd=cost_micro_usd,
                    notes=f"execução {status.value}: {error}"[:500],
                )
            action = "agent.run.failed"
            payload = {
                "agent": envelope.agent.value,
                "error": error,
                "cost_micro_usd": cost_micro_usd,
            }

        audit.record(
            book,
            action=action,
            resource_type="agent_run",
            resource_id=run_id,
            payload=payload,
            context=ctx,
        )


def _run_existente(envelope: JobEnvelope) -> AgentRun | None:
    """A execução que este mesmo envelope já produziu, se produziu.

    O `job_id` do envelope é estável — sobrevive a requeue, porque vive no
    payload do job. Isso torna a execução idempotente, e é o que protege o caso
    real: um job de agente que passa de quinze minutos é considerado órfão pelo
    `requeue_stale` e volta para a fila **enquanto ainda roda**. Sem esta
    verificação, o segundo worker pesquisaria a mesma conta de novo, pagaria a
    conta de novo e deixaria dois rascunhos quase iguais na fila de revisão.

    Só uma execução que **falhou** pode ser refeita: é exatamente para isso que
    a fila tem tentativas.

    E execução abandonada conta como falha. Um run fica em `running` quando o
    processo morre no meio — SIGKILL, container reciclado, banco caído. Passado
    o prazo de órfão da fila, ninguém está do outro lado dele: tratá-lo como
    "já entregue" fazia a tentativa seguinte devolver o run parado e marcar o
    job como concluído sem ter feito nada. O trabalho sumia em silêncio, que é
    pior do que falhar.
    """
    with tenant_session(envelope.tenant_id) as book:
        anterior = book.execute(
            select(AgentRun)
            .where(AgentRun.job_id == envelope.job_id)
            .where(AgentRun.status != RunStatus.FAILED.value)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if anterior is None:
            return None

        if anterior.status == RunStatus.RUNNING.value and _abandonado(anterior):
            logger.warning(
                "agent.run.abandonado run_id=%s — processo morreu no meio, refazendo",
                anterior.id,
            )
            anterior.status = RunStatus.FAILED.value
            anterior.error = "abandonado: o processo não concluiu a execução"
            anterior.finished_at = datetime.now(UTC)
            return None
        return anterior


def _abandonado(run: AgentRun) -> bool:
    limite = datetime.now(UTC) - jobs.STALE_AFTER
    inicio = run.started_at
    if inicio is None:
        return True
    if inicio.tzinfo is None:  # pragma: no cover - coluna é timestamptz
        inicio = inicio.replace(tzinfo=UTC)
    return inicio < limite


def run_job(session: Session, envelope: JobEnvelope) -> AgentRun:
    """Executa um job dentro do contexto do tenant dono do envelope."""
    definition: AgentDefinition = AGENT_DEFINITIONS[envelope.agent]
    ctx = envelope.context()

    with use_context(ctx):
        anterior = _run_existente(envelope)
    if anterior is not None:
        logger.warning(
            "agent.run.ja_executado job_id=%s status=%s — entrega repetida, não roda de novo",
            envelope.job_id,
            anterior.status,
        )
        return anterior

    run_id = uuid.uuid4()

    with use_context(ctx):
        _open_run(envelope, run_id)
        try:
            units = usage.UNIT_COST[definition.usage_kind]
            usage.check_ai_budget(session, envelope.tenant_id, units)

            context = build_context(session, envelope)
            result: ExecutionResult = get_executor(envelope.agent.value)(session, context, envelope)
        except Exception as exc:
            # Qualquer exceção fecha o run. Antes só as de domínio eram tratadas,
            # e o erro mais provável em produção não é de domínio: é o SDK da
            # Anthropic levantando o seu (corte de conexão, 429, 500 do outro
            # lado). O run ficava em `running` para sempre e — por ser um run
            # não-falho com o mesmo `job_id` — a tentativa seguinte o devolvia
            # como "já executado" e o job era marcado como concluído. O trabalho
            # sumia, e a tela dizia que estava tudo bem.
            if isinstance(exc, AppError):
                # 402/403 são recusa de política (cota, isolamento); o resto é falha.
                status = RunStatus.REJECTED if exc.status_code in (402, 403) else RunStatus.FAILED
                erro = f"{exc.code}: {exc.message}"
            else:
                status = RunStatus.FAILED
                erro = f"{type(exc).__name__}: {exc}"
            # A exceção pode vir carregando o que já foi gasto antes de falhar.
            gasto = gasto_de(exc)
            _close_run(
                envelope,
                run_id,
                ctx,
                status=status,
                error=erro,
                model=gasto.model if gasto else None,
                input_tokens=gasto.input_tokens if gasto else 0,
                output_tokens=gasto.output_tokens if gasto else 0,
                cost_micro_usd=gasto.cost_micro_usd if gasto else 0,
            )
            raise

        _close_run(
            envelope,
            run_id,
            ctx,
            status=RunStatus.SUCCEEDED,
            output=result.output,
            units=units,
            model=result.model or context.agent["model"],
            context_digest=context.digest(),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_micro_usd=result.cost_micro_usd,
        )

    # O executor pode ter gravado dado de domínio (a pesquisa, no caso do
    # Research Agent). Fecha a transação antes de reler o run.
    session.commit()
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
