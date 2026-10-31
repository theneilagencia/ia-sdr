"""Campanhas, contas-alvo, prospects e inteligência comercial."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ProspectStatus(StrEnum):
    NEW = "new"
    RESEARCHING = "researching"
    RESEARCHED = "researched"
    SCORED = "scored"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    MEETING_BOOKED = "meeting_booked"
    DISQUALIFIED = "disqualified"
    BOUNCED = "bounced"


class Company(Base, TenantScoped, TimestampMixin):
    """Conta-alvo. Pertence ao tenant que a pesquisou — não é global."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    industry: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(120))
    employee_count: Mapped[int | None] = mapped_column(Integer)
    revenue_band: Mapped[str | None] = mapped_column(String(80))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Contact(Base, TenantScoped, TimestampMixin):
    """Pessoa dentro de uma conta-alvo."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(60))
    title: Mapped[str | None] = mapped_column(String(200))
    seniority: Mapped[str | None] = mapped_column(String(60))
    persona: Mapped[str | None] = mapped_column(String(120))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    timezone: Mapped[str | None] = mapped_column(String(60))
    opted_out: Mapped[bool] = mapped_column(nullable=False, default=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Campaign(Base, TenantScoped, TimestampMixin):
    """Uma campanha é o contexto operacional de tudo que a IA faz.

    ICP, geografia, personas, oferta, mensagem, critérios de qualificação e
    limites diários vivem aqui — para o agente não confundir a campanha de CFO
    no Canadá com a de COO no Brasil.
    """

    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_campaign_tenant_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CampaignStatus.DRAFT.value
    )
    objective: Mapped[str | None] = mapped_column(Text)
    icp: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    target_geography: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    personas: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    offer: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    messaging: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    qualification_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    channels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    daily_limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class Prospect(Base, TenantScoped, TimestampMixin):
    """Contato dentro de uma campanha, com seu estado no funil."""

    __tablename__ = "prospects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "campaign_id", "contact_id", name="uq_prospect_campaign"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProspectStatus.NEW.value, index=True
    )
    source: Mapped[str | None] = mapped_column(String(80))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Research(Base, TenantScoped, TimestampMixin):
    """Saída do Research Agent sobre uma conta ou pessoa."""

    __tablename__ = "research"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)  # company | contact
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    depth: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    summary: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class Score(Base, TenantScoped, TimestampMixin):
    """Aderência ao ICP da campanha, com o porquê registrado."""

    __tablename__ = "scores"

    id: Mapped[uuid.UUID] = uuid_pk()
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("prospects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    value: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    band: Mapped[str | None] = mapped_column(String(20))  # A | B | C | D
    rationale: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scored_on: Mapped[date | None] = mapped_column(Date)
