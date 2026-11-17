"""Contrato entre o orquestrador e quem realmente executa um agente.

Além do contrato, vive aqui o que é da plataforma e não de um agente: o teto de
custo por execução e o gasto que uma falha carrega consigo. Os quatro agentes
gastam dinheiro de verdade a cada chamada, e as duas coisas que não podem
depender de cada executor lembrar são justamente essas — parar de gastar quando
passou do teto, e não perder de vista o que já foi gasto quando a execução não
chega ao fim.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from app.ai.pricing import cost_micro_usd
from app.core.config import settings
from app.core.errors import AppError
from app.orchestrator.context_builder import AgentContext
from app.orchestrator.envelope import JobEnvelope


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """O que uma execução devolve ao orquestrador.

    Separar `output` de custo e tokens é o que permite ao runner gravar
    consumo e margem sem saber nada sobre o agente que rodou.
    """

    output: dict
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    metadata: dict = field(default_factory=dict)


class AgentExecutor(Protocol):
    def __call__(
        self, session: Session, context: AgentContext, envelope: JobEnvelope
    ) -> ExecutionResult: ...


class CostCeilingExceeded(AppError):
    """O teto de custo por execução, estourado.

    Vive aqui, e não em um agente específico, porque o teto é da plataforma: um
    agente que pode iterar — buscar na web, retomar turno pausado — pode gastar
    muito mais do que quem aperta o botão imagina. O limite transforma "caro
    demais" em falha explícita, em vez de uma fatura surpresa no fim do mês.
    """

    code = "cost_ceiling_exceeded"
    status_code = 402


def novo_consumo() -> dict[str, int]:
    """O acumulador de tokens de uma execução, zerado."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def custo_de(model: str, usage: dict) -> int:
    return cost_micro_usd(
        model,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read_tokens"],
        cache_write_tokens=usage["cache_write_tokens"],
    )


def custo_dentro_do_teto(model: str, usage: dict) -> int:
    """O custo acumulado até agora — ou a recusa, se já passou do teto.

    Chamado **depois de cada turno**, e não só no fim. Um agente com busca na
    web pode pausar e retomar várias vezes; conferir o teto uma única vez, ao
    final, significa pagar todos os turnos antes de descobrir que o primeiro já
    tinha estourado o limite.
    """
    custo = custo_de(model, usage)
    if custo > settings.ai_max_cost_micro_usd:
        raise CostCeilingExceeded(
            "Execução ultrapassou o teto de custo configurado",
            details={"cost_micro_usd": custo, "ceiling": settings.ai_max_cost_micro_usd},
        )
    return custo


@dataclass(frozen=True, slots=True)
class Spend:
    """O que uma execução já gastou quando ela falha.

    Token gasto não volta porque a execução deu errado. Sem carregar o gasto na
    exceção, a falha some da contabilidade: o run fica com zero token, o evento
    de consumo não nasce e a margem do mês mente. Pior, o job é tentado de novo
    — e o segundo gasto também fica invisível.
    """

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0


def gasto_de(exc: BaseException) -> Spend | None:
    """O gasto que uma exceção carrega, se carrega."""
    spend = getattr(exc, "spend", None)
    return spend if isinstance(spend, Spend) else None


@contextmanager
def cobrando_a_falha(model: str, usage: dict) -> Iterator[None]:
    """Qualquer falha daqui para baixo sai carregando o que já foi gasto.

    O runner lê esse gasto e o grava no run e no consumo do tenant — com zero
    unidade, porque o cliente não paga unidade por execução que falhou, mas com
    o custo real, porque a Anthropic cobrou.

    Vale para qualquer exceção, e não só para as de domínio: o erro mais provável
    em produção é o SDK da Anthropic levantando o seu próprio — corte de conexão,
    429, 500 do outro lado —, e ele acontece depois de a chamada já ter sido
    cobrada tantas vezes quantas o cliente tentou.
    """
    try:
        yield
    except Exception as exc:
        if gasto_de(exc) is None and (usage["input_tokens"] or usage["output_tokens"]):
            with suppress(AttributeError, TypeError):
                exc.spend = Spend(  # type: ignore[attr-defined]
                    model=model,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    cost_micro_usd=custo_de(model, usage),
                )
        raise
