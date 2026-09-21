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
from app.db.models.knowledge import CompanyProfile, KnowledgeChunk, KnowledgeDocument
from app.db.models.platform import Tenant
from app.db.models.sales import Campaign
from app.orchestrator.agents import AGENT_DEFINITIONS
from app.orchestrator.envelope import JobEnvelope

MAX_KNOWLEDGE_CHUNKS = 20


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

    knowledge = _load_knowledge(session, tenant_id, campaign)

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
                agent_config.instructions if agent_config and agent_config.instructions
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


def _load_knowledge(
    session: Session, tenant_id: uuid.UUID, campaign: Campaign | None
) -> list[dict]:
    """Só documentos deste tenant, e só os visíveis para esta campanha.

    Documento sem `campaign_ids` vale para o tenant inteiro; com lista, só
    para as campanhas listadas.
    """
    stmt = (
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(KnowledgeChunk.tenant_id == tenant_id)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .where(KnowledgeDocument.status == "indexed")
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeChunk.ordinal)
        .limit(MAX_KNOWLEDGE_CHUNKS * 5)
    )
    out: list[dict] = []
    for chunk, document in session.execute(stmt).all():
        assert_same_tenant(chunk, tenant_id)
        assert_same_tenant(document, tenant_id)
        scope = document.campaign_ids or []
        if scope and campaign is not None and str(campaign.id) not in {str(c) for c in scope}:
            continue
        if scope and campaign is None:
            continue
        out.append(
            {
                "document_id": str(document.id),
                "title": document.title,
                "ordinal": chunk.ordinal,
                "content": chunk.content,
            }
        )
        if len(out) >= MAX_KNOWLEDGE_CHUNKS:
            break
    return out
