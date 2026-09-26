"""TOTP (RFC 6238): o segundo fator que não depende de fornecedor nenhum.

Implementado aqui em vez de trazer uma biblioteca por dois motivos concretos. O
primeiro é que o algoritmo é uma especificação fechada de vinte linhas — HMAC,
truncamento e módulo —, e os vetores de teste da própria RFC (seção 5, mais os do
RFC 4226) provam a implementação linha por linha; o teste que confere esses
vetores vale mais do que a confiança numa dependência a mais. O segundo é que
`hmac` e `base64` são da biblioteca padrão, então isto não adiciona superfície de
suprimento ao servidor que guarda as credenciais de email dos clientes.

O que **não** está aqui, de propósito: nada sobre quem pode usar, quantas vezes
pode errar ou se o código já foi gasto. Isto calcula e compara códigos; a decisão
de aceitar é do serviço, que é onde vivem o bloqueio por tentativa e a recusa de
reuso.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

#: Trinta segundos, o passo que todo aplicativo autenticador assume.
PASSO = 30

#: Seis dígitos, o mesmo motivo.
DIGITOS = 6

#: Um passo para trás e um para frente. Relógio de celular atrasado alguns
#: segundos é a causa mais comum de "meu código não funciona", e recusar por
#: isso empurra a pessoa a desligar o segundo fator — que é o pior desfecho
#: possível de uma tela de segurança.
JANELA = 1


def gerar_segredo() -> str:
    """Vinte bytes de aleatório em base32, o formato que os aplicativos leem."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _codigo(segredo_bruto: bytes, contador: int) -> str:
    mensagem = struct.pack(">Q", contador)
    digest = hmac.new(segredo_bruto, mensagem, hashlib.sha1).digest()
    # O offset vem dos quatro bits finais do digest: é o "truncamento dinâmico"
    # do RFC 4226, e é o que faz dois servidores chegarem ao mesmo número.
    offset = digest[-1] & 0x0F
    (trecho,) = struct.unpack(">I", digest[offset : offset + 4])
    return str((trecho & 0x7FFF_FFFF) % (10**DIGITOS)).zfill(DIGITOS)


def _bruto(segredo_base32: str) -> bytes:
    # Os aplicativos mostram o segredo sem o preenchimento; o decodificador da
    # biblioteca padrão exige múltiplo de oito.
    limpo = segredo_base32.strip().replace(" ", "").upper()
    return base64.b32decode(limpo + "=" * (-len(limpo) % 8))


def codigo_agora(segredo_base32: str, *, agora: int | None = None) -> str:
    """O código válido neste instante. Existe para teste e para depuração."""
    import time

    instante = agora if agora is not None else int(time.time())
    return _codigo(_bruto(segredo_base32), instante // PASSO)


def passo_valido(segredo_base32: str, codigo: str, *, agora: int | None = None) -> int | None:
    """O passo em que este código vale, ou `None` se não vale em nenhum.

    Devolve **o passo**, não um booleano, porque é isso que permite recusar
    reuso: o serviço guarda o último passo aceito e não aceita o mesmo duas
    vezes. Sem isso, um código interceptado vale os trinta segundos inteiros
    para quem o pegou no caminho.
    """
    import time

    digitado = codigo.strip().replace(" ", "").replace("-", "")
    if len(digitado) != DIGITOS or not digitado.isdigit():
        return None
    bruto = _bruto(segredo_base32)
    atual = (agora if agora is not None else int(time.time())) // PASSO
    for desvio in range(-JANELA, JANELA + 1):
        passo = atual + desvio
        # `compare_digest` em vez de `==`: comparação de string vaza, pelo tempo,
        # quantos dígitos iniciais estavam certos.
        if hmac.compare_digest(_codigo(bruto, passo), digitado):
            return passo
    return None


def uri_para_aplicativo(segredo_base32: str, *, email: str, plataforma: str) -> str:
    """O `otpauth://` que os aplicativos autenticadores leem.

    No celular, tocar neste link abre o aplicativo já com a conta preenchida —
    é o que substitui o QR code sem trazer uma biblioteca de QR para o browser.
    """
    rotulo = quote(f"{plataforma}:{email}", safe="")
    return (
        f"otpauth://totp/{rotulo}?secret={segredo_base32}"
        f"&issuer={quote(plataforma, safe='')}&algorithm=SHA1&digits={DIGITOS}&period={PASSO}"
    )


__all__ = [
    "DIGITOS",
    "JANELA",
    "PASSO",
    "codigo_agora",
    "gerar_segredo",
    "passo_valido",
    "uri_para_aplicativo",
]
