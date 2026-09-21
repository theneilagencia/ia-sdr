"""Sequências, mensagens, conversas, qualificação e reuniões."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class MessageDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class MessageStatus(StrEnum):
    DRAFT = "draft"
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
