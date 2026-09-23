"""Convite com aceite: a pessoa entra com a senha dela.

Três decisões que valem explicar, porque cada uma tapa um buraco do fluxo
anterior — em que o admin criava a conta com uma senha escolhida por ele:

* **O token vale uma vez e expira.** Sete dias. Convite que fica valendo para
  sempre é credencial permanente escondida num email antigo.
* **Aceitar exige a senha do próprio dono da conta.** Se o email é novo, a senha
  informada nasce com a conta; se já existe conta, a senha precisa ser a dela.
  Sem isso, quem convida — que vê o link uma vez — poderia anexar a conta de
  outra pessoa ao seu próprio tenant, ou pior, entrar como ela.
* **A recusa é sempre a mesma frase.** Token inválido, expirado, já aceito ou
  senha errada devolvem o mesmo erro. Distinguir os casos devolveria, por outro
  caminho, exatamente a enumeração de usuários que este fluxo veio consertar.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.db.models.platform import Invitation, Membership, Tenant, User
from app.db.session import unscoped_session
from app.rbac.roles import Role
from app.services import mfa

#: Prazo do convite. Longo o suficiente para sobreviver a umas férias curtas,
#: curto o suficiente para não virar credencial esquecida.
VALIDADE = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Aceite:
    """Quem entrou, onde e com qual papel — sem arrastar entidade de sessão.

    Devolver objetos do ORM de uma sessão que já fechou é como se colhe
    `DetachedInstanceError` em produção; e recriá-los à mão seria pior, porque um
    `add()` distraído depois inseriria duplicata.
    """

    user_id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    tenant_id: uuid.UUID
    tenant_name: str
    role: str
    #: `False` quando o email já tinha conta na plataforma. Não vai para resposta
    #: de API nenhuma: serve para a auditoria dizer o que aconteceu.
    conta_criada: bool


class InviteInvalid(AppError):
    """Uma frase só para todos os motivos — ver o docstring do módulo."""

    code = "invite_invalid"
    status_code = 400

    def __init__(self) -> None:
        super().__init__(
            "Não foi possível aceitar este convite. Ele pode ter expirado, já ter "
            "sido usado, ou a senha não corresponde à conta deste email. Peça um "
            "convite novo a quem administra a empresa."
        )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pendentes(session: Session, tenant_id: uuid.UUID) -> list[Invitation]:
    """Os convites que ainda podem ser aceitos, mais recentes primeiro."""
    agora = datetime.now(UTC)
    return list(
        session.execute(
            select(Invitation)
            .where(Invitation.tenant_id == tenant_id)
            .where(Invitation.accepted_at.is_(None))
            .where(Invitation.expires_at > agora)
            .order_by(Invitation.created_at.desc())
        ).scalars()
    )


def contar_pendentes(
    session: Session, tenant_id: uuid.UUID, *, exceto_email: str | None = None
) -> int:
    """Convite pendente é vaga ocupada.

    Sem contar, um plano de duas pessoas aceita vinte convites e as vinte
    entram: o limite só seria descoberto por quem aceitasse depois, e a culpa
    pareceria ser de quem chegou por último.

    `exceto_email` tira da conta o convite que está a ponto de ser substituído —
    ver `criar`, que apaga o pendente do mesmo email. Contá-lo faria a segunda
    tentativa de convidar a mesma pessoa bater no limite por causa da primeira.
    """
    consulta = (
        select(func.count(Invitation.id))
        .where(Invitation.tenant_id == tenant_id)
        .where(Invitation.accepted_at.is_(None))
        .where(Invitation.expires_at > datetime.now(UTC))
    )
    if exceto_email:
        consulta = consulta.where(Invitation.email != exceto_email.strip().lower())
    return int(session.execute(consulta).scalar_one())


def criar(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: Role,
    invited_by: uuid.UUID | None,
) -> tuple[Invitation, str]:
    """Cria o convite e devolve o token **uma vez**.

    Convidar o mesmo email de novo substitui o convite pendente em vez de somar
    outro: dois links válidos para a mesma pessoa é uma credencial a mais
    circulando, sem nenhuma utilidade.

    A resposta não diz nada sobre a existência da conta — nem aqui, nem depois.
    """
    alvo = email.strip().lower()
    for antigo in session.execute(
        select(Invitation)
        .where(Invitation.tenant_id == tenant_id)
        .where(Invitation.email == alvo)
        .where(Invitation.accepted_at.is_(None))
    ).scalars():
        session.delete(antigo)
    session.flush()

    token = secrets.token_urlsafe(32)
    convite = Invitation(
        tenant_id=tenant_id,
        email=alvo,
        role=role.value,
        token_hash=_hash(token),
        invited_by=invited_by,
        expires_at=datetime.now(UTC) + VALIDADE,
    )
    session.add(convite)
    session.flush()
    return convite, token


def revogar(session: Session, tenant_id: uuid.UUID, invitation_id: uuid.UUID) -> Invitation | None:
    convite = session.execute(
        select(Invitation)
        .where(Invitation.tenant_id == tenant_id)
        .where(Invitation.id == invitation_id)
        .where(Invitation.accepted_at.is_(None))
    ).scalar_one_or_none()
    if convite is None:
        return None
    session.delete(convite)
    session.flush()
    return convite


def aceitar(token: str, *, password: str, full_name: str, code: str | None = None) -> Aceite:
    """Aceita o convite e devolve quem entrou, onde e com qual papel.

    Roda na sessão sem escopo por necessidade, não por conveniência: quem clica
    no link ainda não é membro de nada, então não existe tenant ativo para o RLS
    usar. O que identifica a empresa é o próprio convite.

    Conta nova: a senha informada é a dela, e a pessoa já sai logada do outro
    lado. Conta que já existe: a senha tem de ser a que ela usa — o convite anexa
    o vínculo, não abre a conta de ninguém.
    """
    agora = datetime.now(UTC)
    with unscoped_session(reason="public:aceitar-convite") as identity:
        convite = identity.execute(
            select(Invitation).where(Invitation.token_hash == _hash(token))
        ).scalar_one_or_none()
        if convite is None or convite.accepted_at is not None or convite.expires_at <= agora:
            raise InviteInvalid()

        tenant = identity.get(Tenant, convite.tenant_id)
        if tenant is None or not tenant.is_active:
            raise InviteInvalid()

        user = identity.execute(
            select(User).where(User.email == convite.email)
        ).scalar_one_or_none()
        conta_criada = user is None

        if user is None:
            if len(password) < 10:
                raise InviteInvalid()
            user = User(
                email=convite.email,
                password_hash=hash_password(password),
                full_name=full_name.strip()[:200],
            )
            identity.add(user)
            identity.flush()
        else:
            if not user.is_active or not verify_password(password, user.password_hash):
                raise InviteInvalid()
            # Nome de quem já tem conta não é sobrescrito por quem convidou.

            # Aceitar emite sessão, então exige o mesmo segundo fator que o
            # login. Sem isto o convite seria a porta que contorna o MFA:
            # bastaria convidar o email de alguém, saber a senha e entrar sem o
            # código — e o vínculo novo viria de brinde.
            #
            # Fora da recusa uniforme de propósito: quem chegou aqui já provou a
            # senha, então contar que a conta tem segundo fator não revela nada
            # que ela não pudesse descobrir entrando. Senha errada continua
            # devolvendo a mesma frase de sempre, alguns passos acima.
            #
            # E **antes** de qualquer escrita: `mfa.verificar` levanta na recusa,
            # esta transação desfaz, e o convite continua valendo. Se a checagem
            # viesse depois, um código digitado errado gastaria o convite de uma
            # vez — a pessoa ficaria trancada fora por um erro de digitação.
            mfa.verificar(user.id, code)

        vinculo = identity.execute(
            select(Membership)
            .where(Membership.tenant_id == convite.tenant_id)
            .where(Membership.user_id == user.id)
        ).scalar_one_or_none()
        if vinculo is None:
            vinculo = Membership(
                tenant_id=convite.tenant_id, user_id=user.id, role=convite.role
            )
            identity.add(vinculo)
        else:
            # Já era membro (convite duplicado, ou reentrada depois de desativado):
            # o convite reativa e aplica o papel convidado.
            vinculo.is_active = True
            vinculo.role = convite.role
        convite.accepted_at = agora
        identity.flush()

        return Aceite(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_platform_admin=user.is_platform_admin,
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            role=vinculo.role,
            conta_criada=conta_criada,
        )


__all__ = [
    "VALIDADE",
    "Aceite",
    "InviteInvalid",
    "aceitar",
    "contar_pendentes",
    "criar",
    "pendentes",
    "revogar",
]
