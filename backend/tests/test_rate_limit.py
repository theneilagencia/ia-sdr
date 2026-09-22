"""O rate limiting: o que ele protege, e o que ele mesmo precisava.

A janela deslizante em memória é suficiente para uma réplica. O que não era
suficiente era a chave e a vida do dicionário: a chave truncava um hash
aleatorizado em 32 bits (dois tokens no mesmo balde) e nenhuma chave era removida
(uma entrada por token já emitido, para sempre).
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import RateLimitMiddleware


def _pilha(limit: int, window: int) -> tuple[RateLimitMiddleware, TestClient]:
    """O middleware embrulhando um app mínimo.

    Instanciado à mão em vez de por `add_middleware`: o middleware é um app ASGI,
    o `TestClient` aceita um, e assim o teste tem a instância real na mão para
    olhar o dicionário — que é o que ele precisa verificar.
    """
    base = FastAPI()

    @base.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    middleware = RateLimitMiddleware(base, limit=limit, window=window)
    return middleware, TestClient(middleware)


def test_estoura_e_devolve_retry_after():
    _, cliente = _pilha(limit=3, window=60)
    for _ in range(3):
        assert cliente.get("/ping").status_code == 200
    excedida = cliente.get("/ping")
    assert excedida.status_code == 429
    assert int(excedida.headers["retry-after"]) >= 1
    assert excedida.json()["error"]["code"] == "rate_limited"


def test_tokens_diferentes_nao_dividem_o_mesmo_balde():
    """Dois tokens no mesmo balde é uma empresa gastando a cota de outra.

    Com `hash()` truncado em 32 bits isso acontecia por colisão, sem nada na
    tela que explicasse. O teste cobre a propriedade, não a implementação:
    milhares de tokens distintos, e nenhum gasta a cota do outro.
    """
    _, cliente = _pilha(limit=1, window=60)
    for i in range(500):
        resposta = cliente.get("/ping", headers={"authorization": f"Bearer token-{i}"})
        assert resposta.status_code == 200, f"token-{i} caiu no balde de outro"


def test_a_janela_desliza():
    _, cliente = _pilha(limit=2, window=1)
    assert cliente.get("/ping").status_code == 200
    assert cliente.get("/ping").status_code == 200
    assert cliente.get("/ping").status_code == 429
    time.sleep(1.1)
    assert cliente.get("/ping").status_code == 200


def test_chaves_vencidas_saem_da_memoria():
    """Uma entrada por token já emitido, para sempre, é um vazamento.

    Cada login emite um token novo, então cada login deixava uma chave para
    trás; quem quisesse derrubar a API só precisaria variar o cabeçalho.
    """
    middleware, cliente = _pilha(limit=100_000, window=0)
    for i in range(RateLimitMiddleware.LIMPEZA_A_CADA + 5):
        cliente.get("/ping", headers={"authorization": f"Bearer token-{i}"})

    # Com janela zero, toda chave vence imediatamente: depois da varredura só
    # sobram as poucas criadas desde então.
    assert len(middleware._hits) < 50, f"{len(middleware._hits)} chaves ficaram na memória"


def test_limite_zero_bloqueia_tudo():
    """`limit=0` e `window=0` tinham de valer.

    Com `or` no lugar de `is None`, zero caía no padrão: quem pedisse janela
    zero recebia sessenta segundos, e quem pedisse "bloqueie tudo" recebia
    trezentas requisições por minuto. Parâmetro aceito e ignorado é pior do que
    parâmetro recusado.
    """
    _, cliente = _pilha(limit=0, window=60)
    assert cliente.get("/ping").status_code == 429
