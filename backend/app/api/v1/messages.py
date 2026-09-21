"""Fila de revisão: o portão humano entre a IA e a caixa de entrada de alguém.

Os agentes escrevem rascunhos. Nada sai daqui sem uma pessoa aprovar — e a
recusa fica registrada, porque saber o que a IA escreveu e foi barrado vale
tanto quanto saber o que saiu.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.db.models.engagement import Message, MessageStatus
from app.db.models.platform import Tenant
from app.rbac.roles import Permission
from app.services import audit, email_sender
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/messages", tags=["messages"])


def _draft_or_404(db: Session, message_id: uuid.UUID) -> Message:
    mensagem = db.get(Message, message_id)
    if mensagem is None:
        raise NotFound("Mensagem não encontrada")
    if mensagem.status != MessageStatus.DRAFT.value:
        raise ConflictError(
            f"Mensagem já está em '{mensagem.status}': só rascunho pode ser revisado"
        )
    return mensagem


@router.get("", response_model=list[schemas.MessageResponse])
def list_messages(
    status_filter: str = Query(default=MessageStatus.DRAFT.value, alias="status"),
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_READ)),
    db: Session = Depends(get_db),
):
    """Por padrão devolve a fila de rascunhos — o que espera revisão."""
    return list(
        db.execute(
            select(Message)
            .where(Message.status == status_filter)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).scalars()
    )


@router.post("/{message_id}/approve", response_model=schemas.MessageResponse)
def approve(
    message_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Aprova o rascunho e o coloca na fila de envio.

    Aprovar não envia: o envio é do integrador de email. Mas a partir daqui a
    responsabilidade pelo texto é de quem aprovou, e o registro diz quem foi.
    """
    mensagem = _draft_or_404(db, message_id)
    mensagem.status = MessageStatus.QUEUED.value
    mensagem.metrics = {
        **(mensagem.metrics or {}),
        "approved_by": str(ctx.user_id),
        "approved_at": datetime.now(UTC).isoformat(),
    }
    audit.record(
        db,
        action="message.approved",
        resource_type="message",
        resource_id=mensagem.id,
        payload={"conversation_id": str(mensagem.conversation_id)},
        context=ctx,
    )
    return mensagem


@router.post("/{message_id}/reject", response_model=schemas.MessageResponse)
def reject(
    message_id: uuid.UUID,
    payload: schemas.MessageReject,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Recusa o rascunho, com motivo.

    O motivo não é burocracia: é o sinal mais barato que existe para descobrir
    onde os agentes erram, antes de ter dado de conversão.
    """
    mensagem = _draft_or_404(db, message_id)
    mensagem.status = MessageStatus.REJECTED.value
    mensagem.metrics = {
        **(mensagem.metrics or {}),
        "rejected_by": str(ctx.user_id),
        "rejected_at": datetime.now(UTC).isoformat(),
        "rejection_reason": payload.reason,
    }
    audit.record(
        db,
        action="message.rejected",
        resource_type="message",
        resource_id=mensagem.id,
        payload={"reason": payload.reason},
        context=ctx,
    )
    return mensagem


@router.get("/allowance", response_model=schemas.AllowanceResponse)
def allowance(
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_READ)),
    db: Session = Depends(get_db),
):
    """Quantos envios cabem hoje, e por quê esse número."""
    tenant = db.get(Tenant, ctx.tenant_id)
    cota = email_sender.allowance_today(db, tenant)
    return schemas.AllowanceResponse(
        **cota, within_business_hours=email_sender.within_business_hours(tenant)
    )


@router.post("/{message_id}/send", response_model=schemas.MessageResponse)
def send(
    message_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Envia uma mensagem já aprovada, respeitando cota e horário."""
    return email_sender.send_message(db, tenant_id=ctx.tenant_id, message_id=message_id)


@router.post("/send-queued", response_model=schemas.SendQueuedResult)
def send_queued(
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Envia a fila aprovada até a cota do dia acabar."""
    return email_sender.send_queued(db, tenant_id=ctx.tenant_id)


@router.post("/{message_id}/requeue", response_model=schemas.MessageResponse)
def requeue(
    message_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Devolve para a fila uma mensagem que falhou no envio.

    Falha de rede ou servidor fora do ar não deveria exigir SQL para se
    recuperar. A aprovação continua valendo: quem aprovou o texto aprovou
    este texto, e ele não mudou.
    """
    mensagem = db.get(Message, message_id)
    if mensagem is None:
        raise NotFound("Mensagem não encontrada")
    if mensagem.status != MessageStatus.FAILED.value:
        raise ConflictError(
            f"Mensagem está em '{mensagem.status}': só o que falhou volta para a fila"
        )

    mensagem.status = MessageStatus.QUEUED.value
    mensagem.metrics = {
        **(mensagem.metrics or {}),
        "requeued_by": str(ctx.user_id),
        "previous_error": (mensagem.metrics or {}).get("send_error"),
    }
    audit.record(
        db,
        action="message.requeued",
        resource_type="message",
        resource_id=mensagem.id,
        context=ctx,
    )
    return mensagem
