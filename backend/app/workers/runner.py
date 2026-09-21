"""O worker: o que faz a plataforma trabalhar sem ninguém olhando.

Um ciclo faz três coisas, nesta ordem:

1. **Agenda o trabalho periódico** de cada empresa ativa — ler a caixa e
   despachar o que está aprovado. Com deduplicação, então um ciclo lento não
   acumula fila.
2. **Devolve à fila o que ficou preso**, de worker que morreu no meio.
3. **Executa os jobs disponíveis**, um de cada vez, cada um no contexto do seu
   tenant.

O que decide em qual empresa o trabalho acontece é o job, nunca o processo. É
a mesma regra do envelope dos agentes, e pelo mesmo motivo: worker que herda
contexto é worker que um dia executa com a configuração da empresa errada.
"""

from __future__ import annotations

import logging
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models.jobs import Job, JobKind
from app.db.models.platform import Tenant
from app.db.session import tenant_session, unscoped_session
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors import register_default_executors
from app.orchestrator.runner import run_job
from app.services import email_receiver, email_sender, jobs
from app.tenancy.context import system_context, use_context

logger = logging.getLogger("ia_sdr.worker")

INTERVALO_CICLO = 5  # segundos entre ciclos quando não há trabalho
INTERVALO_PERIODICO = timedelta(minutes=5)


def executar(tenant_id: uuid.UUID, kind: str, payload: dict) -> None:
    """Roda o trabalho dentro do tenant dono do job.

    Recebe os dados soltos, não o objeto do banco: o trabalho acontece numa
    sessão com escopo do tenant, e arrastar uma entidade da sessão
    administrativa para dentro dela só convidaria confusão.
    """
    ctx = system_context(tenant_id, source="worker")
    with use_context(ctx), tenant_session(tenant_id) as session:
        if kind == JobKind.AGENT_RUN.value:
            run_job(session, JobEnvelope.from_dict(payload))
        elif kind == JobKind.FETCH_INBOX.value:
            resultado = email_receiver.fetch_inbox(session, tenant_id=tenant_id, context=ctx)
            _despachar_conversas(session, tenant_id, resultado)
        elif kind == JobKind.SEND_QUEUED.value:
            email_sender.send_queued(session, tenant_id=tenant_id, context=ctx)
        else:
            raise ValueError(f"Tipo de job desconhecido: {kind}")


def _despachar_conversas(session, tenant_id: uuid.UUID, resultado: dict) -> None:
    """Resposta nova aciona o Conversation Agent.

    É aqui que o ciclo se fecha sozinho: o lead responde, o agente escreve a
    réplica, e ela entra na fila de revisão humana como qualquer outra.
    """
    if not resultado.get("recorded"):
        return
    from app.db.models.engagement import Conversation, Message, MessageDirection

    conversas = (
        session.execute(
            select(Conversation.id)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Message.direction == MessageDirection.INBOUND.value)
            .where(Message.created_at >= datetime.now(UTC) - timedelta(minutes=10))
            .distinct()
        )
        .scalars()
        .all()
    )
    for conversa_id in conversas:
        jobs.enqueue(
            session,
            tenant_id=tenant_id,
            kind=JobKind.AGENT_RUN,
            payload={
                "tenant_id": str(tenant_id),
                "agent": "conversation",
                "entity_type": "conversation",
                "entity_id": str(conversa_id),
            },
            dedupe_key=f"conversation:{conversa_id}",
        )


def agendar_periodicos(agora: datetime | None = None) -> int:
    """Enfileira leitura de caixa e envio para cada empresa ativa."""
    agora = agora or datetime.now(UTC)
    criados = 0
    with unscoped_session(reason="worker:listar-tenants") as admin:
        tenants = [
            t.id
            for t in admin.execute(select(Tenant).where(Tenant.is_active.is_(True)))
            .scalars()
            .all()
        ]

    for tenant_id in tenants:
        ctx = system_context(tenant_id, source="worker")
        with use_context(ctx), tenant_session(tenant_id) as session:
            for kind in (JobKind.FETCH_INBOX, JobKind.SEND_QUEUED):
                if jobs.enqueue(
                    session,
                    tenant_id=tenant_id,
                    kind=kind,
                    dedupe_key=kind.value,
                    run_at=agora,
                    max_attempts=1,  # periódico: falhou, o próximo ciclo tenta
                ):
                    criados += 1
    return criados


def ciclo() -> dict:
    """Um ciclo do worker. Devolve o que aconteceu, para o log e o teste."""
    with unscoped_session(reason="worker:retomar-presos") as admin:
        retomados = jobs.requeue_stale(admin)

    executados, falhados = 0, 0
    while True:
        with unscoped_session(reason="worker:reservar-job") as admin:
            job = jobs.claim_next(admin)
            if job is None:
                break
            dados = (job.id, job.tenant_id, job.kind, dict(job.payload))

        job_id, tenant_id, kind, payload = dados
        try:
            executar(tenant_id, kind, payload)
            with unscoped_session(reason="worker:concluir") as admin:
                jobs.complete(admin, admin.get(Job, job_id))
            executados += 1
        except Exception as exc:  # noqa: BLE001 - o worker não pode morrer por um job
            logger.exception(
                "job falhou", extra={"job_id": str(job_id), "tenant_id": str(tenant_id)}
            )
            with unscoped_session(reason="worker:registrar-falha") as admin:
                jobs.fail(admin, admin.get(Job, job_id), f"{type(exc).__name__}: {exc}")
            falhados += 1

    return {"requeued": retomados, "done": executados, "failed": falhados}


def main() -> int:  # pragma: no cover - laço de processo
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    register_default_executors()

    parar = False

    def _sinal(*_):
        nonlocal parar
        parar = True
        logger.info("encerrando após o ciclo atual")

    signal.signal(signal.SIGTERM, _sinal)
    signal.signal(signal.SIGINT, _sinal)

    logger.info("worker de pé")
    proximo_periodico = datetime.now(UTC)

    while not parar:
        agora = datetime.now(UTC)
        if agora >= proximo_periodico:
            criados = agendar_periodicos(agora)
            proximo_periodico = agora + INTERVALO_PERIODICO
            if criados:
                logger.info("trabalho periódico agendado", extra={"jobs": criados})

        resultado = ciclo()
        if resultado["done"] or resultado["failed"]:
            logger.info("ciclo concluído", extra=resultado)
        else:
            time.sleep(INTERVALO_CICLO)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
