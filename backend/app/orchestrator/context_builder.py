"""Montagem do contexto que vai para o modelo.

    Tenant -> Company Brain -> Campaign -> ICP -> Knowledge -> Instruções -> Task

A IA só recebe o que pertence àquele contexto. Toda linha carregada aqui é
verificada contra o tenant do envelope: o RLS já filtra, e esta é a segunda
barreira, para o caso de alguém abrir uma sessão sem escopo por engano.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import CrossTenantAccess, NotFound
from app.db.models.ai import AIAgent
from app.db.models.engagement import Message, MessageDirection
from app.db.models.knowledge import CompanyProfile, KnowledgeChunk
from app.db.models.platform import Tenant
from app.db.models.sales import Campaign
from app.orchestrator.agents import AGENT_DEFINITIONS
from app.orchestrator.envelope import JobEnvelope
from app.services import knowledge as knowledge_service

MAX_KNOWLEDGE_CHUNKS = 20
#: A pergunta vira tsquery; uma mensagem inteira de lead, com assinatura e
#: histórico citado, só adiciona ruído ao ranqueamento.
MAX_QUERY_CHARS = 600


def assert_same_tenant(obj, tenant_id: uuid.UUID) -> None:
    """Segunda barreira de isolamento. Se disparar, algo está muito errado."""
    obj_tenant = getattr(obj, "tenant_id", None)
    if obj_tenant is not None and obj_tenant != tenant_id:
        raise CrossTenantAccess(
            "Objeto de outro tenant alcançado durante a montagem de contexto",
            details={
                "expected_tenant": str(tenant_id),
                "found_tenant": str(obj_tenant),
                "object": type(obj).__name__,
            },
        )


@dataclass(slots=True)
class AgentContext:
    tenant: dict
    company_brain: dict
    campaign: dict | None
    icp: dict
    knowledge: list[dict]
    agent: dict
    task: dict
    job_id: str = ""
    policies: dict = field(default_factory=dict)

    def digest(self) -> str:
        """Hash do contexto — vai para o agent_run e permite auditar depois."""
        blob = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def to_dict(self) -> dict:
        return asdict(self)


def build_context(session: Session, envelope: JobEnvelope) -> AgentContext:
    tenant_id = envelope.tenant_id

    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise NotFound("Tenant inexistente ou inativo")

    brain = session.execute(
        select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_id)
    ).scalar_one_or_none()
    if brain is not None:
        assert_same_tenant(brain, tenant_id)

    campaign = None
    if envelope.campaign_id:
        campaign = session.get(Campaign, envelope.campaign_id)
        if campaign is None:
            raise NotFound("Campanha não encontrada neste tenant")
        assert_same_tenant(campaign, tenant_id)

    definition = AGENT_DEFINITIONS[envelope.agent]
    agent_config = session.execute(
        select(AIAgent)
        .where(AIAgent.tenant_id == tenant_id)
        .where(AIAgent.kind == envelope.agent.value)
        .where(AIAgent.is_active.is_(True))
        .limit(1)
    ).scalar_one_or_none()
    if agent_config is not None:
        assert_same_tenant(agent_config, tenant_id)

    knowledge = _load_knowledge(session, tenant_id, campaign, _knowledge_query(session, envelope))

    return AgentContext(
        job_id=envelope.job_id,
        tenant={
            "id": str(tenant.id),
            "name": tenant.name,
            "plan": tenant.plan,
        },
        company_brain=_brain_payload(brain),
        campaign=_campaign_payload(campaign),
        icp=(campaign.icp if campaign else (brain.icp if brain else {})) or {},
        knowledge=knowledge,
        agent={
            "kind": envelope.agent.value,
            "name": agent_config.name if agent_config else definition.default_name,
            "model": agent_config.model if agent_config else definition.default_model,
            "instructions": (
                agent_config.instructions
                if agent_config and agent_config.instructions
                else definition.base_instructions
            ),
            "tools": list(agent_config.tools) if agent_config else list(definition.tools),
            "max_output_tokens": agent_config.max_output_tokens if agent_config else 2000,
        },
        task={
            "type": envelope.agent.value,
            "entity_type": envelope.entity_type,
            "entity_id": str(envelope.entity_id) if envelope.entity_id else None,
            "params": envelope.params,
        },
        policies=(brain.ai_policies if brain else {}) or {},
    )


def _brain_payload(brain: CompanyProfile | None) -> dict:
    if brain is None:
        return {}
    return {
        "legal_name": brain.legal_name,
        "website": brain.website,
        "positioning": brain.positioning,
        "products": brain.products,
        "services": brain.services,
        "personas": brain.personas,
        "pricing": brain.pricing,
        "cases": brain.cases,
        "faqs": brain.faqs,
        "objections": brain.objections,
        "competitors": brain.competitors,
        "sales_playbook": brain.sales_playbook,
        "brand_voice": brain.brand_voice,
    }


def _campaign_payload(campaign: Campaign | None) -> dict | None:
    if campaign is None:
        return None
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "status": campaign.status,
        "objective": campaign.objective,
        "target_geography": campaign.target_geography,
        "personas": campaign.personas,
        "offer": campaign.offer,
        "messaging": campaign.messaging,
        "qualification_criteria": campaign.qualification_criteria,
        "channels": campaign.channels,
        "daily_limits": campaign.daily_limits,
    }


def _knowledge_query(session: Session, envelope: JobEnvelope) -> str | None:
    """A pergunta que a recuperação vai responder.

    Quem enfileira pode dizer explicitamente (`params["knowledge_query"]`). Se
    não disser e o trabalho for sobre uma conversa, a pergunta é a última
    mensagem do lead — que é, literalmente, o que o agente precisa responder.
    """
    explicita = envelope.params.get("knowledge_query")
    if isinstance(explicita, str) and explicita.strip():
        return explicita.strip()[:MAX_QUERY_CHARS]

    if envelope.entity_type != "conversation" or envelope.entity_id is None:
        return None

    ultima = session.execute(
        select(Message)
        .where(Message.conversation_id == envelope.entity_id)
        .where(Message.direction == MessageDirection.INBOUND.value)
        .order_by(Message.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ultima is None:
        return None
    assert_same_tenant(ultima, envelope.tenant_id)
    texto = f"{ultima.subject or ''} {ultima.body or ''}".strip()
    return texto[:MAX_QUERY_CHARS] or None


def _load_knowledge(
    session: Session,
    tenant_id: uuid.UUID,
    campaign: Campaign | None,
    query: str | None = None,
) -> list[dict]:
    """Os trechos mais relevantes deste tenant, visíveis para esta campanha.

    Antes isto devolvia os mais recentes. Com dez documentos dá no mesmo; com
    cem, o agente recebe o que foi carregado por último e responde "não está na
    minha base" sobre algo que está — e escalar para humano à toa, todo dia, é
    o jeito mais rápido de a empresa desligar o agente.

    O escopo por campanha e o filtro por tenant são aplicados dentro da busca;
    a verificação aqui é a segunda barreira, que deve ser redundante.
    """
    achados = knowledge_service.search_chunks(
        session,
        tenant_id,
        query,
        campaign_id=campaign.id if campaign is not None else None,
        limit=MAX_KNOWLEDGE_CHUNKS,
    )
    out: list[dict] = []
    for achado in achados:
        chunk = session.get(KnowledgeChunk, uuid.UUID(achado["chunk_id"]))
        if chunk is not None:
            assert_same_tenant(chunk, tenant_id)
        out.append(
            {
                "document_id": achado["document_id"],
                "title": achado["title"],
                "ordinal": achado["ordinal"],
                "content": achado["content"],
                "relevance": achado["relevance"],
            }
        )
    return out
