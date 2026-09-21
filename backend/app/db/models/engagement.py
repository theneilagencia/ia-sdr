"""Sequências, mensagens, conversas, qualificação e reuniões."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class MessageDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class MessageStatus(StrEnum):
    DRAFT = "draft"
    #: Recusado por quem revisou. Fica no histórico: saber o que a IA escreveu
    #: e foi barrado vale tanto quanto saber o que saiu.
    REJECTED = "rejected"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    REPLIED = "replied"
    BOUNCED = "bounced"
    FAILED = "failed"


class Sequence(Base, TenantScoped, TimestampMixin):
    """Cadência de uma campanha: passos, canais e intervalos."""

    __tablename__ = "sequences"

    id: Mapped[uuid.UUID] = uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"


class SequenceEnrollment(Base, TenantScoped, TimestampMixin):
    """Onde cada prospect está dentro de uma cadência.

    Sem esta tabela, "follow-up automático" viraria uma varredura que recalcula
    tudo a cada ciclo e não tem como saber o que já mandou. O estado é por
    prospect porque é ele que avança — e porque parar a cadência de um sem
    parar a dos outros é a operação mais comum de todas.
    """

    __tablename__ = "sequence_enrollments"
    __table_args__ = (
        Index("ix_sequence_enrollments_tenant_created", "tenant_id", "created_at"),
        # O índice do tick: ativas cuja hora chegou.
        Index("ix_sequence_enrollments_due", "status", "next_run_at"),
        # Um prospect em duas cadências ativas receberia dois emails no mesmo
        # dia, de duas linhas de raciocínio diferentes. O banco recusa.
        Index(
            "uq_sequence_enrollment_ativa",
            "tenant_id",
            "prospect_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sequences.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("prospects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EnrollmentStatus.ACTIVE.value
    )
    #: Quantos passos já foram gerados. 0 = ainda não saiu nada.
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Quando o próximo passo pode ser gerado. Nulo quando não há próximo.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_step_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Por que parou. Fica gravado porque "o lead respondeu" e "o contato pediu
    #: descadastro" exigem respostas diferentes de quem opera.
    stop_reason: Mapped[str | None] = mapped_column(String(60))


class Conversation(Base, TenantScoped, TimestampMixin):
    """Fio de conversa com um prospect, em um canal."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="email")
    subject: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    external_thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_to_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class Message(Base, TenantScoped, TimestampMixin):
    """Mensagem individual. Histórico de conversa é conhecimento do tenant."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MessageStatus.DRAFT.value
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="email")
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    generated_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Qualification(Base, TenantScoped, TimestampMixin):
    """Resultado da qualificação: apto, não apto e por quê."""

    __tablename__ = "qualifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)  # qualified | disqualified
    criteria_results: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class Meeting(Base, TenantScoped, TimestampMixin):
    """A conversão que importa."""

    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = uuid_pk()
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    location: Mapped[str | None] = mapped_column(String(500))
    external_event_id: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
