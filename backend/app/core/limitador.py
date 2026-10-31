"""O freio de requisições, com dois backends e uma interface só.

Por que dois. O de memória basta para uma réplica, e é o que estava aqui desde
o começo: um `deque` por cliente, janela deslizante, tudo dentro do processo.
Só que a conta que ele faz é a do **processo**, não a da aplicação: com duas
réplicas atrás do proxy, cada uma tem o próprio balde, e o teto efetivo dobra —
o freio anunciado (300 por minuto) passa a valer 600 sem ninguém mudar
configuração nenhuma. Num dia de incidente, isso é a diferença entre conter e
não conter.

O de Redis resolve isso porque o balde passa a ser um só, compartilhado. Ele
também resolve dois problemas menores que o de memória tem por construção: a
contagem sobrevive a um reinício (hoje, reiniciar a API zera o freio de quem
está abusando) e as chaves expiram sozinhas, sem a varredura periódica que o
dicionário em memória precisa para não crescer para sempre.

**Quando o Redis cai, o freio não desaparece.** Cair para "deixa passar tudo"
abriria a porta exatamente no tipo de dia em que um Redis morre; cair para
"recusa tudo" derrubaria a aplicação por causa de um serviço auxiliar. O que
este módulo faz é voltar ao balde em memória e avisar no log: a proteção fica
por réplica — pior do que o ideal, melhor do que nenhuma — e o operador tem a
linha que explica.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Protocol

logger = logging.getLogger("ia_sdr.limitador")


class Limitador(Protocol):
    """Contrato único: consumir uma vaga da janela, ou dizer quanto falta."""

    def consumir(self, chave: str, *, limite: int, janela: int) -> tuple[bool, int]:
        """Devolve (permitido, segundos até liberar).

        `limite=0` recusa tudo — é o interruptor de emergência, e precisa
        funcionar sem estourar em nenhum dos backends.
        """
        ...


def chave_do_cliente(authorization: str, ip: str | None) -> str:
    """Quem é o cliente, para efeito de cota.

    Digest, não `hash()`: o builtin é aleatorizado por processo — o que num
    backend compartilhado seria pior do que inútil, porque duas réplicas
    colocariam o mesmo token em baldes diferentes — e truncá-lo em 32 bits faz
    dois tokens caírem no mesmo balde: uma empresa gastando a cota de outra, sem
    nada na tela que explique.
    """
    if authorization:
        return "token:" + hashlib.sha256(authorization.encode()).hexdigest()[:32]
    return f"ip:{ip or 'unknown'}"


class LimitadorEmMemoria:
    """Janela deslizante por processo. O que existia antes, agora isolado."""

    #: A cada tantas requisições, varre as chaves que não têm mais nada dentro
    #: da janela. Sem isso o dicionário só cresce: cada login emite um token
    #: novo — logo, uma chave nova — e cada IP visto deixa a sua para trás. Uma
    #: API em pé por semanas acumularia uma entrada por token já emitido, e
    #: quem quisesse derrubá-la só precisaria variar o cabeçalho.
    LIMPEZA_A_CADA = 1000

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._desde_a_limpeza = 0

    def consumir(self, chave: str, *, limite: int, janela: int) -> tuple[bool, int]:
        agora = time.monotonic()

        self._desde_a_limpeza += 1
        if self._desde_a_limpeza >= self.LIMPEZA_A_CADA:
            self._desde_a_limpeza = 0
            self._limpar(agora, janela)

        hits = self._hits[chave]
        while hits and agora - hits[0] > janela:
            hits.popleft()
        if len(hits) >= limite:
            # A fila pode estar vazia aqui: com `limite=0` nada foi registrado e
            # `hits[0]` estouraria, devolvendo 500 no lugar do 429 justamente no
            # momento em que alguém está contendo um incidente.
            espera = janela - (agora - hits[0]) if hits else janela
            return False, max(1, int(espera))
        hits.append(agora)
        return True, 0

    def _limpar(self, agora: float, janela: int) -> None:
        vencidas = [
            chave for chave, hits in self._hits.items() if not hits or agora - hits[-1] > janela
        ]
        for chave in vencidas:
            del self._hits[chave]


#: Janela deslizante num sorted set, em um passo atômico.
#:
#: Atômico importa aqui mais do que em qualquer outro lugar deste código: sem
#: isso, duas réplicas que leem "299 usados" no mesmo milissegundo deixam as
#: duas passarem, e o teto vira 301 — o bug que o backend compartilhado existia
#: para impedir. `ZREMRANGEBYSCORE` limpa o que saiu da janela, `ZCARD` conta o
#: que ficou, e o `PEXPIRE` faz a chave desaparecer sozinha quando o cliente
#: para de bater: nenhuma varredura, nenhum crescimento sem fim.
#:
#: O membro é um id aleatório, não o timestamp: duas requisições no mesmo
#: milissegundo têm o mesmo score, e no sorted set isso seria **um** membro só —
#: a segunda sobrescreveria a primeira e o cliente ganharia uma vaga de graça.
_SCRIPT = """
local chave = KEYS[1]
local agora = tonumber(ARGV[1])
local janela = tonumber(ARGV[2])
local limite = tonumber(ARGV[3])
local id = ARGV[4]

redis.call('ZREMRANGEBYSCORE', chave, 0, agora - janela)
local usados = redis.call('ZCARD', chave)
if usados >= limite then
  local mais_antigo = redis.call('ZRANGE', chave, 0, 0, 'WITHSCORES')
  local espera = janela
  if mais_antigo[2] then
    espera = janela - (agora - tonumber(mais_antigo[2]))
  end
  return {0, math.ceil(espera / 1000)}
end
redis.call('ZADD', chave, agora, id)
redis.call('PEXPIRE', chave, janela)
return {1, 0}
"""


class LimitadorRedis:
    """Balde único, compartilhado por todas as réplicas.

    Recebe o cliente já construído para o teste poder apontar para um Redis de
    verdade sem precisar de fábrica nem de monkeypatch.
    """

    #: Prefixo com versão: se algum dia a estrutura mudar, um deploy novo não
    #: passa a ler contagem gravada no formato antigo.
    PREFIXO = "ia_sdr:rl:v1:"

    def __init__(self, cliente, *, reserva: Limitador | None = None) -> None:
        self._redis = cliente
        self._script = cliente.register_script(_SCRIPT)
        #: Onde o freio cai quando o Redis não responde. Por réplica, portanto
        #: pior — e ainda assim proteção.
        self._reserva = reserva or LimitadorEmMemoria()
        self._avisado = False

    def consumir(self, chave: str, *, limite: int, janela: int) -> tuple[bool, int]:
        agora_ms = int(time.time() * 1000)
        try:
            permitido, espera = self._script(
                keys=[self.PREFIXO + chave],
                args=[agora_ms, janela * 1000, limite, uuid.uuid4().hex],
            )
            self._avisado = False
            return bool(permitido), int(espera)
        except Exception as erro:  # noqa: BLE001 - qualquer falha do Redis cai na reserva
            if not self._avisado:
                # Uma linha por episódio, não uma por requisição: um Redis fora
                # do ar geraria milhares de linhas idênticas e esconderia o
                # resto do log justamente quando ele é mais necessário.
                self._avisado = True
                logger.warning(
                    "Redis do limitador indisponível (%s): o freio volta a ser por "
                    "processo até ele responder. Com mais de uma réplica, o teto "
                    "efetivo é o teto vezes o número de réplicas.",
                    erro,
                )
            return self._reserva.consumir(chave, limite=limite, janela=janela)


def construir(url: str) -> Limitador:
    """O backend que a configuração pede. Sem `REDIS_URL`, o de memória.

    A importação do cliente fica aqui dentro de propósito: quem não usa Redis
    não paga por ela, e um `import redis` no topo do módulo tornaria a
    dependência obrigatória para rodar a aplicação.
    """
    if not url:
        return LimitadorEmMemoria()

    import redis  # noqa: PLC0415 - dependência opcional, importada quando pedida

    cliente = redis.Redis.from_url(
        url,
        # Tempos curtos porque isto está no caminho de **toda** requisição: um
        # Redis lento não pode virar uma API lenta. Estourando, cai na reserva.
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
        decode_responses=False,
    )
    logger.info("Limitador de requisições compartilhado via Redis")
    return LimitadorRedis(cliente)
