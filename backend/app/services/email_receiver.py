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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message as EmailMessageType
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.engagement import (
    Conversation,
    EnrollmentStatus,
    Message,
    MessageDirection,
    MessageStatus,
    SequenceEnrollment,
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
class Bounce:
    """O que voltou quando o email não chegou.

    `permanent` é a distinção que importa. Caixa cheia e servidor fora do ar
    (4.x.x) voltam a funcionar; endereço que não existe (5.x.x) não volta — e
    insistir num endereço inexistente é o jeito mais rápido de a reputação do
    domínio de quem manda cair.
    """

    permanent: bool
    recipient: str | None
    status_code: str | None
    diagnostic: str | None


@dataclass(frozen=True, slots=True)
class InboundEmail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_email: str
    subject: str | None
    body: str
    received_at: datetime
    #: Preenchido quando a mensagem é um aviso de não entrega, não uma resposta.
    bounce: Bounce | None = None


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


#: Remetentes automáticos de aviso de não entrega. A parte local é o que
#: varia menos entre provedores.
REMETENTES_DE_AVISO = {"mailer-daemon", "postmaster", "mail-daemon", "noreply-dmarc-support"}

#: Assuntos de bounce que chegam sem `multipart/report`. A lista é curta de
#: propósito: errar para o lado de tratar como resposta apenas atrasa; errar
#: para o lado de marcar como bounce apaga um lead de verdade do funil.
ASSUNTOS_DE_AVISO = (
    "undelivered mail returned to sender",
    "delivery status notification (failure)",
    "mail delivery failed",
    "returned mail",
    "undeliverable",
    "falha na entrega",
)


def _detectar_bounce(mensagem: EmailMessageType, from_email: str) -> Bounce | None:
    """Reconhece um aviso de não entrega e extrai o que ele diz.

    O caminho confiável é o `multipart/report` da RFC 3464, que traz
    `Final-Recipient` e `Status` em campos próprios. O reconhecimento por
    remetente e assunto é o recuo para provedores que não seguem a norma — e,
    sem código de status, um bounce assim é tratado como **temporário**: tirar
    um lead do funil por causa de um assunto em inglês mal traduzido seria pior
    do que tentar de novo.
    """
    relatorio = None
    for parte in mensagem.walk() if mensagem.is_multipart() else []:
        if parte.get_content_type() == "message/delivery-status":
            relatorio = parte
            break

    if relatorio is not None:
        campos: dict[str, str] = {}
        for bloco in relatorio.get_payload():
            if not hasattr(bloco, "items"):
                continue
            for chave, valor in bloco.items():
                campos.setdefault(chave.lower(), str(valor).strip())
        status = campos.get("status")
        destinatario = campos.get("final-recipient") or campos.get("original-recipient")
        if destinatario and ";" in destinatario:
            destinatario = destinatario.split(";", 1)[1].strip()
        return Bounce(
            # 5.x.x é permanente; 4.x.x é temporário. Sem status, o mais
            # seguro é assumir temporário.
            permanent=bool(status and status.startswith("5")),
            recipient=(destinatario or "").lower() or None,
            status_code=status,
            diagnostic=campos.get("diagnostic-code"),
        )

    local = from_email.split("@", 1)[0] if from_email else ""
    assunto = (mensagem.get("Subject") or "").lower()
    if local in REMETENTES_DE_AVISO or any(a in assunto for a in ASSUNTOS_DE_AVISO):
        return Bounce(permanent=False, recipient=None, status_code=None, diagnostic=None)
    return None


def parse_email(bruto: bytes) -> InboundEmail:
    mensagem = email_lib.message_from_bytes(bruto)
    referencias = (mensagem.get("References") or "").split()
    assunto = mensagem.get("Subject")
    remetente = parseaddr(mensagem.get("From") or "")[1].lower()
    return InboundEmail(
        message_id=mensagem.get("Message-ID"),
        in_reply_to=(mensagem.get("In-Reply-To") or "").strip() or None,
        references=referencias,
        from_email=remetente,
        subject=str(make_header(decode_header(assunto))) if assunto else None,
        body=_corpo(mensagem),
        received_at=datetime.now(UTC),
        bounce=_detectar_bounce(mensagem, remetente),
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

    if recebido.bounce is not None:
        return _registrar_bounce(session, tenant_id=tenant_id, recebido=recebido, ctx=ctx)

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


def _contato_do_bounce(
    session: Session, tenant_id: uuid.UUID, recebido: InboundEmail
) -> tuple[Contact | None, Message | None]:
    """De quem era o email que voltou.

    O aviso de não entrega não vem do lead: vem do servidor. O endereço
    original está no `Final-Recipient` do relatório ou na referência ao
    `Message-ID` da mensagem que saiu.
    """
    original = None
    for referencia in [recebido.in_reply_to, *reversed(recebido.references)]:
        if not referencia:
            continue
        original = session.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id)
            .where(Message.external_message_id == referencia)
            .limit(1)
        ).scalar_one_or_none()
        if original is not None:
            break

    destinatario = (recebido.bounce.recipient or "").lower() if recebido.bounce else ""
    contato = None
    if destinatario:
        contato = session.execute(
            select(Contact)
            .where(Contact.tenant_id == tenant_id)
            .where(func.lower(Contact.email) == destinatario)
            .limit(1)
        ).scalar_one_or_none()

    if contato is None and original is not None:
        conversa = session.get(Conversation, original.conversation_id)
        prospect = session.get(Prospect, conversa.prospect_id) if conversa else None
        contato = session.get(Contact, prospect.contact_id) if prospect else None

    return contato, original


def _registrar_bounce(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    recebido: InboundEmail,
    ctx: TenantContext,
) -> Message | None:
    """Trata o aviso de não entrega — e nunca como se fosse resposta do lead.

    Contar um bounce como resposta seria o pior dos mundos: o prospect viraria
    "engajado", o Conversation Agent escreveria uma réplica para um endereço
    que não existe, e a cadência continuaria insistindo. É assim que a
    reputação de um domínio some.
    """
    bounce = recebido.bounce
    contato, original = _contato_do_bounce(session, tenant_id, recebido)

    if original is not None:
        original.status = MessageStatus.BOUNCED.value
        original.metrics = {
            **(original.metrics or {}),
            "bounce": {
                "permanent": bounce.permanent,
                "status_code": bounce.status_code,
                "diagnostic": (bounce.diagnostic or "")[:500],
            },
        }

    if contato is None:
        logger.info(
            "aviso de não entrega sem destinatário reconhecido",
            extra={"tenant_id": str(tenant_id), "recipient": bounce.recipient},
        )
        return None

    if not bounce.permanent:
        # Temporário: registra e não mexe no funil. Caixa cheia esvazia.
        audit.record(
            session,
            action="email.soft_bounce",
            resource_type="contact",
            resource_id=contato.id,
            payload={"status_code": bounce.status_code},
            context=ctx,
        )
        session.flush()
        return None

    # Permanente: o endereço não existe. Todo prospect deste contato sai do
    # funil de abordagem — é o mesmo email em todos.
    afetados = list(
        session.execute(select(Prospect).where(Prospect.contact_id == contato.id)).scalars()
    )
    for prospect in afetados:
        prospect.status = ProspectStatus.BOUNCED.value
        prospect.last_activity_at = recebido.received_at

    # E a cadência para: insistir num endereço inexistente é o jeito mais
    # rápido de a reputação do domínio de quem manda cair.
    paradas = 0
    if afetados:
        inscricoes = session.execute(
            select(SequenceEnrollment)
            .where(SequenceEnrollment.prospect_id.in_([p.id for p in afetados]))
            .where(SequenceEnrollment.status == EnrollmentStatus.ACTIVE.value)
        ).scalars()
        for inscricao in inscricoes:
            inscricao.status = EnrollmentStatus.STOPPED.value
            inscricao.stop_reason = "email inválido"
            inscricao.next_run_at = None
            paradas += 1

    audit.record(
        session,
        action="email.hard_bounce",
        resource_type="contact",
        resource_id=contato.id,
        payload={
            "status_code": bounce.status_code,
            "prospects": len(afetados),
            "sequences_stopped": paradas,
            "diagnostic": (bounce.diagnostic or "")[:300],
        },
        context=ctx,
    )
    session.flush()
    return None


def _ler_caixa(
    host: str,
    port: int,
    username: str,
    password: str,
    limite: int,
    processar: Callable[[bytes], bool],
) -> int:
    """Lê os não lidos e marca como lida **só** a mensagem já processada.

    A ordem é o ponto. `fetch (RFC822)` marca `\\Seen` no ato, então a versão
    anterior marcava a caixa inteira antes de gravar qualquer coisa: uma
    mensagem malformada no meio do lote abortava o laço, e as outras — já
    marcadas como lidas — nunca voltavam na busca seguinte, que só pede UNSEEN.
    A resposta do lead desaparecia em silêncio, que é a pior falha que esta
    plataforma pode ter.

    Agora a leitura usa `BODY.PEEK[]`, que não marca nada, e quem decide marcar é
    quem processou. Na dúvida, a mensagem continua não lida: reprocessar é de
    graça — a duplicata é barrada pelo Message-ID — e perder não é.

    Devolve quantas mensagens foram lidas da caixa.
    """
    try:
        # 15s: o dobro disso já deixa a requisição pendurada tempo demais, e
        # servidor de email que não responde em 15 segundos não vai responder.
        with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) as imap:
            imap.login(username, password)
            imap.select("INBOX")
            _, dados = imap.search(None, "UNSEEN")
            ids = dados[0].split()[:limite]
            lidas = 0
            for identificador in ids:
                _, payload = imap.fetch(identificador, "(BODY.PEEK[])")
                if not (payload and isinstance(payload[0], tuple)):
                    continue
                lidas += 1
                if processar(payload[0][1]):
                    imap.store(identificador, "+FLAGS", "\\Seen")
            return lidas
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
        raise InboxUnavailable("Nenhuma conta de email configurada para esta empresa.") from exc

    integracao_config = {
        "imap_host": credenciais.get("imap_host"),
        "imap_port": credenciais.get("imap_port"),
    }
    host, porta = imap_settings(credenciais.get("provider", ""), integracao_config)

    contagem = {"recorded": 0, "ignored": 0, "bounced": 0, "failed": 0}

    def processar(bruto: bytes) -> bool:
        """Processa uma mensagem. Devolve se ela pode ser marcada como lida.

        A guarda é por mensagem de propósito: antes, qualquer falha aqui abortava
        o lote inteiro — e como a caixa já tinha sido marcada como lida, as
        outras respostas do lote desapareciam. Uma mensagem defeituosa não pode
        levar as outras, e ela mesma fica não lida para ser tentada de novo.
        """
        try:
            recebido = parse_email(bruto)
            resultado = record_inbound(
                session, tenant_id=tenant_id, recebido=recebido, context=context
            )
            # Commit por mensagem, e só então marcar como lida: o pior caso passa
            # a ser reprocessar — barrado pelo Message-ID — em vez de perder.
            session.commit()
        except Exception:  # noqa: BLE001 - ver docstring
            logger.exception("fetch_inbox.mensagem_com_defeito tenant=%s", tenant_id)
            session.rollback()
            contagem["failed"] += 1
            return False

        if resultado is not None:
            contagem["recorded"] += 1
        elif recebido.bounce is not None:
            # Contar bounce como "ignorada" esconderia justamente o número que
            # a operação precisa vigiar: lista comprada tem taxa de retorno
            # alta, e é ela que queima o domínio.
            contagem["bounced"] += 1
        else:
            contagem["ignored"] += 1
        return True

    lidas = _ler_caixa(
        host, porta, credenciais["username"], credenciais["password"], limit, processar
    )

    return {"fetched": lidas, **contagem}
