"""Registro dos executores de agente.

Ligar o modelo de verdade é registrar um executor — nada no caminho do
orquestrador muda.
"""

from __future__ import annotations

import logging

from app.ai.client import is_configured
from app.db.models.ai import AgentKind
from app.orchestrator.executors.base import AgentExecutor, ExecutionResult
from app.orchestrator.executors.echo import echo_executor
from app.orchestrator.executors.research import ResearchExecutor

logger = logging.getLogger("ia_sdr.agents")


def register_default_executors() -> None:
    """Registra o que está pronto, e diz em voz alta o que não está."""
    from app.orchestrator.runner import register_executor

    if not is_configured():
        logger.warning(
            "ANTHROPIC_API_KEY ausente: agentes rodam com o executor de eco, "
            "sem chamada de modelo"
        )
        return

    register_executor(AgentKind.RESEARCH.value, ResearchExecutor())
    logger.info("Research Agent registrado com execução real")


__all__ = [
    "AgentExecutor",
    "ExecutionResult",
    "ResearchExecutor",
    "echo_executor",
    "register_default_executors",
]
