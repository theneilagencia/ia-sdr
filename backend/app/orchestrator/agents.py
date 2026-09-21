"""Catálogo de agentes da AI Sales Workforce.

O MVP tem um único papel — SDR — desdobrado em quatro agentes. O motor é o
mesmo para todos os tenants; o que muda é o contexto montado para cada um.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models.ai import AgentKind
from app.services.usage import UsageKind


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    kind: AgentKind
    default_name: str
    default_model: str
    usage_kind: UsageKind
    base_instructions: str
    tools: tuple[str, ...] = ()


AGENT_DEFINITIONS: dict[AgentKind, AgentDefinition] = {
    AgentKind.RESEARCH: AgentDefinition(
        kind=AgentKind.RESEARCH,
        default_name="Research Agent",
        default_model="claude-sonnet-5",
        usage_kind=UsageKind.RESEARCH,
        tools=("web_search", "company_lookup"),
        base_instructions=(
            "Pesquise a conta-alvo e produza fatos verificáveis, com fonte, "
            "úteis para avaliar aderência ao ICP da campanha. "
            "Não invente dados; o que não encontrar, marque como desconhecido."
        ),
    ),
    AgentKind.OUTREACH: AgentDefinition(
        kind=AgentKind.OUTREACH,
        default_name="Outreach Agent",
        default_model="claude-sonnet-5",
        usage_kind=UsageKind.AI_MESSAGE,
        tools=("send_email",),
        base_instructions=(
            "Escreva a primeira abordagem usando a oferta e o tom de voz do "
            "Company Brain e os achados da pesquisa. Seja específico, curto e "
            "honesto. Nunca prometa o que não está no playbook do cliente."
        ),
    ),
    AgentKind.CONVERSATION: AgentDefinition(
        kind=AgentKind.CONVERSATION,
        default_name="Conversation Agent",
        default_model="claude-sonnet-5",
        usage_kind=UsageKind.AI_MESSAGE,
        tools=("send_email", "knowledge_search"),
        base_instructions=(
            "Conduza a conversa respondendo com base na base de conhecimento "
            "do tenant. Se a resposta não estiver no contexto, diga que vai "
            "confirmar e escale para um humano."
        ),
    ),
    AgentKind.QUALIFICATION: AgentDefinition(
        kind=AgentKind.QUALIFICATION,
        default_name="Qualification Agent",
        default_model="claude-sonnet-5",
        usage_kind=UsageKind.QUALIFICATION,
        tools=("calendar_propose",),
        base_instructions=(
            "Avalie o prospect contra os critérios de qualificação da campanha. "
            "Devolva veredito, critério a critério, com a evidência que sustenta "
            "cada um."
        ),
    ),
}
