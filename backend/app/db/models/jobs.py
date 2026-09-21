"""Fila de trabalho.

Postgres como fila, não Redis. O motivo não é preguiça: a transação que grava
o resultado do agente e a que marca o job como concluído passam a ser a mesma,
então não existe o estado "trabalho feito, job perdido" nem o contrário. Com
broker separado, isso exige idempotência em tudo; aqui sai de graça.

Quando o volume justificar um broker de verdade, o que muda é o claim —
`app/services/jobs.py` — e nada mais.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class JobKind(StrEnum):
    AGENT_RUN = "agent_run"
    FETCH_INBOX = "fetch_inbox"
    SEND_QUEUED = "send_queued"
    #: Avança as cadências cuja hora chegou. Não manda email: gera o rascunho
    #: do próximo toque, que segue para a fila de revisão como qualquer outro.
    SEQUENCE_TICK = "sequence_tick"
    #: Empurra para o RAVI os prospects que mudaram. O CRM é lá; aqui é o motor.
    RAVI_SYNC = "ravi_sync"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base, TenantScoped, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_tenant_created", "tenant_id", "created_at"),
        # O índice que o claim usa: pendentes cuja hora chegou, mais velhos primeiro.
        Index("ix_jobs_claim", "status", "run_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.PENDING.value
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: Quando pode rodar. É o que permite adiar uma tentativa depois de falhar.
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Chave de deduplicação: impede duas leituras de caixa pendentes ao mesmo
    #: tempo para o mesmo tenant.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)
