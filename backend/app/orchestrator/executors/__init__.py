"""Registro dos executores de agente.

Os quatro agentes são registrados sempre. Quem decide se há chave para rodar é
o tenant, no momento da execução — uma empresa configurada trabalha mesmo que
a do lado ainda não tenha configurado nada.

O executor de eco continua aqui para desenvolvimento e teste, mas não é mais
usado como reserva silenciosa: fingir que trabalhou é pior do que dizer que
falta configurar.
"""

from __future__ import annotations

import logging

from app.db.models.ai import AgentKind
from app.orchestrator.executors.base import AgentExecutor, ExecutionResult
from app.orchestrator.executors.conversation import ConversationExecutor
from app.orchestrator.executors.echo import echo_executor
from app.orchestrator.executors.outreach import OutreachExecutor
from app.orchestrator.executors.qualification import QualificationExecutor
from app.orchestrator.executors.research import ResearchExecutor

logger = logging.getLogger("ia_sdr.agents")


def register_default_executors() -> None:
    from app.orchestrator.runner import register_executor

    register_executor(AgentKind.RESEARCH.value, ResearchExecutor())
    register_executor(AgentKind.OUTREACH.value, OutreachExecutor())
    register_executor(AgentKind.CONVERSATION.value, ConversationExecutor())
    register_executor(AgentKind.QUALIFICATION.value, QualificationExecutor())
    logger.info("Os quatro agentes registrados; a chave vem de cada tenant")


__all__ = [
    "AgentExecutor",
    "ConversationExecutor",
    "ExecutionResult",
    "OutreachExecutor",
    "QualificationExecutor",
    "ResearchExecutor",
    "echo_executor",
    "register_default_executors",
]
