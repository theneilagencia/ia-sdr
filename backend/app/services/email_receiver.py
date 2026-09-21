"""Leitura da caixa de entrada: a resposta do lead volta para a conversa certa.

O problema central aqui é de pareamento. Uma resposta chega como email solto;
para servir de alguma coisa, ela precisa voltar a ser "a resposta da Alice à
abordagem da campanha Mining Canada". Duas formas de fazer isso, nesta ordem:

1. **`In-Reply-To` / `References`.** O cliente de email do lead devolve o
   `Message-ID` da mensagem original. É exato, e por isso o envio guarda o
   Message-ID de tudo que sai.
2. **Endereço do remetente.** Quando o cabeçalho não veio — encaminhamento,
   cliente que reescreve, resposta de outro endereço da mesma pessoa —, cai
   para o contato com aquele email dentro do tenant.

Sem nenhum dos dois, a mensagem é ignorada de propósito: inventar a qual
conversa ela pertence seria pior do que perdê-la, porque colocaria o texto de
um lead na conversa de outro.
"""

from __future__ import annotations

import email as email_lib
import imaplib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message as EmailMessageType
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.engagement import (
    Conversation,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.db.models.sales import Contact, Prospect, ProspectStatus
from app.services import audit, email_accounts
from app.tenancy.context import TenantContext, get_current_context_or_none, system_context

logger = logging.getLogger("ia_sdr.email.inbox")

IMAP_PRESETS = {
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
}
DEFAULT_IMAP_PORT = 993
IMAP_TIMEOUT = 15
MAX_BODY = 20_000


class InboxUnavailable(AppError):
    code = "inbox_unavailable"
    status_code = 502


@dataclass(frozen=True, slots=True)
class InboundEmail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_email: str
    subject: str | None
    body: str
    received_at: datetime


def imap_settings(provider: str, config: dict) -> tuple[str, int]:
    host = config.get("imap_host") or IMAP_PRESETS.get(provider, (None, None))[0]
    if not host:
        # SMTP próprio sem IMAP informado: chutar o mesmo host do envio erraria
        # em boa parte dos provedores.
        raise InboxUnavailable(
            "Esta conta não tem servidor de leitura configurado. Informe o IMAP "
            "em Configurações para receber as respostas."
        )
    return host, int(config.get("imap_port") or DEFAULT_IMAP_PORT)


def _texto(parte: EmailMessageType) -> str:
    carga = parte.get_payload(decode=True)
    if carga is None:
        return ""
    return carga.decode(parte.get_content_charset() or "utf-8", errors="replace")


def _corpo(mensagem: EmailMessageType) -> str:
    """Só o texto. HTML de resposta vem cheio de citação e assinatura."""
    if mensagem.is_multipart():
        for parte in mensagem.walk():
            if parte.get_content_type() == "text/plain":
                return _texto(parte)[:MAX_BODY]
        for parte in mensagem.walk():
            if parte.get_content_type() == "text/html":
                return _texto(parte)[:MAX_BODY]
        return ""
    return _texto(mensagem)[:MAX_BODY]


def parse_email(bruto: bytes) -> InboundEmail:
    mensagem = email_lib.message_from_bytes(bruto)
    referencias = (mensagem.get("References") or "").split()
    assunto = mensagem.get("Subject")
    return InboundEmail(
        message_id=mensagem.get("Message-ID"),
        in_reply_to=(mensagem.get("In-Reply-To") or "").strip() or None,
        references=referencias,
        from_email=parseaddr(mensagem.get("From") or "")[1].lower(),
        subject=str(make_header(decode_header(assunto))) if assunto else None,
        body=_corpo(mensagem),
        received_at=datetime.now(UTC),
    )


def _match_conversation(
    session: Session, tenant_id: uuid.UUID, recebido: InboundEmail
) -> tuple[Conversation, Contact] | None:
    """Acha a conversa desta resposta, ou desiste em silêncio."""
    candidatos = [recebido.in_reply_to, *reversed(recebido.references)]
    for referencia in [c for c in candidatos if c]:
        original = session.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id)
            .where(Message.external_message_id == referencia)
            .limit(1)
        ).scalar_one_or_none()
        if original is not None:
            conversa = session.get(Conversation, original.conversation_id)
            prospect = session.get(Prospect, conversa.prospect_id)
            contato = session.get(Contact, prospect.contact_id)
            return conversa, contato

    if recebido.from_email:
        contato = session.execute(
            select(Contact)
            .where(Contact.tenant_id == tenant_id)
            .where(Contact.email == recebido.from_email)
            .limit(1)
        ).scalar_one_or_none()
        if contato is not None:
            conversa = session.execute(
                select(Conversation)
                .join(Prospect, Conversation.prospect_id == Prospect.id)
                .where(Prospect.contact_id == contato.id)
                .order_by(Conversation.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if conversa is not None:
                return conversa, contato
    return None


def record_inbound(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    recebido: InboundEmail,
    context: TenantContext | None = None,
) -> Message | None:
    """Grava a resposta na conversa certa. Devolve None quando não parear."""
    ctx = context or get_current_context_or_none() or system_context(tenant_id)

    if recebido.message_id:
        ja_existe = session.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id)
            .where(Message.external_message_id == recebido.message_id)
            .limit(1)
        ).scalar_one_or_none()
        if ja_existe is not None:
            # Reler a caixa não pode duplicar conversa nem reengajar ninguém.
            return None

    par = _match_conversation(session, tenant_id, recebido)
    if par is None:
        logger.info(
            "resposta sem conversa correspondente, ignorada",
            extra={"tenant_id": str(tenant_id), "from": recebido.from_email},
        )
        return None

    conversa, contato = par
    mensagem = Message(
        tenant_id=tenant_id,
        conversation_id=conversa.id,
        direction=MessageDirection.INBOUND.value,
        status=MessageStatus.REPLIED.value,
        channel="email",
        subject=recebido.subject or conversa.subject,
        body=recebido.body,
        external_message_id=recebido.message_id,
    )
    session.add(mensagem)
    conversa.last_message_at = recebido.received_at
    if conversa.status == "open":
        conversa.status = "replied"

    prospect = session.get(Prospect, conversa.prospect_id)
    if prospect is not None and prospect.status not in (
        ProspectStatus.QUALIFIED.value,
        ProspectStatus.MEETING_BOOKED.value,
        ProspectStatus.DISQUALIFIED.value,
    ):
        # Quem respondeu está engajado, seja qual for o teor da resposta.
        prospect.status = ProspectStatus.ENGAGED.value
        prospect.last_activity_at = recebido.received_at

    audit.record(
        session,
        action="conversation.inbound_received",
        resource_type="conversation",
        resource_id=conversa.id,
        payload={"from": recebido.from_email, "contact_id": str(contato.id)},
        context=ctx,
    )
    session.flush()
    return mensagem


def _fetch_raw(host: str, port: int, username: str, password: str, limite: int) -> list[bytes]:
    """Busca os não lidos e os marca como lidos.

    Marcar como lido é o que evita reprocessar a caixa inteira a cada leitura;
    a duplicata ainda é barrada pelo Message-ID, mas de graça é melhor.
    """
    try:
        # 15s: o dobro disso já deixa a requisição pendurada tempo demais, e
        # servidor de email que não responde em 15 segundos não vai responder.
        with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) as imap:
            imap.login(username, password)
            imap.select("INBOX")
            _, dados = imap.search(None, "UNSEEN")
            ids = dados[0].split()[:limite]
            brutos = []
            for identificador in ids:
                _, payload = imap.fetch(identificador, "(RFC822)")
                if payload and isinstance(payload[0], tuple):
                    brutos.append(payload[0][1])
            return brutos
    except imaplib.IMAP4.error as exc:
        raise InboxUnavailable(
            "O servidor recusou a leitura da caixa. Revise usuário e senha em "
            f"Configurações. ({exc})"
        ) from exc
    except OSError as exc:
        raise InboxUnavailable(
            f"Não foi possível alcançar {host}:{port} para ler as respostas."
        ) from exc


def fetch_inbox(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    context: TenantContext | None = None,
) -> dict:
    """Lê a caixa da empresa e grava o que for resposta de campanha."""
    try:
        credenciais = email_accounts.credentials(session, tenant_id)
    except NotFound as exc:
        raise InboxUnavailable(
            "Nenhuma conta de email configurada para esta empresa."
        ) from exc

    integracao_config = {
        "imap_host": credenciais.get("imap_host"),
        "imap_port": credenciais.get("imap_port"),
    }
    host, porta = imap_settings(credenciais.get("provider", ""), integracao_config)

    brutos = _fetch_raw(host, porta, credenciais["username"], credenciais["password"], limit)

    gravadas, ignoradas = 0, 0
    for bruto in brutos:
        recebido = parse_email(bruto)
        if record_inbound(
            session, tenant_id=tenant_id, recebido=recebido, context=context
        ) is not None:
            gravadas += 1
        else:
            ignoradas += 1

    return {"fetched": len(brutos), "recorded": gravadas, "ignored": ignoradas}
