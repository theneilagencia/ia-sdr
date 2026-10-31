"""Enfileirar, reservar e concluir trabalho.

O claim usa `FOR UPDATE SKIP LOCKED`: dois workers podem rodar lado a lado sem
pegar o mesmo job e sem um travar o outro. É o padrão que faz o Postgres servir
de fila sem broker.

Duas escolhas que evitam problema crônico:

* **Reserva e execução na mesma transação.** O job só é marcado como concluído
  junto com o resultado do trabalho. Não existe "trabalho feito, job perdido".
* **Deduplicação por chave.** Ler a caixa de uma empresa é sempre o mesmo
  trabalho: duas leituras pendentes ao mesmo tempo não adiantam nada e só
  gastam conexão.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.jobs import Job, JobKind, JobStatus

#: Espera antes de tentar de novo, por número de tentativas já feitas.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30))

#: Job reservado que não terminou neste prazo é considerado órfão — worker
#: morto no meio, container reiniciado. Volta para a fila.
STALE_AFTER = timedelta(minutes=15)


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: JobKind | str,
    payload: dict | None = None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> Job | None:
    """Põe um job na fila. Devolve None se já existe um igual esperando."""
    tipo = kind.value if isinstance(kind, JobKind) else kind

    if dedupe_key:
        existente = session.execute(
            select(Job)
            .where(Job.tenant_id == tenant_id)
            .where(Job.dedupe_key == dedupe_key)
            .where(Job.status.in_((JobStatus.PENDING.value, JobStatus.RUNNING.value)))
            .limit(1)
        ).scalar_one_or_none()
        if existente is not None:
            return None

    job = Job(
        tenant_id=tenant_id,
        kind=tipo,
        payload=payload or {},
        run_at=run_at or datetime.now(UTC),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session, *, agora: datetime | None = None) -> Job | None:
    """Reserva o próximo job disponível, de qualquer tenant.

    Roda na sessão administrativa: o worker atende todas as empresas, e é o
    job que diz em qual tenant o trabalho acontece.
    """
    agora = agora or datetime.now(UTC)
    linha = session.execute(
        text(
            """
            SELECT id FROM jobs
            WHERE status = 'pending' AND run_at <= :agora
            ORDER BY run_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ),
        {"agora": agora},
    ).first()
    if linha is None:
        return None

    job = session.get(Job, linha[0])
    job.status = JobStatus.RUNNING.value
    job.attempts += 1
    job.started_at = agora
    session.flush()
    return job


def complete(session: Session, job: Job, *, agora: datetime | None = None) -> None:
    job.status = JobStatus.DONE.value
    job.finished_at = agora or datetime.now(UTC)
    job.last_error = None
    session.flush()


def fail(
    session: Session,
    job: Job,
    erro: str,
    *,
    agora: datetime | None = None,
    retry: bool = True,
) -> None:
    """Marca a falha e reagenda, até acabarem as tentativas.

    `retry=False` encerra o job na primeira falha. É para o que não muda de
    resposta na segunda tentativa: recusa de política — cota estourada, contato
    descadastrado, teto de custo, isolamento de tenant. Insistir nesses casos não
    conserta nada e, quando a recusa vem **depois** da chamada ao modelo, cada
    tentativa paga a conta de novo.
    """
    agora = agora or datetime.now(UTC)
    job.last_error = erro[:1000]
    if not retry or job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED.value
        job.finished_at = agora
    else:
        job.status = JobStatus.PENDING.value
        espera = BACKOFF[min(job.attempts - 1, len(BACKOFF) - 1)]
        job.run_at = agora + espera
    session.flush()


def requeue_stale(session: Session, *, agora: datetime | None = None) -> int:
    """Devolve à fila o que ficou preso em 'running'.

    Sem isso, um worker que morre no meio leva o job junto: ele fica reservado
    para sempre e ninguém percebe.
    """
    agora = agora or datetime.now(UTC)
    limite = agora - STALE_AFTER
    presos = list(
        session.execute(
            select(Job).where(Job.status == JobStatus.RUNNING.value).where(Job.started_at < limite)
        ).scalars()
    )
    for job in presos:
        job.status = JobStatus.PENDING.value
        job.run_at = agora
        job.last_error = "retomado: worker não concluiu a tempo"
    session.flush()
    return len(presos)
