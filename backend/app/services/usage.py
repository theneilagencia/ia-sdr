"""Medição de consumo e enforcement de limites.

Cada operação de IA gera um evento de consumo. Isso responde três perguntas
que uma plataforma de IA não pode deixar em aberto: quanto o tenant usou,
quanto custou e se ele ainda pode usar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.plans import UNLIMITED, limits_for
from app.core.errors import LimitExceeded, NotFound
from app.db.models.platform import Tenant, UsageEvent


class UsageKind(StrEnum):
    RESEARCH = "research"
    DEEP_RESEARCH = "deep_research"
    AI_MESSAGE = "ai_message"
    QUALIFICATION = "qualification"
    VOICE_INTERACTION = "voice_interaction"
    PROSPECT_IMPORTED = "prospect_imported"


#: Unidades por operação. É a moeda interna da plataforma.
UNIT_COST: dict[UsageKind, int] = {
    UsageKind.RESEARCH: 1,
    UsageKind.AI_MESSAGE: 1,
    UsageKind.DEEP_RESEARCH: 5,
    UsageKind.QUALIFICATION: 2,
    UsageKind.VOICE_INTERACTION: 10,
    UsageKind.PROSPECT_IMPORTED: 0,
}

#: Só contam contra a cota de IA; import de prospect tem cota própria.
_AI_KINDS = frozenset(k for k, cost in UNIT_COST.items() if cost > 0)


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def units_used_this_month(session: Session, tenant_id: uuid.UUID) -> int:
    stmt = (
        select(func.coalesce(func.sum(UsageEvent.units), 0))
        .where(UsageEvent.tenant_id == tenant_id)
        .where(UsageEvent.created_at >= _month_start())
        .where(UsageEvent.kind.in_([k.value for k in _AI_KINDS]))
    )
    return int(session.execute(stmt).scalar_one())


def cost_micro_usd_this_month(session: Session, tenant_id: uuid.UUID) -> int:
    """Quanto a IA deste tenant custou de verdade no mês, em micro-dólares.

    Soma **todos** os eventos, inclusive os de zero unidade: execução que falhou
    depois de chamar o modelo gasta dólar e não gasta unidade nenhuma. Era
    exatamente esse gasto que não tinha freio: a cota do plano conta unidade, e
    um agente que falha em série nunca a consumia.
    """
    stmt = (
        select(func.coalesce(func.sum(UsageEvent.cost_micro_usd), 0))
        .where(UsageEvent.tenant_id == tenant_id)
        .where(UsageEvent.created_at >= _month_start())
    )
    return int(session.execute(stmt).scalar_one())


def count_this_month(session: Session, tenant_id: uuid.UUID, kind: UsageKind) -> int:
    stmt = (
        select(func.coalesce(func.sum(UsageEvent.quantity), 0))
        .where(UsageEvent.tenant_id == tenant_id)
        .where(UsageEvent.kind == kind.value)
        .where(UsageEvent.created_at >= _month_start())
    )
    return int(session.execute(stmt).scalar_one())


def _tenant(session: Session, tenant_id: uuid.UUID) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound("Tenant não encontrado")
    return tenant


def effective_limits(session: Session, tenant_id: uuid.UUID) -> dict:
    tenant = _tenant(session, tenant_id)
    return limits_for(tenant.plan, tenant.limit_overrides)


def check_ai_budget(session: Session, tenant_id: uuid.UUID, units: int) -> None:
    """Chamado antes de gastar. Estourou a cota, não roda o agente.

    São dois freios, e eles medem coisas diferentes. A **unidade** é a moeda que
    o cliente compra, e a conta é exata: sabe-se de antemão quantas unidades a
    operação custa. O **dólar** é o que a Anthropic cobra, e aí não se sabe: o
    custo de uma execução só existe depois dela. Por isso a regra do teto em
    dólar é "já passou, não começa outra" — o excesso possível é de uma execução,
    e essa está limitada pelo teto por execução (`ai_max_cost_micro_usd`).
    """
    limits = effective_limits(session, tenant_id)

    limit = limits["ai_units_per_month"]
    if limit != UNLIMITED:
        used = units_used_this_month(session, tenant_id)
        if used + units > limit:
            raise LimitExceeded(
                "Cota mensal de IA esgotada para este tenant",
                details={"limit": limit, "used": used, "requested": units},
            )

    teto_usd = limits.get("ai_cost_usd_per_month", UNLIMITED)
    if teto_usd == UNLIMITED:
        return
    gasto = cost_micro_usd_this_month(session, tenant_id)
    if gasto >= int(teto_usd) * 1_000_000:
        raise LimitExceeded(
            "Teto mensal de custo de IA atingido para este tenant",
            details={
                "limit_usd": int(teto_usd),
                "spent_usd": round(gasto / 1e6, 4),
                "kind": "cost",
            },
        )


def record_usage(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: UsageKind,
    quantity: int = 1,
    campaign_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    cost_micro_usd: int = 0,
    notes: str | None = None,
) -> UsageEvent:
    units = UNIT_COST[kind] * quantity
    event = UsageEvent(
        tenant_id=tenant_id,
        kind=kind.value,
        units=units,
        quantity=quantity,
        cost_micro_usd=cost_micro_usd,
        campaign_id=campaign_id,
        agent_run_id=agent_run_id,
        user_id=user_id,
        notes=notes,
    )
    session.add(event)
    session.flush()
    return event


def usage_summary(session: Session, tenant_id: uuid.UUID) -> dict:
    stmt = (
        select(
            UsageEvent.kind,
            func.sum(UsageEvent.units),
            func.sum(UsageEvent.quantity),
            func.sum(UsageEvent.cost_micro_usd),
        )
        .where(UsageEvent.tenant_id == tenant_id)
        .where(UsageEvent.created_at >= _month_start())
        .group_by(UsageEvent.kind)
    )
    by_kind = {
        kind: {"units": int(units or 0), "quantity": int(qty or 0), "cost_usd": (cost or 0) / 1e6}
        for kind, units, qty, cost in session.execute(stmt).all()
    }
    limits = effective_limits(session, tenant_id)
    used = units_used_this_month(session, tenant_id)
    return {
        "period_start": _month_start().isoformat(),
        "ai_units_used": used,
        "ai_units_limit": limits["ai_units_per_month"],
        "estimated_cost_usd": round(cost_micro_usd_this_month(session, tenant_id) / 1e6, 4),
        "estimated_cost_limit_usd": limits.get("ai_cost_usd_per_month", UNLIMITED),
        "by_kind": by_kind,
        "limits": limits,
    }
