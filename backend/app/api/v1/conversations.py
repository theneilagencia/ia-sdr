"""Conversas: a caixa de entrada da operação.

A tabela existia desde a primeira migration e não tinha endpoint nenhum. As
respostas dos leads chegavam por IMAP, eram ligadas à conversa certa e ficavam
no banco — sem nenhuma forma de alguém ler o que o lead escreveu. O agente
podia até escalar para humano, o que não servia de muito: o humano não tinha
onde olhar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.db.models.engagement import (
    Conversation,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.db.models.platform import Membership
from app.db.models.sales import Company, Contact, Prospect
from app.db.session import unscoped_session
from app.rbac.roles import Permission
from app.services import audit
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/conversations", tags=["conversations"])

STATUS_VALIDOS = {"open", "closed", "handed_off"}


def _quem_e_o_lead(db: Session, conversas: list[Conversation]) -> dict[uuid.UUID, dict]:
    """Nome do contato e da empresa, em uma consulta para a lista inteira.

    Resolver por linha seria uma consulta por conversa na tela — o tipo de coisa
    que fica rápido com dez conversas e inutiliza a tela com mil.
    """
    if not conversas:
        return {}
    prospect_ids = {c.prospect_id for c in conversas}
    linhas = db.execute(
        select(Prospect.id, Contact.full_name, Contact.email, Company.name)
        .join(Contact, Prospect.contact_id == Contact.id, isouter=True)
        .join(Company, Prospect.company_id == Company.id, isouter=True)
        .where(Prospect.id.in_(prospect_ids))
    ).all()
    return {
        pid: {"contact_name": nome, "contact_email": email, "company_name": empresa}
        for pid, nome, email, empresa in linhas
    }


def _contagens(db: Session, conversas: list[Conversation]) -> dict[uuid.UUID, int]:
    if not conversas:
        return {}
    linhas = db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(Message.conversation_id.in_([c.id for c in conversas]))
        .group_by(Message.conversation_id)
    ).all()
    return {cid: int(total) for cid, total in linhas}


def _esperando_resposta(db: Session, conversas: list[Conversation]) -> set[uuid.UUID]:
    """Conversas cuja última mensagem é do lead.

    É o que a operação precisa ver primeiro: alguém escreveu e ninguém
    respondeu. Deixar isso para a tela calcular daria a mesma resposta em cada
    cliente, com uma chance a mais de dar diferente.
    """
    if not conversas:
        return set()
    ultima = (
        select(
            Message.conversation_id.label("cid"),
            func.max(Message.created_at).label("quando"),
        )
        .where(Message.conversation_id.in_([c.id for c in conversas]))
        .group_by(Message.conversation_id)
        .subquery()
    )
    linhas = db.execute(
        select(Message.conversation_id)
        .join(
            ultima,
            (Message.conversation_id == ultima.c.cid) & (Message.created_at == ultima.c.quando),
        )
        .where(Message.direction == MessageDirection.INBOUND.value)
    ).scalars()
    return set(linhas)


def _resposta(
    conversa: Conversation, lead: dict, mensagens: int, esperando: bool
) -> schemas.ConversationResponse:
    return schemas.ConversationResponse(
        id=conversa.id,
        prospect_id=conversa.prospect_id,
        campaign_id=conversa.campaign_id,
        channel=conversa.channel,
        subject=conversa.subject,
        status=conversa.status,
        last_message_at=conversa.last_message_at,
        handoff_to_user_id=conversa.handoff_to_user_id,
        created_at=conversa.created_at,
        contact_name=lead.get("contact_name"),
        contact_email=lead.get("contact_email"),
        company_name=lead.get("company_name"),
        message_count=mensagens,
        awaiting_reply=esperando,
    )


@router.get("", response_model=list[schemas.ConversationResponse])
def list_conversations(
    conversation_status: str | None = Query(default=None, alias="status"),
    campaign_id: uuid.UUID | None = Query(default=None),
    prospect_id: uuid.UUID | None = Query(default=None),
    awaiting_reply: bool | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.ConversationResponse]:
    stmt = select(Conversation)
    if conversation_status:
        stmt = stmt.where(Conversation.status == conversation_status)
    if campaign_id:
        stmt = stmt.where(Conversation.campaign_id == campaign_id)
    if prospect_id:
        stmt = stmt.where(Conversation.prospect_id == prospect_id)
    # Conversa sem mensagem ainda não tem `last_message_at`; ordenar só por ele
    # esconderia as recém-criadas no fim da lista.
    stmt = stmt.order_by(
        func.coalesce(Conversation.last_message_at, Conversation.created_at).desc()
    ).limit(limit)

    conversas = list(db.execute(stmt).scalars())
    leads = _quem_e_o_lead(db, conversas)
    mensagens = _contagens(db, conversas)
    esperando = _esperando_resposta(db, conversas)

    saida = [
        _resposta(c, leads.get(c.prospect_id, {}), mensagens.get(c.id, 0), c.id in esperando)
        for c in conversas
    ]
    if awaiting_reply is not None:
        saida = [c for c in saida if c.awaiting_reply is awaiting_reply]
    return saida


@router.get("/{conversation_id}", response_model=schemas.ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_READ)),
    db: Session = Depends(get_db),
) -> schemas.ConversationDetail:
    conversa = db.get(Conversation, conversation_id)
    if conversa is None:
        raise NotFound("Conversa não encontrada nesta empresa")

    mensagens = list(
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        ).scalars()
    )
    leads = _quem_e_o_lead(db, [conversa])
    esperando = _esperando_resposta(db, [conversa])
    base = _resposta(conversa, leads.get(conversa.prospect_id, {}), len(mensagens), bool(esperando))
    return schemas.ConversationDetail(
        **base.model_dump(),
        messages=[schemas.MessageResponse.model_validate(m) for m in mensagens],
    )


@router.patch("/{conversation_id}", response_model=schemas.ConversationResponse)
def update_conversation(
    conversation_id: uuid.UUID,
    payload: schemas.ConversationUpdate,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.ConversationResponse:
    """Fecha, reabre ou passa a conversa para uma pessoa.

    Passar para uma pessoa é o outro lado do escalonamento que o Conversation
    Agent já faz: o agente marca que precisa de humano, e aqui se diz qual.
    """
    conversa = db.get(Conversation, conversation_id)
    if conversa is None:
        raise NotFound("Conversa não encontrada nesta empresa")

    if payload.handoff_to_user_id is not None:
        # Passar para alguém de fora da empresa deixaria a conversa parada num
        # responsável que não tem como abri-la.
        with unscoped_session(reason="conversation:validate-handoff") as identity:
            membro = identity.execute(
                select(Membership)
                .where(Membership.tenant_id == ctx.tenant_id)
                .where(Membership.user_id == payload.handoff_to_user_id)
                .where(Membership.is_active.is_(True))
            ).scalar_one_or_none()
        if membro is None:
            raise NotFound("Este usuário não é membro ativo desta empresa")
        conversa.handoff_to_user_id = payload.handoff_to_user_id
        if payload.status is None:
            conversa.status = "handed_off"
    elif payload.clear_handoff:
        conversa.handoff_to_user_id = None
        if payload.status is None and conversa.status == "handed_off":
            conversa.status = "open"

    if payload.status is not None:
        if payload.status not in STATUS_VALIDOS:
            raise ConflictError(f"Status inválido: {payload.status}")
        conversa.status = payload.status

    db.flush()
    audit.record(
        db,
        action="conversation.updated",
        resource_type="conversation",
        resource_id=conversa.id,
        payload={"status": conversa.status, "handoff": str(conversa.handoff_to_user_id or "")},
        context=ctx,
    )

    leads = _quem_e_o_lead(db, [conversa])
    return _resposta(
        conversa,
        leads.get(conversa.prospect_id, {}),
        _contagens(db, [conversa]).get(conversa.id, 0),
        conversa.id in _esperando_resposta(db, [conversa]),
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=schemas.MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def write_reply(
    conversation_id: uuid.UUID,
    payload: schemas.ConversationReplyCreate,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Uma pessoa escreve a resposta, à mão.

    Nasce rascunho, como o que o agente escreve, e passa pelo mesmo portão de
    aprovação. Não é desconfiança de quem escreveu: é que o caminho de envio
    — limite diário, aquecimento, horário comercial, descadastro — está todo
    depois da aprovação, e um texto que saltasse essa fila sairia sem freio
    nenhum.
    """
    conversa = db.get(Conversation, conversation_id)
    if conversa is None:
        raise NotFound("Conversa não encontrada nesta empresa")

    prospect = db.get(Prospect, conversa.prospect_id)
    contato = db.get(Contact, prospect.contact_id) if prospect else None
    if contato is not None and contato.opted_out:
        raise ConflictError(
            f"{contato.full_name} pediu para não receber mais contato. "
            "Escrever de novo é ilegal em boa parte do mundo."
        )

    mensagem = Message(
        tenant_id=ctx.tenant_id,
        conversation_id=conversa.id,
        direction=MessageDirection.OUTBOUND.value,
        status=MessageStatus.DRAFT.value,
        channel=conversa.channel,
        subject=payload.subject or conversa.subject,
        body=payload.body,
        metrics={"written_by_user_id": str(ctx.user_id)},
    )
    db.add(mensagem)
    conversa.last_message_at = datetime.now(UTC)
    db.flush()
    audit.record(
        db,
        action="conversation.reply_drafted",
        resource_type="message",
        resource_id=mensagem.id,
        payload={"conversation_id": str(conversa.id)},
        context=ctx,
    )
    return schemas.MessageResponse.model_validate(mensagem)
