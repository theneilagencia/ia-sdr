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

    # ------------------------------------------------------------- contrato
    #
    # Nasce zerado nos três casos, e isso é decisão: quanto custa o serviço é
    # número comercial de quem opera a plataforma, não default para o código
    # inventar. Com zero, o fechamento do mês continua fechando — ele só não
    # cobra nada, e mostra o consumo, que é o que o operador precisa ver antes
    # de decidir o preço.
    #: Valor mensal contratado, em centavos.
    contract_monthly_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Moeda do contrato. BRL por padrão porque é onde a plataforma opera; o
    #: painel troca. Guardada aqui e não numa constante global porque nada
    #: impede dois clientes em moedas diferentes.
    contract_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    #: Preço da unidade de IA além da cota do plano, em centavos. Zero significa
    #: "não cobra excedente" — o limite do plano já recusa a execução, então
    #: excedente só existe se o limite tiver sido levantado por contrato.
    overage_cents_per_unit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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


class InvoiceStatus(StrEnum):
    """O ciclo de uma fatura, e cada passo existe por um motivo operacional.

    * `draft` — o fechamento rodou e os números estão prontos. Nada foi cobrado,
      e refazer o fechamento atualiza estes valores.
    * `issued` — a cobrança foi mandada ao cliente (boleto, PIX, nota). A partir
      daqui o fechamento **não** mexe mais nos números: fatura que muda depois de
      emitida é discussão com o cliente, não correção de sistema.
    * `paid` — entrou.
    * `void` — anulada. Existe para não apagar: o histórico de uma cobrança
      cancelada é exatamente o que alguém vai querer conferir depois.
    """

    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    VOID = "void"


class Invoice(Base, TenantScoped, TimestampMixin):
    """O fechamento de um mês por empresa: o que consumiu e o que se cobra.

    A plataforma media tudo — unidades, micro-dólares por modelo, por agente — e
    não tinha como transformar isso em cobrança. Faltava o passo que um gateway
    **não** resolve: no Brasil, quem emite nota fiscal é o contador ou um serviço
    de NFe, então o gateway é conveniência de recebimento, não o que destrava
    faturar.

    Os números ficam congelados na linha, e não recalculados na leitura, porque
    fatura é documento: o consumo do mês passado precisa continuar dizendo o que
    dizia quando foi emitida, mesmo que a tabela de preços mude depois.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_tenant_created", "tenant_id", "created_at"),
        # Um fechamento por empresa por mês. Sem isto, rodar o fechamento duas
        # vezes cria duas cobranças do mesmo período — e a segunda parece
        # legítima.
        UniqueConstraint("tenant_id", "period_year", "period_month", name="uq_invoices_periodo"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=InvoiceStatus.DRAFT.value
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    #: O valor do contrato no momento do fechamento.
    subscription_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Unidades de IA consumidas no mês, e quantas passaram da cota.
    ai_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_units_included: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_units_over: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overage_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: O que a Anthropic cobrou de verdade, em micro-dólares. Não entra na conta
    #: do cliente: é a margem, e é o número que diz se o contrato faz sentido.
    ai_cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


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
