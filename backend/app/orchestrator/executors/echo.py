"""Executor de desenvolvimento: não chama modelo nenhum.

É o que roda quando não há chave de API configurada. Mantém o caminho inteiro
exercitável — contexto, cota, registro, consumo, auditoria — sem gastar um
centavo e sem depender de rede.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.orchestrator.context_builder import AgentContext
from app.orchestrator.envelope import JobEnvelope
from app.orchestrator.executors.base import ExecutionResult


def echo_executor(
    session: Session, context: AgentContext, envelope: JobEnvelope
) -> ExecutionResult:
    return ExecutionResult(
        output={
            "agent": envelope.agent.value,
            "context_digest": context.digest(),
            "knowledge_chunks": len(context.knowledge),
            "campaign": (context.campaign or {}).get("name"),
            "note": "executor de desenvolvimento: nenhuma chamada de modelo realizada",
        },
        model=None,
    )
