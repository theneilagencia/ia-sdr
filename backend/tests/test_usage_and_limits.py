"""Consumo, limites de plano e criptografia de segredos."""

from __future__ import annotations

import pytest

from app.billing.plans import PLAN_LIMITS, limits_for
from app.core.crypto import decrypt_json, decrypt_secret, encrypt_json, encrypt_secret
from app.core.errors import LimitExceeded
from app.db.models.platform import Plan, Tenant
from app.db.session import tenant_session
from app.services import limits, usage


def test_unidades_por_operacao_seguem_a_tabela():
    assert usage.UNIT_COST[usage.UsageKind.RESEARCH] == 1
    assert usage.UNIT_COST[usage.UsageKind.AI_MESSAGE] == 1
    assert usage.UNIT_COST[usage.UsageKind.DEEP_RESEARCH] == 5
    assert usage.UNIT_COST[usage.UsageKind.QUALIFICATION] == 2
    assert usage.UNIT_COST[usage.UsageKind.VOICE_INTERACTION] == 10


def test_consumo_acumula_e_resume_por_tipo(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        usage.record_usage(
            session, tenant_id=t["tenant_id"], kind=usage.UsageKind.RESEARCH, quantity=3
        )
        usage.record_usage(
            session,
            tenant_id=t["tenant_id"],
            kind=usage.UsageKind.DEEP_RESEARCH,
            cost_micro_usd=184_000,
        )

    with tenant_session(t["tenant_id"]) as session:
        resumo = usage.usage_summary(session, t["tenant_id"])

    assert resumo["ai_units_used"] == 8  # 3 * 1 + 1 * 5
    assert resumo["by_kind"]["research"]["units"] == 3
    assert resumo["estimated_cost_usd"] == pytest.approx(0.184)


def test_cota_de_ia_bloqueia_antes_de_gastar(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"ai_units_per_month": 5}
        usage.record_usage(
            session, tenant_id=t["tenant_id"], kind=usage.UsageKind.RESEARCH, quantity=4
        )

    with tenant_session(t["tenant_id"]) as session:
        usage.check_ai_budget(session, t["tenant_id"], 1)  # cabe
        with pytest.raises(LimitExceeded) as exc:
            usage.check_ai_budget(session, t["tenant_id"], 5)  # não cabe
    assert exc.value.details["limit"] == 5


def test_plano_starter_limita_uma_campanha(client, make_tenant, auth_headers):
    t = make_tenant(plan=Plan.STARTER.value)
    headers = auth_headers(t["email"], t["password"])

    primeira = client.post(
        "/api/v1/campaigns", headers=headers, json={"name": "Uma", "slug": "uma"}
    )
    assert primeira.status_code == 201

    segunda = client.post(
        "/api/v1/campaigns", headers=headers, json={"name": "Outra", "slug": "outra"}
    )
    assert segunda.status_code == 402
    assert segunda.json()["error"]["code"] == "limit_exceeded"


def test_override_contratual_vence_o_plano(make_tenant):
    t = make_tenant(plan=Plan.STARTER.value)
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"campaigns": 3}

    from app.db.models.sales import Campaign

    with tenant_session(t["tenant_id"]) as session:
        for i in range(3):
            limits.check_can_create_campaign(session, t["tenant_id"])
            session.add(
                Campaign(tenant_id=t["tenant_id"], name=f"C{i}", slug=f"c{i}")
            )
            session.flush()
        with pytest.raises(LimitExceeded):
            limits.check_can_create_campaign(session, t["tenant_id"])


def test_enterprise_e_ilimitado():
    efetivos = limits_for(Plan.ENTERPRISE)
    assert efetivos["campaigns"] == -1
    assert efetivos["ai_units_per_month"] == -1
    assert PLAN_LIMITS[Plan.GROWTH].campaigns == 10


def test_segredo_vai_e_volta_mas_nao_em_claro():
    cifrado = encrypt_secret("senha-do-cliente")
    assert "senha-do-cliente" not in cifrado
    assert decrypt_secret(cifrado) == "senha-do-cliente"

    credenciais = {"client_id": "abc", "client_secret": "xyz"}
    token = encrypt_json(credenciais)
    assert "xyz" not in token
    assert decrypt_json(token) == credenciais


def test_cifragem_nao_e_deterministica():
    """Dois segredos iguais não produzem o mesmo texto cifrado."""
    assert encrypt_secret("igual") != encrypt_secret("igual")
