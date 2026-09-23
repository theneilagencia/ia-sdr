"""Criptografia simétrica para credenciais de integração de cada tenant.

Contas de email, CRM, calendário e API keys são do cliente. Ficam cifradas em
repouso e só são decifradas dentro do backend, no contexto do tenant dono.

**Rotação.** A chave é uma lista, não um valor: a primeira cifra, qualquer uma
decifra (`MultiFernet`). Isso existe porque a pergunta "e se a chave vazar?"
tinha, até aqui, uma resposta inaceitável — trocar a chave tornava ilegível toda
credencial já salva, o que na prática significa nunca trocar. Com a lista, a
sequência é: gerar a nova, pôr a antiga em `SECRETS_ENCRYPTION_KEYS_PREVIOUS`,
subir, rodar `scripts/rotacionar_chave.py`, e só então apagar a antiga.

O cache é indexado pelas próprias chaves, e não guardado num `lru_cache` sem
argumento: uma troca de chave em tempo de execução (é o que os testes fazem, e é
o que uma recarga de configuração faria) precisa produzir um `MultiFernet` novo,
senão o processo seguiria cifrando com a chave que acabou de sair.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings
from app.core.errors import AppError


class SecretDecryptionError(AppError):
    code = "secret_decryption_error"
    status_code = 500


def _uma(raw: str) -> Fernet:
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


@lru_cache(maxsize=8)
def _multi(chaves: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([_uma(k) for k in chaves])


def _chaves() -> tuple[str, ...]:
    """A atual primeiro; é ela que cifra."""
    return (settings.secrets_encryption_key, *settings.previous_encryption_keys)


def _fernet() -> MultiFernet:
    return _multi(_chaves())


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


def recifrar(token: str) -> str:
    """Passa um texto cifrado para a chave **atual**, sem ver o conteúdo.

    É o que o script de rotação usa. `MultiFernet.rotate` decifra com qualquer
    chave da lista e cifra de novo com a primeira — e o segredo não passa por
    nenhuma variável do nosso código no caminho.
    """
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "Há segredo cifrado com uma chave que não está na lista: ponha a chave "
            "antiga em SECRETS_ENCRYPTION_KEYS_PREVIOUS antes de rotacionar."
        ) from exc


def rotacao_pendente() -> bool:
    """Há chave antiga configurada — ou seja, a rotação começou e não terminou."""
    return bool(settings.previous_encryption_keys)
