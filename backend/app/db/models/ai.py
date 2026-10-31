"""Agentes de IA, execuções e integrações do tenant."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class AgentKind(StrEnum):
    RESEARCH = "research"
    OUTREACH = "outreach"
    CONVERSATION = "conversation"
    QUALIFICATION = "qualification"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class IntegrationProvider(StrEnum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    SMTP = "smtp"
    GOOGLE_CALENDAR = "google_calendar"
    #: O CRM é o RAVI, que já existe e já é o sistema de registro do lead. Esta
    #: plataforma não tem CRM próprio nem vai ter: duas bases com o mesmo lead
    #: divergem em uma semana, e a partir daí ninguém sabe qual está certa.
    RAVI = "ravi"


class AIAgent(Base, TenantScoped, TimestampMixin):
    """Configuração de um agente dentro de um tenant.

    O motor é o mesmo para todos os clientes; o que muda é a configuração e o
    contexto que o orquestrador monta.
    """

    __tablename__ = "ai_agents"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "name", name="uq_agent_tenant_kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="claude-opus-5")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    temperature: Mapped[int] = mapped_column(Integer, nullable=False, default=30)  # 0-100
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    tools: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    guardrails: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AgentRun(Base, TenantScoped, TimestampMixin):
    """Uma execução de agente: entrada, saída, custo e rastro.

    Todo job assíncrono carrega tenant_id, job_id, user_id e campaign_id. É o
    que impede o worker de pegar um job do tenant A e executar com
    configuração do tenant B.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RunStatus.PENDING.value, index=True
    )
    context_digest: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Integration(Base, TenantScoped, TimestampMixin):
    """Credenciais do cliente, cifradas em repouso.

    `credentials_encrypted` nunca sai do backend em claro e nunca é serializada
    em resposta de API.
    """

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "account_ref", name="uq_integration_account"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    account_ref: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected")
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
