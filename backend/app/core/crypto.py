"""Criptografia simétrica para credenciais de integração de cada tenant.

Contas de email, CRM, calendário e API keys são do cliente. Ficam cifradas em
repouso e só são decifradas dentro do backend, no contexto do tenant dono.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import AppError


class SecretDecryptionError(AppError):
    code = "secret_decryption_error"
    status_code = 500


@lru_cache
def _fernet() -> Fernet:
    raw = settings.secrets_encryption_key
    try:
        key = base64.urlsafe_b64decode(raw)
        if len(key) != 32:
            raise ValueError
        return Fernet(raw.encode())
    except Exception:
        # Chave de desenvolvimento: deriva uma Fernet estável a partir do texto.
        if settings.is_production:
            raise AppError(
                "SECRETS_ENCRYPTION_KEY precisa ser uma chave Fernet de 32 bytes em produção"
            ) from None
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(derived)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("Não foi possível decifrar o segredo") from exc


def encrypt_json(payload: dict[str, Any]) -> str:
    return encrypt_secret(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def decrypt_json(token: str) -> dict[str, Any]:
    return json.loads(decrypt_secret(token))
