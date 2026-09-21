"""Conta de email de onde sai a prospecção, configurada por empresa.

Escrito para quem não sabe o que é SMTP. A tela oferece três provedores e
preenche host e porta sozinha; quem sabe o que está fazendo escolhe
"outro servidor" e digita tudo.

A senha entra cifrada, sai mascarada e é testada antes de salvar — porque
descobrir que a senha estava errada no primeiro disparo de uma campanha é o
pior momento possível.
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import AppError, NotFound
from app.db.models.ai import Integration

ACCOUNT_PROVIDERS = ("gmail", "outlook", "smtp")


class EmailConfigInvalid(AppError):
    code = "email_config_invalid"
    status_code = 400


@dataclass(frozen=True, slots=True)
class Preset:
    host: str
    port: int
    label: str
    #: O que a pessoa precisa fazer antes de conseguir conectar. Sem isso, a
    #: tela pede uma senha que o provedor vai recusar.
    help: str


PRESETS: dict[str, Preset] = {
    "gmail": Preset(
        host="smtp.gmail.com",
        port=587,
        label="Gmail / Google Workspace",
        help=(
            "O Google não aceita a senha normal da conta. Ative a verificação em "
            "duas etapas e crie uma Senha de app em myaccount.google.com/apppasswords — "
            "é ela que vai aqui."
        ),
    ),
    "outlook": Preset(
        host="smtp.office365.com",
        port=587,
        label="Outlook / Microsoft 365",
        help=(
            "Se a conta tem verificação em duas etapas, crie uma senha de aplicativo "
            "no portal da Microsoft e use ela aqui."
        ),
    ),
    "smtp": Preset(
        host="",
        port=587,
        label="Outro servidor (SMTP)",
        help="Peça ao seu provedor de email o endereço do servidor de envio e a porta.",
    ),
}


def presets_payload() -> list[dict]:
    return [
        {"provider": chave, "label": p.label, "host": p.host, "port": p.port, "help": p.help}
        for chave, p in PRESETS.items()
    ]


def _integration(session: Session, tenant_id: uuid.UUID) -> Integration | None:
    return session.execute(
        select(Integration)
        .where(Integration.tenant_id == tenant_id)
        .where(Integration.provider.in_(ACCOUNT_PROVIDERS))
        .limit(1)
    ).scalar_one_or_none()


def resolve(provider: str, host: str | None, port: int | None) -> tuple[str, int]:
    preset = PRESETS.get(provider)
    if preset is None:
        raise EmailConfigInvalid(f"Provedor desconhecido: {provider}")
    servidor = (host or preset.host).strip()
    porta = port or preset.port
    if not servidor:
        raise EmailConfigInvalid("Informe o endereço do servidor de envio")
    return servidor, porta


def test_connection(
    *, host: str, port: int, username: str, password: str, use_tls: bool = True
) -> tuple[bool, str]:
    """Conecta e autentica de verdade, sem mandar email para ninguém.

    As mensagens de erro são traduzidas: quem está configurando precisa saber
    o que fazer, não o código SMTP que voltou.
    """
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(username, password)
        return True, "Conectado. O servidor aceitou o usuário e a senha."
    except smtplib.SMTPAuthenticationError:
        return False, (
            "O servidor recusou usuário ou senha. Em Gmail e Outlook com verificação "
            "em duas etapas, é preciso usar uma senha de app, não a senha da conta."
        )
    except smtplib.SMTPNotSupportedError:
        return False, "O servidor não aceita conexão segura nesta porta. Tente a porta 587."
    except (TimeoutError, OSError):
        return False, (
            f"Não foi possível alcançar {host}:{port}. Confira o endereço e a porta, "
            "e se o servidor aceita conexões de fora."
        )
    except smtplib.SMTPException as exc:
        return False, f"O servidor respondeu com erro: {exc}"


def describe(session: Session, tenant_id: uuid.UUID) -> dict:
    integracao = _integration(session, tenant_id)
    if integracao is None:
        return {
            "configured": False,
            "provider": None,
            "from_email": None,
            "from_name": None,
            "host": None,
            "port": None,
            "status": "missing",
            "last_error": None,
        }
    config = integracao.config or {}
    return {
        "configured": True,
        "provider": integracao.provider,
        "from_email": integracao.account_ref,
        "from_name": config.get("from_name"),
        "host": config.get("host"),
        "port": config.get("port"),
        "status": integracao.status,
        "last_error": integracao.last_error,
    }


def store(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    provider: str,
    from_email: str,
    from_name: str | None,
    username: str | None,
    password: str,
    host: str | None,
    port: int | None,
    created_by: uuid.UUID | None,
) -> Integration:
    servidor, porta = resolve(provider, host, port)
    usuario = (username or from_email).strip()

    integracao = _integration(session, tenant_id)
    if integracao is None:
        integracao = Integration(
            tenant_id=tenant_id,
            provider=provider,
            account_ref=from_email,
            created_by=created_by,
            credentials_encrypted="",
        )
        session.add(integracao)
    else:
        integracao.provider = provider
        integracao.account_ref = from_email

    integracao.display_name = from_name or from_email
    integracao.credentials_encrypted = encrypt_json({"username": usuario, "password": password})
    integracao.config = {
        "host": servidor,
        "port": porta,
        "from_name": from_name,
        "use_tls": True,
    }
    integracao.status = "connected"
    integracao.last_error = None
    session.flush()
    return integracao


def credentials(session: Session, tenant_id: uuid.UUID) -> dict:
    """Usado pelo envio. Decifra na hora, no contexto do tenant dono."""
    integracao = _integration(session, tenant_id)
    if integracao is None:
        raise NotFound("Nenhuma conta de email configurada para esta empresa")
    segredo = decrypt_json(integracao.credentials_encrypted)
    config = integracao.config or {}
    return {
        "host": config.get("host"),
        "port": config.get("port"),
        "use_tls": config.get("use_tls", True),
        "username": segredo["username"],
        "password": segredo["password"],
        "from_email": integracao.account_ref,
        "from_name": config.get("from_name"),
    }


def remove(session: Session, tenant_id: uuid.UUID) -> None:
    integracao = _integration(session, tenant_id)
    if integracao is None:
        raise NotFound("Nenhuma conta de email configurada")
    session.delete(integracao)
    session.flush()
