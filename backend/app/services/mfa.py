"""Segundo fator por TOTP: o que ele decide, e por que decide fora da sessão de quem chama.

Esta plataforma guarda, por empresa, a senha da conta de email e a chave da API
de IA. Uma senha vazada abre as duas. É por isso que o segundo fator vem antes de
SSO na fila: SSO depende do provedor de identidade do cliente, TOTP não depende de
ninguém — funciona com qualquer aplicativo autenticador e cabe na biblioteca
padrão (ver `app/core/totp.py`, verificado contra os vetores da RFC 6238).

**A contabilidade de tentativa roda na própria sessão deste módulo, e não na de
quem chama.** Isso não é estilo: `unscoped_session` dá rollback quando a exceção
atravessa, e a recusa do código é exatamente uma exceção atravessando. Se o
incremento de `mfa_failed_attempts` vivesse na transação de quem chama, todo
chute errado seria desfeito junto com a recusa — o bloqueio por tentativa nunca
chegaria a cinco, e o segundo fator seria um cadeado que aceita chute infinito.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core import totp
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.errors import AppError
from app.core.security import verify_password
from app.db.models.platform import User
from app.db.session import unscoped_session

#: Cinco chutes e a conta descansa. Seis dígitos são um milhão de combinações,
#: então cinco por quinze minutos deixa um ataque de força bruta em séculos, sem
#: transformar dedo trocado em suporte.
TENTATIVAS = 5
CASTIGO = timedelta(minutes=15)

#: Oito códigos de recuperação. Perder o celular não pode significar perder a
#: empresa — e o caminho de volta não pode depender de suporte humano, que numa
#: plataforma multiempresa é a porta mais fácil de engenharia social.
RECUPERACAO_QUANTOS = 8

PLATAFORMA = "AI Sales Workforce"


class MfaRequired(AppError):
    """A senha estava certa e falta o código. Dito só depois da senha.

    Contar que esta conta tem segundo fator é inevitável para quem já provou a
    senha — sem isso a pessoa não sabe que precisa do código. O que **não** se
    pode é contar antes: senha errada devolve a recusa genérica de sempre, e
    quem chuta senha não descobre nada sobre a conta.
    """

    code = "mfa_required"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("Informe o código do seu aplicativo autenticador.")


class MfaInvalid(AppError):
    code = "mfa_invalid"
    status_code = 401

    def __init__(self) -> None:
        super().__init__(
            "Código inválido ou já usado. Confira o aplicativo — cada código vale "
            "trinta segundos e serve uma vez só."
        )


class MfaLocked(AppError):
    code = "mfa_locked"
    status_code = 429

    def __init__(self, minutos: int) -> None:
        super().__init__(
            f"Muitas tentativas. Espere {minutos} minuto(s) e tente de novo, ou "
            "entre com um código de recuperação."
        )


class MfaJaAtivo(AppError):
    """Trocar o segundo fator com a sessão aberta seria trocá-lo sem prova.

    Quem estiver com um token roubado poderia apontar o segundo fator para o
    próprio celular e trancar o dono fora. Desligar exige senha **e** código.
    """

    code = "mfa_already_active"
    status_code = 409

    def __init__(self) -> None:
        super().__init__(
            "A verificação em duas etapas já está ativa. Para trocar de aplicativo, "
            "desative primeiro — o que exige a sua senha e um código atual."
        )


class MfaNaoConfigurado(AppError):
    code = "mfa_not_configured"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("Não há verificação em duas etapas configurada nesta conta.")


@dataclass(frozen=True, slots=True)
class Configuracao:
    """O que a tela mostra **uma vez**: o segredo e o link para o aplicativo."""

    secret: str
    otpauth_uri: str


def _hash(valor: str) -> str:
    return hashlib.sha256(valor.strip().lower().encode()).hexdigest()


def _codigo_de_recuperacao() -> str:
    # Três grupos de quatro, em base32 sem caracteres ambíguos: é para ser
    # copiado à mão de um papel, às vezes por telefone.
    alfabeto = "abcdefghjkmnpqrstuvwxyz23456789"
    grupos = ["".join(secrets.choice(alfabeto) for _ in range(4)) for _ in range(3)]
    return "-".join(grupos)


def esta_ativo(user: User) -> bool:
    return user.mfa_enabled_at is not None


def iniciar(user_id: uuid.UUID) -> Configuracao:
    """Gera o segredo e devolve o link, **sem ligar nada**.

    Ligar só na confirmação é o que impede alguém de se trancar fora: se o
    aplicativo foi configurado errado, o login continua funcionando com a senha
    até que um código de verdade prove o contrário.
    """
    with unscoped_session(reason="auth:mfa-iniciar") as session:
        user = session.get(User, user_id)
        if user is None:
            raise MfaNaoConfigurado()
        if esta_ativo(user):
            raise MfaJaAtivo()
        segredo = totp.gerar_segredo()
        user.mfa_secret = encrypt_secret(segredo)
        user.mfa_last_step = None
        user.mfa_failed_attempts = 0
        user.mfa_locked_until = None
        return Configuracao(
            secret=segredo,
            otpauth_uri=totp.uri_para_aplicativo(
                segredo, email=user.email, plataforma=PLATAFORMA
            ),
        )


def confirmar(user_id: uuid.UUID, codigo: str) -> list[str]:
    """Liga o segundo fator e devolve os códigos de recuperação, uma vez."""
    with unscoped_session(reason="auth:mfa-confirmar") as session:
        user = session.get(User, user_id)
        if user is None or not user.mfa_secret:
            raise MfaNaoConfigurado()
        if esta_ativo(user):
            raise MfaJaAtivo()
        passo = totp.passo_valido(decrypt_secret(user.mfa_secret), codigo)
        if passo is None:
            raise MfaInvalid()

        codigos = [_codigo_de_recuperacao() for _ in range(RECUPERACAO_QUANTOS)]
        user.mfa_recovery_hashes = [_hash(c) for c in codigos]
        user.mfa_enabled_at = datetime.now(UTC)
        user.mfa_last_step = passo
        user.mfa_failed_attempts = 0
        user.mfa_locked_until = None
        return codigos


def desativar(user_id: uuid.UUID, *, password: str, codigo: str) -> None:
    """Desligar exige senha **e** código: as duas coisas que o fator protege."""
    with unscoped_session(reason="auth:mfa-desativar") as session:
        user = session.get(User, user_id)
        if user is None or not esta_ativo(user):
            raise MfaNaoConfigurado()
        if not verify_password(password, user.password_hash):
            raise MfaInvalid()
        if not _aceita(user, codigo):
            raise MfaInvalid()
        user.mfa_secret = None
        user.mfa_enabled_at = None
        user.mfa_last_step = None
        user.mfa_recovery_hashes = None
        user.mfa_failed_attempts = 0
        user.mfa_locked_until = None


def _aceita(user: User, codigo: str) -> bool:
    """Confere o código do aplicativo ou um de recuperação, e gasta o que valeu."""
    if not codigo or not user.mfa_secret:
        return False

    passo = totp.passo_valido(decrypt_secret(user.mfa_secret), codigo)
    if passo is not None:
        # Reuso recusado: o mesmo código não entra duas vezes, nem um passo já
        # passado. Quem interceptou o código na primeira vez não entra na
        # segunda.
        if user.mfa_last_step is not None and passo <= user.mfa_last_step:
            return False
        user.mfa_last_step = passo
        return True

    guardados = list(user.mfa_recovery_hashes or [])
    alvo = _hash(codigo)
    if alvo in guardados:
        guardados.remove(alvo)
        # Reatribuição, não `.remove()` na lista original: JSONB mutado no lugar
        # não é detectado pelo SQLAlchemy, e o código de recuperação continuaria
        # valendo para sempre.
        user.mfa_recovery_hashes = guardados
        return True
    return False


def verificar(user_id: uuid.UUID, codigo: str | None) -> None:
    """Exige o segundo fator de quem o tem ligado. Silencioso para quem não tem.

    Roda em transação própria e commita antes de recusar — ver o docstring do
    módulo: a contabilidade de tentativa não pode viver na transação que a
    recusa vai desfazer.
    """
    agora = datetime.now(UTC)
    problema: AppError | None = None

    with unscoped_session(reason="auth:mfa-verificar") as session:
        user = session.get(User, user_id)
        if user is None or not esta_ativo(user):
            return

        if user.mfa_locked_until and user.mfa_locked_until > agora:
            restante = max(1, int((user.mfa_locked_until - agora).total_seconds() // 60) + 1)
            problema = MfaLocked(restante)
        elif not codigo:
            # Falta de código não é chute: não conta tentativa, ou a tela de
            # login trancaria a conta de quem só apertou Enter cedo.
            problema = MfaRequired()
        elif _aceita(user, codigo):
            user.mfa_failed_attempts = 0
            user.mfa_locked_until = None
        else:
            user.mfa_failed_attempts = (user.mfa_failed_attempts or 0) + 1
            if user.mfa_failed_attempts >= TENTATIVAS:
                user.mfa_locked_until = agora + CASTIGO
                user.mfa_failed_attempts = 0
            problema = MfaInvalid()

    if problema is not None:
        raise problema


def estado(user: User) -> dict:
    """O que a tela pode saber sem revelar nada: se está ativo e desde quando."""
    return {
        "enabled": esta_ativo(user),
        "enabled_at": user.mfa_enabled_at,
        "pending": user.mfa_secret is not None and not esta_ativo(user),
        "recovery_codes_left": len(user.mfa_recovery_hashes or []),
    }


__all__ = [
    "CASTIGO",
    "Configuracao",
    "MfaInvalid",
    "MfaJaAtivo",
    "MfaLocked",
    "MfaNaoConfigurado",
    "MfaRequired",
    "RECUPERACAO_QUANTOS",
    "TENTATIVAS",
    "confirmar",
    "desativar",
    "esta_ativo",
    "estado",
    "iniciar",
    "verificar",
]
