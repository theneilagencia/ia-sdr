"""Tenants, usuários, vínculos, auditoria e consumo."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class Plan(StrEnum):
    STARTER = "starter"
    GROWTH = "growth"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    SUSPENDED = "suspended"


class Tenant(Base, TimestampMixin):
    """A empresa cliente. Raiz de todo isolamento."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default=Plan.STARTER.value)
    subscription_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SubscriptionStatus.TRIAL.value
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Overrides de limite por contrato; o default vem de app.billing.plans
    limit_overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class User(Base, TimestampMixin):
    """Identidade global. Um usuário pode pertencer a mais de um tenant."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Trocar a senha encerra as sessões abertas: qualquer token emitido antes
    #: deste instante é recusado. Sem isso, "troquei a senha" só valeria a
    #: partir da expiração do token que já estava na mão de quem invadiu.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ------------------------------------------------- segundo fator (TOTP)
    #
    # Fica no usuário, e não no vínculo, porque a identidade é global: quem
    # serve duas empresas protege uma conta, não duas. E o segredo é credencial
    # como qualquer outra desta plataforma — cifrado com a chave Fernet, nunca
    # devolvido depois de mostrado uma vez.
    mfa_secret: Mapped[str | None] = mapped_column(String(500))
    #: Nulo enquanto o segundo fator não foi confirmado com um código de
    #: verdade. Gerar segredo não liga nada: quem liga é a prova de que o
    #: aplicativo do outro lado funciona — senão a pessoa se tranca fora.
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: O último passo de trinta segundos aceito. É o que recusa reuso: sem
    #: guardar, um código interceptado vale a janela inteira para quem o pegou.
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger)
    #: SHA-256 dos códigos de recuperação. Hash rápido de propósito, como o
    #: token de convite: são vinte bytes de aleatório, não senha escolhida por
    #: gente. Cada um serve uma vez e sai da lista.
    mfa_recovery_hashes: Mapped[list | None] = mapped_column(JSONB)
    #: Seis dígitos são um milhão de combinações, e quem chega aqui já acertou a
    #: senha. Sem contar as tentativas, o segundo fator é uma porta com
    #: cadeado que aceita chute infinito.
    mfa_failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mfa_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Invitation(Base, TenantScoped, TimestampMixin):
    """Convite para entrar numa empresa, aceito por quem recebe.

    O que muda em relação a criar a pessoa direto: **quem escolhe a senha é ela**.
    Antes um admin digitava a senha de outra pessoa e descobria, pela resposta, se
    aquele email já tinha conta na plataforma — enumeração de base de usuários, de
    severidade baixa, mas cujo conserto certo nunca foi esconder a resposta: é não
    precisar dela.

    O token não é guardado, só o hash. Convite pendente lido no banco não abre
    porta nenhuma — a mesma regra da senha. E `accepted_at` fica: saber que o
    convite foi aceito, por qual email e quando, é parte do histórico de quem
    entrou na empresa.
    """

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    #: SHA-256 do token. Hash rápido, e de propósito: o token é aleatório de 32
    #: bytes, então não há o que uma função lenta proteja aqui.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(Base, TimestampMixin):
    """Usuário X pertence ao tenant Y com o papel Z."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AuditLog(Base, TenantScoped, TimestampMixin):
    """Quem fez o quê, em qual tenant, sobre qual recurso."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="api")
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class UsageEvent(Base, TenantScoped, TimestampMixin):
    """Consumo por operação. Base de margem e de enforcement de limite."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_usage_events_tenant_kind", "tenant_id", "kind"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Custo real estimado em milésimos de centavo de dólar, para não usar float
    cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
