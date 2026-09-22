"""Modelos do domínio.

Importar este pacote registra todo o metadata no `Base` — o Alembic depende
disso para autogerar migrations completas.
"""

from app.db.base import Base
from app.db.models.ai import (
    AgentKind,
    AgentRun,
    AIAgent,
    Integration,
    IntegrationProvider,
    RunStatus,
)
from app.db.models.engagement import (
    Conversation,
    EnrollmentStatus,
    Meeting,
    Message,
    MessageDirection,
    MessageStatus,
    Qualification,
    Sequence,
    SequenceEnrollment,
)
from app.db.models.jobs import Job, JobKind, JobStatus
from app.db.models.knowledge import (
    CompanyProfile,
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.db.models.platform import (
    AuditLog,
    Membership,
    Plan,
    SubscriptionStatus,
    Tenant,
    UsageEvent,
    User,
)
from app.db.models.sales import (
    Campaign,
    CampaignStatus,
    Company,
    Contact,
    Prospect,
    ProspectStatus,
    Research,
    Score,
)

#: Tabelas que carregam dados de cliente e, portanto, recebem RLS.
#:
#: A lista é mantida à mão, e por isso a suíte prova que ela está completa:
#: `test_rls.py` falha se alguma tabela mapeada tiver `tenant_id` e não estiver
#: aqui. Foi assim que `memberships` apareceu — carregava `tenant_id` desde a
#: primeira migration e não tinha política nenhuma.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "audit_logs",
    "memberships",
    "usage_events",
    "companies",
    "contacts",
    "campaigns",
    "prospects",
    "research",
    "scores",
    "sequences",
    "sequence_enrollments",
    "conversations",
    "messages",
    "qualifications",
    "meetings",
    "company_profiles",
    "knowledge_documents",
    "knowledge_chunks",
    "ai_agents",
    "agent_runs",
    "integrations",
    "jobs",
)

__all__ = [
    "Base",
    "TENANT_SCOPED_TABLES",
    "AIAgent",
    "AgentKind",
    "AgentRun",
    "AuditLog",
    "Campaign",
    "CampaignStatus",
    "Company",
    "CompanyProfile",
    "Contact",
    "Conversation",
    "DocumentStatus",
    "Integration",
    "IntegrationProvider",
    "Job",
    "JobKind",
    "JobStatus",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "Meeting",
    "Membership",
    "Message",
    "MessageDirection",
    "MessageStatus",
    "Plan",
    "Prospect",
    "ProspectStatus",
    "Qualification",
    "Research",
    "RunStatus",
    "Score",
    "Sequence",
    "SequenceEnrollment",
    "EnrollmentStatus",
    "SubscriptionStatus",
    "Tenant",
    "UsageEvent",
    "User",
]
