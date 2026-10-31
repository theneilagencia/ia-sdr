"""Planos e limites.

Cobrança não entra no primeiro sprint, mas o modelo entra. Quando o billing
chegar, não se refaz o modelo de dados — só se liga o gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models.platform import Plan

UNLIMITED = -1


@dataclass(frozen=True, slots=True)
class PlanLimits:
    campaigns: int
    prospects_per_month: int
    users: int
    email_accounts: int
    ai_units_per_month: int
    knowledge_documents: int
    #: Teto de **custo real** em dólares por mês, o freio que a unidade não dá.
    #: A unidade é a moeda que o cliente compra; este é o que a Anthropic cobra.
    #: Nasce ilimitado nos três planos de propósito: quanto vale a pena gastar
    #: com cada cliente é decisão comercial de quem opera a plataforma, não
    #: número para um default inventar. Ligado por contrato, no painel.
    ai_cost_usd_per_month: int = UNLIMITED
    features: frozenset[str] = field(default_factory=frozenset)

    def as_dict(self) -> dict:
        return {
            "campaigns": self.campaigns,
            "prospects_per_month": self.prospects_per_month,
            "users": self.users,
            "email_accounts": self.email_accounts,
            "ai_units_per_month": self.ai_units_per_month,
            "ai_cost_usd_per_month": self.ai_cost_usd_per_month,
            "knowledge_documents": self.knowledge_documents,
            "features": sorted(self.features),
        }


PLAN_LIMITS: dict[Plan, PlanLimits] = {
    Plan.STARTER: PlanLimits(
        campaigns=1,
        prospects_per_month=1_000,
        users=2,
        email_accounts=1,
        ai_units_per_month=5_000,
        knowledge_documents=50,
        features=frozenset({"research", "outreach", "conversation"}),
    ),
    Plan.GROWTH: PlanLimits(
        campaigns=10,
        prospects_per_month=10_000,
        users=10,
        email_accounts=5,
        ai_units_per_month=60_000,
        knowledge_documents=500,
        features=frozenset({"research", "outreach", "conversation", "qualification", "crm"}),
    ),
    Plan.ENTERPRISE: PlanLimits(
        campaigns=UNLIMITED,
        prospects_per_month=UNLIMITED,
        users=UNLIMITED,
        email_accounts=UNLIMITED,
        ai_units_per_month=UNLIMITED,
        knowledge_documents=UNLIMITED,
        # `voice` e `sso` saíram desta lista. Não é redução de escopo: nenhum dos
        # dois existe, e o painel mostrava "recursos: ..., voice, sso" para quem
        # opera — que é como um recurso inexistente entra numa proposta comercial.
        # Voltam no dia em que houver código atrás deles; o teste
        # `test_plano_nao_anuncia_recurso_inexistente` falha se voltarem antes.
        features=frozenset({"research", "outreach", "conversation", "qualification", "crm"}),
    ),
}


def limits_for(plan: str | Plan, overrides: dict | None = None) -> dict:
    """Limites efetivos do tenant: o do plano, com overrides contratuais."""
    plan_enum = Plan(plan) if not isinstance(plan, Plan) else plan
    effective = PLAN_LIMITS[plan_enum].as_dict()
    for key, value in (overrides or {}).items():
        if key in effective:
            effective[key] = value
    return effective
