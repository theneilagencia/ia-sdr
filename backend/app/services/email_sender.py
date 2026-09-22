"""Envio de verdade: o rascunho aprovado vira email na caixa de alguém.

Quatro freios antes de qualquer mensagem sair, e todos existem para proteger
uma coisa que não se recupera fácil — a reputação do domínio de quem envia:

1. **Só mensagem aprovada sai.** Rascunho não enviado é rascunho.
2. **Descadastro é absoluto.** Quem pediu para sair não recebe, nem que a
   mensagem já estivesse aprovada e na fila.
3. **Limite diário com aquecimento.** Domínio novo que dispara 500 no primeiro
   dia é classificado como spam antes do décimo email.
4. **Horário comercial.** Email de prospecção às 3h da manhã denuncia robô, e
   provedor de email lê isso como sinal.

Todo email sai com link de descadastro no corpo e no cabeçalho
`List-Unsubscribe`, que é o botão nativo de "cancelar inscrição" do Gmail e do
Outlook. Oferecer esse botão reduz denúncia de spam: quem quer sair, sai, em
vez de marcar como lixo.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.engagement import (
    Conversation,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.db.models.platform import Tenant
from app.db.models.sales import Contact, Prospect, ProspectStatus
from app.db.session import tenant_session
from app.services import audit, email_accounts
from app.services.unsubscribe import link_for
from app.tenancy.context import (
    TenantContext,
    get_current_context_or_none,
    system_context,
)

logger = logging.getLogger("ia_sdr.email")

BUSINESS_START = 8
BUSINESS_END = 18


class SendBlocked(AppError):
    """Não é erro: é a política funcionando. O motivo importa mais que o código."""

    code = "send_blocked"
    status_code = 409


class SendFailed(AppError):
    code = "send_failed"
    status_code = 502


def _policy(tenant: Tenant) -> dict:
    padrao = {
        "daily_limit": 30,
        "warmup_enabled": True,
        "warmup_start": 10,
        "warmup_daily_increment": 5,
        "business_hours_only": True,
        "timezone": "America/Sao_Paulo",
    }
    return {**padrao, **((tenant.settings or {}).get("sending_policy") or {})}


def _tz(nome: str) -> ZoneInfo:
    try:
        return ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def first_send_at(session: Session, tenant_id: uuid.UUID) -> datetime | None:
    return session.execute(
        select(func.min(Message.sent_at))
        .where(Message.tenant_id == tenant_id)
        .where(Message.status == MessageStatus.SENT.value)
    ).scalar()


def sent_today(session: Session, tenant_id: uuid.UUID, agora: datetime | None = None) -> int:
    agora = agora or datetime.now(UTC)
    return int(
        session.execute(
            select(func.count(Message.id))
            .where(Message.tenant_id == tenant_id)
            .where(Message.status == MessageStatus.SENT.value)
            .where(Message.sent_at >= agora - timedelta(days=1))
        ).scalar_one()
    )


def allowance_today(session: Session, tenant: Tenant, agora: datetime | None = None) -> dict:
    """Quantos emails esta empresa pode mandar hoje, e por quê esse número.

    Devolver o cálculo aberto é o que permite a tela dizer "hoje o limite é 15
    porque o domínio está no terceiro dia de aquecimento", em vez de só recusar.
    """
    agora = agora or datetime.now(UTC)
    politica = _policy(tenant)
    limite = int(politica["daily_limit"])
    motivo = "limite diário configurado"
    dia_aquecimento = None

    if politica["warmup_enabled"]:
        primeiro = first_send_at(session, tenant.id)
        dias = 0 if primeiro is None else (agora - primeiro).days
        dia_aquecimento = dias + 1
        durante_aquecimento = (
            int(politica["warmup_start"]) + int(politica["warmup_daily_increment"]) * dias
        )
        if durante_aquecimento < limite:
            limite = durante_aquecimento
            motivo = f"aquecimento do domínio, dia {dia_aquecimento}"

    usados = sent_today(session, tenant.id, agora)
    return {
        "limit": limite,
        "used": usados,
        "remaining": max(0, limite - usados),
        "reason": motivo,
        "warmup_day": dia_aquecimento,
    }


def within_business_hours(tenant: Tenant, agora: datetime | None = None) -> bool:
    politica = _policy(tenant)
    if not politica["business_hours_only"]:
        return True
    local = (agora or datetime.now(UTC)).astimezone(_tz(politica["timezone"]))
    return local.weekday() < 5 and BUSINESS_START <= local.hour < BUSINESS_END


def _uma_linha(valor: str) -> str:
    """Cabeçalho é de uma linha por definição.

    A biblioteca de email recusa `\r` e `\n` num cabeçalho — o que barra
    injeção, e é bom — mas recusa levantando `ValueError`. O assunto é escrito
    por um modelo, e modelo às vezes devolve duas linhas: a exceção subia pelo
    envio, a mensagem ficava "aprovada" para sempre e, por ser a mais antiga da
    fila, travava a fila inteira daquela empresa na próxima tentativa. Aqui o
    valor é achatado antes de virar cabeçalho.
    """
    return " ".join(valor.split())


def _build(
    *,
    remetente: str,
    nome_remetente: str | None,
    destinatario: str,
    assunto: str,
    corpo: str,
    link_descadastro: str,
) -> EmailMessage:
    mensagem = EmailMessage()
    # Message-ID explícito: é por ele que a resposta do lead volta a ser ligada
    # a esta conversa. Deixar o servidor gerar significaria não saber qual foi.
    mensagem["Message-ID"] = make_msgid(domain=remetente.split("@")[-1])
    nome = _uma_linha(nome_remetente) if nome_remetente else None
    mensagem["From"] = f"{nome} <{remetente}>" if nome else remetente
    mensagem["To"] = _uma_linha(destinatario)
    mensagem["Subject"] = _uma_linha(assunto) or "(sem assunto)"
    # O botão nativo de cancelar inscrição do Gmail e do Outlook.
    mensagem["List-Unsubscribe"] = f"<{link_descadastro}>"
    mensagem["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    mensagem.set_content(
        f"{corpo}\n\n--\nSe preferir não receber mais mensagens, cancele aqui: {link_descadastro}"
    )
    return mensagem


def _transport(credenciais: dict, mensagem: EmailMessage) -> None:
    """Separado para o teste poder trocar o transporte sem tocar na política."""
    with smtplib.SMTP(credenciais["host"], credenciais["port"], timeout=30) as smtp:
        smtp.ehlo()
        if credenciais.get("use_tls", True):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        smtp.login(credenciais["username"], credenciais["password"])
        smtp.send_message(mensagem)


def _traduzir(exc: Exception, credenciais: dict) -> str:
    """Erro de transporte na língua de quem opera."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "O servidor recusou usuário ou senha. Revise a conta de email em "
            "Configurações — Gmail e Outlook exigem senha de app."
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "O servidor recusou o destinatário. O endereço pode não existir."
    if isinstance(exc, smtplib.SMTPException):
        return f"O servidor de email recusou a mensagem: {exc}"
    return (
        f"Não foi possível alcançar {credenciais.get('host')}:{credenciais.get('port')}. "
        "Confira o servidor e a porta em Configurações."
    )


def _record_failure(tenant_id: uuid.UUID, message_id: uuid.UUID, tecnico: str, humano: str) -> None:
    """Grava a falha em transação própria, para ela não sumir no rollback.

    Duas versões do mesmo erro: a técnica serve para quem vai depurar, a
    humana é a que a tela mostra. Quem opera não precisa saber o que é
    `OSError: [Errno 97]`.
    """
    with tenant_session(tenant_id) as book:
        mensagem = book.get(Message, message_id)
        if mensagem is None:  # pragma: no cover - só se alguém apagar no meio
            return
        mensagem.status = MessageStatus.FAILED.value
        mensagem.metrics = {
            **(mensagem.metrics or {}),
            "send_error": tecnico[:500],
            "send_error_message": humano[:500],
        }


def send_message(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    agora: datetime | None = None,
    context: TenantContext | None = None,
) -> Message:
    """Envia uma mensagem aprovada.

    O contexto é explícito porque quem chama pode ser um worker, sem usuário
    algum: depender de estado ambiente faria o envio funcionar pela API e
    quebrar na fila, que é justamente onde ele vai rodar de verdade.
    """
    agora = agora or datetime.now(UTC)
    ctx = context or get_current_context_or_none() or system_context(tenant_id)
    mensagem = session.get(Message, message_id)
    if mensagem is None:
        raise NotFound("Mensagem não encontrada")
    if mensagem.direction != MessageDirection.OUTBOUND.value:
        raise SendBlocked("Só mensagem de saída é enviada")
    if mensagem.status != MessageStatus.QUEUED.value:
        raise SendBlocked(f"Mensagem está em '{mensagem.status}': só o que foi aprovado é enviado")

    conversa = session.get(Conversation, mensagem.conversation_id)
    prospect = session.get(Prospect, conversa.prospect_id) if conversa else None
    contato = session.get(Contact, prospect.contact_id) if prospect else None
    if contato is None or not contato.email:
        raise SendBlocked("Contato sem email")
    if contato.opted_out:
        raise SendBlocked("Contato pediu para não receber mais mensagens")

    tenant = session.get(Tenant, tenant_id)
    if not within_business_hours(tenant, agora):
        raise SendBlocked(
            "Fora do horário de envio configurado para esta empresa",
            details={"timezone": _policy(tenant)["timezone"]},
        )

    cota = allowance_today(session, tenant, agora)
    if cota["remaining"] <= 0:
        raise SendBlocked(
            "Limite de envios de hoje atingido",
            details=cota,
        )

    credenciais = email_accounts.credentials(session, tenant_id)
    email = _build(
        remetente=credenciais["from_email"],
        nome_remetente=credenciais["from_name"],
        destinatario=contato.email,
        assunto=mensagem.subject or "(sem assunto)",
        corpo=mensagem.body,
        link_descadastro=link_for(tenant_id, contato.id),
    )

    try:
        _transport(credenciais, email)
    except (smtplib.SMTPException, OSError) as exc:
        # OSError cobre servidor inalcançável, DNS quebrado e timeout — que na
        # prática são mais comuns do que erro de protocolo, e antes escapavam
        # como 500 sem deixar registro nenhum.
        # A falha precisa sobreviver ao rollback de quem chamou: uma mensagem
        # que o servidor recusou e voltou para "aprovada" seria tentada de novo
        # para sempre, sem ninguém entender por quê.
        session.rollback()
        motivo = _traduzir(exc, credenciais)
        _record_failure(tenant_id, message_id, f"{type(exc).__name__}: {exc}", motivo)
        raise SendFailed(motivo) from exc

    mensagem.status = MessageStatus.SENT.value
    mensagem.sent_at = agora
    mensagem.external_message_id = email["Message-ID"]
    if conversa is not None:
        conversa.last_message_at = agora
    # Agora sim: contatado é depois do envio, não depois do rascunho.
    if prospect is not None and prospect.status in (
        ProspectStatus.NEW.value,
        ProspectStatus.RESEARCHED.value,
        ProspectStatus.SCORED.value,
    ):
        prospect.status = ProspectStatus.CONTACTED.value
        prospect.last_activity_at = agora

    audit.record(
        session,
        action="message.sent",
        resource_type="message",
        resource_id=mensagem.id,
        payload={"to": contato.email, "used_today": cota["used"] + 1, "limit": cota["limit"]},
        context=ctx,
    )
    session.flush()
    return mensagem


def send_queued(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    agora: datetime | None = None,
    context: TenantContext | None = None,
) -> dict:
    """Envia o que está aprovado, até a cota do dia acabar.

    É o que um worker chamaria de tempos em tempos. Para quem não quer parar
    de olhar, cada mensagem ainda pode ser enviada uma a uma.
    """
    agora = agora or datetime.now(UTC)
    fila = list(
        session.execute(
            select(Message)
            .where(Message.status == MessageStatus.QUEUED.value)
            .where(Message.direction == MessageDirection.OUTBOUND.value)
            .order_by(Message.created_at)
            .limit(limit)
        ).scalars()
    )

    enviados, bloqueados = 0, []
    for mensagem in fila:
        try:
            send_message(
                session,
                tenant_id=tenant_id,
                message_id=mensagem.id,
                agora=agora,
                context=context,
            )
            # Commit por mensagem: o que já saiu está gravado, e uma falha mais
            # adiante na fila não desfaz o registro de quem já recebeu.
            session.commit()
            enviados += 1
        except SendBlocked as exc:
            bloqueados.append({"message_id": str(mensagem.id), "reason": exc.message})
            # Cota ou horário valem para a fila inteira: insistir não muda nada.
            if "Limite" in exc.message or "horário" in exc.message:
                break
        except SendFailed as exc:
            # Falha de transporte não para a fila: pode ser só um destinatário.
            bloqueados.append({"message_id": str(mensagem.id), "reason": exc.message})
        except Exception as exc:  # noqa: BLE001 - ver comentário
            # Qualquer outra falha é desta mensagem, não da fila. Deixar subir
            # abortava o lote e devolvia 500: a mensagem continuava "aprovada",
            # era a mais antiga na próxima tentativa, e travava o envio daquela
            # empresa para sempre. Uma mensagem defeituosa não pode calar as
            # outras — e o erro precisa aparecer com nome e sobrenome.
            logger.exception("send_queued.mensagem_com_defeito message_id=%s", mensagem.id)
            session.rollback()
            _record_failure(
                tenant_id,
                mensagem.id,
                f"{type(exc).__name__}: {exc}",
                "Esta mensagem tem um defeito que impediu o envio; o erro está no log.",
            )
            bloqueados.append(
                {
                    "message_id": str(mensagem.id),
                    "reason": "Defeito nesta mensagem; ela saiu da fila e o erro está no log.",
                }
            )

    return {
        "sent": enviados,
        "blocked": bloqueados,
        "allowance": allowance_today(session, session.get(Tenant, tenant_id), agora),
    }
