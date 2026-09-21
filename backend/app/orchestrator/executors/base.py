"""Contrato entre o orquestrador e quem realmente executa um agente."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

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
