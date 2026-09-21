"""Envelope de job.

Nenhum trabalho assíncrono roda sem envelope. É ele que impede o clássico:
worker pega um job da Empresa A e executa com a configuração da Empresa B.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError
from app.db.models.ai import AgentKind
from app.tenancy.context import TenantContext, system_context


class InvalidEnvelope(AppError):
    code = "invalid_envelope"
    status_code = 400


def _as_uuid(value: Any, field_name: str, *, required: bool = True) -> uuid.UUID | None:
    if value is None:
        if required:
            raise InvalidEnvelope(f"Campo obrigatório ausente no envelope: {field_name}")
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise InvalidEnvelope(f"Campo {field_name} não é um UUID válido") from exc


@dataclass(frozen=True, slots=True)
class JobEnvelope:
    tenant_id: uuid.UUID
    agent: AgentKind
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    campaign_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "tenant_id": str(self.tenant_id),
            "agent": self.agent.value,
            "campaign_id": str(self.campaign_id) if self.campaign_id else None,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict) -> JobEnvelope:
        if not isinstance(data, dict):
            raise InvalidEnvelope("Envelope precisa ser um objeto")
        agent_raw = data.get("agent")
        try:
            agent = AgentKind(agent_raw)
        except ValueError as exc:
            raise InvalidEnvelope(f"Agente desconhecido: {agent_raw!r}") from exc
        return cls(
            tenant_id=_as_uuid(data.get("tenant_id"), "tenant_id"),
            agent=agent,
            job_id=str(data.get("job_id") or uuid.uuid4().hex),
            campaign_id=_as_uuid(data.get("campaign_id"), "campaign_id", required=False),
            entity_type=data.get("entity_type"),
            entity_id=_as_uuid(data.get("entity_id"), "entity_id", required=False),
            user_id=_as_uuid(data.get("user_id"), "user_id", required=False),
            params=data.get("params") or {},
        )

    def context(self) -> TenantContext:
        """O contexto de execução derivado do envelope — nunca do processo."""
        return system_context(self.tenant_id)
