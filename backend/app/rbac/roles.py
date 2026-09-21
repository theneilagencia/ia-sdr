"""RBAC simples, porém real, desde o MVP."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Permission(StrEnum):
    # Tenant / billing
    TENANT_READ = "tenant:read"
    TENANT_WRITE = "tenant:write"
    BILLING_MANAGE = "billing:manage"
    # Usuários
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    # Operação comercial
    CAMPAIGN_READ = "campaign:read"
    CAMPAIGN_WRITE = "campaign:write"
    PROSPECT_READ = "prospect:read"
    PROSPECT_WRITE = "prospect:write"
    CONVERSATION_READ = "conversation:read"
    CONVERSATION_WRITE = "conversation:write"
    MEETING_READ = "meeting:read"
    MEETING_WRITE = "meeting:write"
    # Conhecimento e IA
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"
    AGENT_READ = "agent:read"
    AGENT_WRITE = "agent:write"
    AGENT_RUN = "agent:run"
    # Infra do tenant
    INTEGRATION_READ = "integration:read"
    INTEGRATION_WRITE = "integration:write"
    AUDIT_READ = "audit:read"
    USAGE_READ = "usage:read"


_READ_ONLY = {
    Permission.TENANT_READ,
    Permission.USER_READ,
    Permission.CAMPAIGN_READ,
    Permission.PROSPECT_READ,
    Permission.CONVERSATION_READ,
    Permission.MEETING_READ,
    Permission.KNOWLEDGE_READ,
    Permission.AGENT_READ,
    Permission.INTEGRATION_READ,
    Permission.USAGE_READ,
}

_OPERATOR = _READ_ONLY | {
    Permission.CAMPAIGN_WRITE,
    Permission.PROSPECT_WRITE,
    Permission.CONVERSATION_WRITE,
    Permission.MEETING_WRITE,
    Permission.KNOWLEDGE_WRITE,
    Permission.AGENT_RUN,
}

_ADMIN = _OPERATOR | {
    Permission.TENANT_WRITE,
    Permission.USER_WRITE,
    Permission.AGENT_WRITE,
    Permission.INTEGRATION_WRITE,
    Permission.AUDIT_READ,
}

_OWNER = _ADMIN | {Permission.BILLING_MANAGE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_READ_ONLY),
    Role.OPERATOR: frozenset(_OPERATOR),
    Role.ADMIN: frozenset(_ADMIN),
    Role.OWNER: frozenset(_OWNER),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def permissions_for(role: Role) -> list[str]:
    return sorted(p.value for p in ROLE_PERMISSIONS[role])
