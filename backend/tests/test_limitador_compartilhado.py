"""O freio de requisições com o balde compartilhado, contra um Redis de verdade.

Contra um Redis de verdade, e não contra um dublê, por um motivo que é a razão
de este backend existir: o que se está verificando é **atomicidade**. Um dublê
em Python executaria o script Lua como se fosse uma função local e concordaria
com qualquer implementação, inclusive com a errada — a que lê "299 usados",
decide, e deixa duas réplicas passarem no mesmo milissegundo.

O `REDIS_URL` dos testes é `redis://localhost:6379/15` por padrão: o banco 15,
que a suíte limpa, para não esbarrar em nada que a máquina tenha de verdade no
banco 0. O CI sobe um serviço Redis no job do backend; numa máquina sem Redis,
os testes deste arquivo são pulados com o motivo na cara — e o de memória, que
continua sendo o padrão da aplicação, tem cobertura própria em
`test_rate_limit.py`.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import RateLimitMiddleware
from app.core.limitador import (
    LimitadorEmMemoria,
    LimitadorRedis,
    chave_do_cliente,
    construir,
)

REDIS_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15")


def _cliente_redis():
    try:
        import redis
    except ImportError:  # pragma: no cover - a dependência está no pyproject
        pytest.skip("biblioteca `redis` não instalada")
    cliente = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
    try:
        cliente.ping()
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"Redis não disponível em {REDIS_URL}: {erro}")
    return cliente


@pytest.fixture
def redis_limpo():
    cliente = _cliente_redis()
    cliente.flushdb()
    yield cliente
    cliente.flushdb()


@pytest.fixture
def limitador(redis_limpo):
    return LimitadorRedis(redis_limpo)


def test_conta_dentro_da_janela_e_recusa_no_teto(limitador):
    for _ in range(3):
        permitido, _ = limitador.consumir("cliente", limite=3, janela=60)
        assert permitido
    permitido, espera = limitador.consumir("cliente", limite=3, janela=60)
    assert not permitido
    # O `retry-after` precisa ser útil: mandar "1" quando falta um minuto faz o
    # cliente bem-comportado voltar 59 vezes antes da hora.
    assert 55 <= espera <= 60


def test_a_janela_desliza(limitador):
    assert limitador.consumir("desliza", limite=1, janela=1)[0]
    assert not limitador.consumir("desliza", limite=1, janela=1)[0]
    time.sleep(1.1)
    assert limitador.consumir("desliza", limite=1, janela=1)[0], (
        "passada a janela, a vaga volta: freio é janela deslizante, não bloqueio"
    )


def test_baldes_de_clientes_diferentes_nao_se_misturam(limitador):
    assert limitador.consumir("empresa-a", limite=1, janela=60)[0]
    assert limitador.consumir("empresa-b", limite=1, janela=60)[0], (
        "uma empresa não pode gastar a cota da outra"
    )


def test_duas_replicas_dividem_o_mesmo_balde(redis_limpo):
    """O defeito que este backend existe para corrigir.

    Duas instâncias do limitador são duas réplicas da API. Com o balde em
    memória, cada uma contaria o seu e o teto anunciado valeria o dobro: o
    operador configura 300 por minuto e a aplicação deixa passar 600, sem nada
    na configuração que explique.
    """
    replica_a = LimitadorRedis(redis_limpo)
    replica_b = LimitadorRedis(redis_limpo)

    assert replica_a.consumir("token:x", limite=2, janela=60)[0]
    assert replica_b.consumir("token:x", limite=2, janela=60)[0]
    assert not replica_b.consumir("token:x", limite=2, janela=60)[0]
    assert not replica_a.consumir("token:x", limite=2, janela=60)[0]

    # E o de memória, no mesmo cenário, deixa passar o dobro — é o contraste que
    # justifica o backend, e vale tê-lo escrito e não só afirmado.
    mem_a, mem_b = LimitadorEmMemoria(), LimitadorEmMemoria()
    assert mem_a.consumir("token:x", limite=2, janela=60)[0]
    assert mem_a.consumir("token:x", limite=2, janela=60)[0]
    assert not mem_a.consumir("token:x", limite=2, janela=60)[0]
    assert mem_b.consumir("token:x", limite=2, janela=60)[0], (
        "a segunda réplica com balde próprio ignora o que a primeira já contou"
    )


def test_duas_requisicoes_no_mesmo_milissegundo_contam_duas(limitador):
    """O membro do sorted set é um id aleatório, não o timestamp.

    Com o timestamp como membro, duas requisições no mesmo milissegundo seriam
    **um** membro só: a segunda sobrescreveria a primeira e o cliente ganharia
    uma vaga de graça a cada empate de relógio. Em milissegundo, empate é comum.
    """
    permitidas = [limitador.consumir("rajada", limite=5, janela=60)[0] for _ in range(6)]
    assert permitidas == [True, True, True, True, True, False]


def test_limite_zero_recusa_tudo_sem_estourar(limitador):
    """O interruptor de emergência. Se ele devolvesse 500, seria inútil."""
    permitido, espera = limitador.consumir("qualquer", limite=0, janela=60)
    assert not permitido
    assert espera >= 1


def test_a_chave_expira_sozinha(limitador, redis_limpo):
    """Sem expiração, o Redis acumularia uma chave por token já emitido.

    É o mesmo problema que o balde em memória resolve com varredura periódica —
    aqui é o próprio Redis que apaga, o que também significa que a contagem não
    sobrevive além da janela.
    """
    limitador.consumir("expira", limite=5, janela=2)
    chave = LimitadorRedis.PREFIXO + "expira"
    ttl = redis_limpo.pttl(chave)
    assert 0 < ttl <= 2000
    time.sleep(2.2)
    assert redis_limpo.exists(chave) == 0


def test_redis_fora_do_ar_cai_para_o_balde_em_memoria(caplog):
    """Cair para "deixa passar tudo" abriria a porta no dia em que o Redis morre.

    Cair para "recusa tudo" derrubaria a aplicação por causa de um serviço
    auxiliar. O que se quer é o freio por réplica — pior do que o ideal, melhor
    do que nada — com uma linha no log dizendo o que aconteceu.
    """
    import redis

    morto = redis.Redis.from_url(
        # Porta onde não há ninguém: falha de conexão de verdade, não simulada.
        "redis://127.0.0.1:6399/0",
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
    )
    limitador = LimitadorRedis(morto)

    with caplog.at_level("WARNING"):
        assert limitador.consumir("sem-redis", limite=2, janela=60)[0]
        assert limitador.consumir("sem-redis", limite=2, janela=60)[0]
        assert not limitador.consumir("sem-redis", limite=2, janela=60)[0], (
            "sem Redis o freio continua existindo, por processo"
        )

    avisos = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("volta a ser por" in m for m in avisos)
    # Uma linha por episódio, não uma por requisição: um Redis fora do ar
    # geraria milhares de linhas idênticas e esconderia o resto do log.
    assert len([m for m in avisos if "volta a ser por" in m]) == 1


def test_construir_escolhe_o_backend_pela_configuracao(redis_limpo):
    assert isinstance(construir(""), LimitadorEmMemoria)
    assert isinstance(construir(REDIS_URL), LimitadorRedis)


def test_a_chave_do_cliente_nao_e_o_token(redis_limpo):
    """O token nunca vira nome de chave no Redis: quem lê o banco lê credencial."""
    chave = chave_do_cliente("Bearer abc.def.ghi", "10.0.0.1")
    assert "abc.def.ghi" not in chave
    assert chave.startswith("token:")
    # Sem Authorization, o balde é do IP — senão todo anônimo dividiria um só.
    assert chave_do_cliente("", "10.0.0.2") == "ip:10.0.0.2"
    assert chave_do_cliente("", None) == "ip:unknown"


def test_a_api_responde_429_com_retry_after_usando_o_redis(redis_limpo):
    """Ponta a ponta: o middleware, com o backend de Redis, numa aplicação de verdade."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit=2, window=60, backend=LimitadorRedis(redis_limpo))

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as cliente:
        assert cliente.get("/ping").status_code == 200
        assert cliente.get("/ping").status_code == 200
        recusa = cliente.get("/ping")
        assert recusa.status_code == 429
        assert recusa.json()["error"]["code"] == "rate_limited"
        assert int(recusa.headers["retry-after"]) >= 1


def test_health_nao_gasta_cota(redis_limpo):
    """Monitor de uptime bate a cada 30s; contá-lo é derrubar o alerta junto."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit=1, window=60, backend=LimitadorRedis(redis_limpo))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    with TestClient(app) as cliente:
        for _ in range(5):
            assert cliente.get("/health").status_code == 200
