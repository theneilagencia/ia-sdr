"""Limites estruturais do plano (campanhas, usuários, contas de email)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.plans import UNLIMITED
from app.core.errors import LimitExceeded
from app.db.models.ai import Integration
from app.db.models.knowledge import KnowledgeDocument
from app.db.models.platform import Membership
from app.db.models.sales import Campaign
from app.services.usage import effective_limits

_EMAIL_PROVIDERS = ("gmail", "outlook", "smtp")


def _count(session: Session, stmt) -> int:
    return int(session.execute(stmt).scalar_one())


def _enforce(limit: int, current: int, resource: str) -> None:
    if limit == UNLIMITED:
        return
    if current >= limit:
        raise LimitExceeded(
            f"Limite do plano atingido para {resource}",
            details={"resource": resource, "limit": limit, "current": current},
        )


def check_can_create_campaign(session: Session, tenant_id: uuid.UUID) -> None:
    limits = effective_limits(session, tenant_id)
    current = _count(
        session,
        select(func.count(Campaign.id))
        .where(Campaign.tenant_id == tenant_id)
        .where(Campaign.status != "archived"),
    )
    _enforce(limits["campaigns"], current, "campaigns")


def check_can_add_user(session: Session, tenant_id: uuid.UUID) -> None:
    limits = effective_limits(session, tenant_id)
    current = _count(
        session,
        select(func.count(Membership.id))
        .where(Membership.tenant_id == tenant_id)
        .where(Membership.is_active.is_(True)),
    )
    _enforce(limits["users"], current, "users")


def check_can_add_email_account(session: Session, tenant_id: uuid.UUID) -> None:
    limits = effective_limits(session, tenant_id)
    current = _count(
        session,
        select(func.count(Integration.id))
        .where(Integration.tenant_id == tenant_id)
        .where(Integration.provider.in_(_EMAIL_PROVIDERS)),
    )
    _enforce(limits["email_accounts"], current, "email_accounts")


def check_can_add_document(session: Session, tenant_id: uuid.UUID) -> None:
    limits = effective_limits(session, tenant_id)
    current = _count(
        session,
        select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.tenant_id == tenant_id),
    )
    _enforce(limits["knowledge_documents"], current, "knowledge_documents")
