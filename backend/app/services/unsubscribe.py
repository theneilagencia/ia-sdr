"""Link de descadastro assinado.

Email frio sem saída para quem recebe é ilegal em boa parte do mundo (LGPD,
CAN-SPAM, GDPR) e, mesmo onde não fosse, é o caminho mais rápido para o
domínio do cliente virar spam: quem não acha o link clica em "denunciar".

O token é assinado com HMAC e carrega tenant e contato. Não expira de
propósito — descadastro de dois anos atrás continua valendo — e não precisa
de banco para ser verificado, só para ser aplicado.

Por isso a assinatura usa um segredo **próprio** (`UNSUBSCRIBE_SECRET`), e não o
dos tokens de sessão: rotacionar o `JWT_SECRET` é o que se faz depois de um
vazamento, e se ele assinasse estes links, cada rotação transformaria todo
descadastro já enviado numa página de erro. Sem a variável configurada, herda o
`jwt_secret` — é como as instalações existentes já assinavam, e trocar isso de
uma vez quebraria exatamente o que este arquivo existe para proteger.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

from app.core.config import settings
from app.core.errors import AppError


class InvalidUnsubscribeToken(AppError):
    code = "invalid_unsubscribe_token"
    status_code = 400


def _segredo() -> str:
    return settings.unsubscribe_secret or settings.jwt_secret


def _assinar(payload: str) -> str:
    mac = hmac.new(_segredo().encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:32]


def make_token(tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str:
    payload = f"{tenant_id}:{contact_id}"
    corpo = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{corpo}.{_assinar(payload)}"


def parse_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        corpo, assinatura = token.split(".", 1)
        payload = base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)).decode()
        tenant_raw, contact_raw = payload.split(":", 1)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidUnsubscribeToken("Link de descadastro inválido") from exc

    # compare_digest: comparação de assinatura não pode vazar tempo.
    if not hmac.compare_digest(assinatura, _assinar(payload)):
        raise InvalidUnsubscribeToken("Link de descadastro inválido")
    try:
        return uuid.UUID(tenant_raw), uuid.UUID(contact_raw)
    except ValueError as exc:
        raise InvalidUnsubscribeToken("Link de descadastro inválido") from exc


def link_for(tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/public/unsubscribe/{make_token(tenant_id, contact_id)}"
