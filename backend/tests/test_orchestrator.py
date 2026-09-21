"""Orquestrador: o contexto que chega ao modelo é só do tenant dono do job."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import CrossTenantAccess, LimitExceeded
from app.db.models.ai import AgentKind, AIAgent
from app.db.models.knowledge import CompanyProfile, KnowledgeChunk, KnowledgeDocument
from app.db.models.sales import Campaign
from app.db.session import tenant_session
from app.orchestrator.context_builder import assert_same_tenant, build_context
from app.orchestrator.envelope import InvalidEnvelope, JobEnvelope
from app.orchestrator.runner import new_job, run_job


def _seed(tenant_id: uuid.UUID, *, marca: str) -> uuid.UUID:
    """Cria Company Brain, campanha e um documento indexado para o tenant."""
    with tenant_session(tenant_id) as session:
        session.add(
            CompanyProfile(
                tenant_id=tenant_id,
                legal_name=f"{marca} Ltda",
                positioning=f"Vendemos {marca}",
                icp={"industria": marca},
            )
        )
        campaign = Campaign(
            tenant_id=tenant_id,
            name=f"Campanha {marca}",
            slug=f"campanha-{marca.lower()}",
            icp={"cargo": "CFO", "pais": marca},
        )
        session.add(campaign)
        doc = KnowledgeDocument(
            tenant_id=tenant_id, title=f"Playbook {marca}", status="indexed"
        )
        session.add(doc)
        session.flush()
        session.add(
            KnowledgeChunk(
                tenant_id=tenant_id,
                document_id=doc.id,
                ordinal=0,
                content=f"Segredo comercial de {marca}",
            )
        )
        session.flush()
        return campaign.id


def test_contexto_carrega_apenas_conhecimento_do_proprio_tenant(make_tenant):
    a = make_tenant()
    b = make_tenant()
    campanha_a = _seed(a["tenant_id"], marca="Mineracao")
    _seed(b["tenant_id"], marca="ERP")

    envelope = new_job(
        tenant_id=a["tenant_id"], agent="research", campaign_id=campanha_a
    )
    with tenant_session(a["tenant_id"]) as session:
        contexto = build_context(session, envelope)

    assert contexto.company_brain["positioning"] == "Vendemos Mineracao"
    assert contexto.campaign["name"] == "Campanha Mineracao"
    conteudos = " ".join(chunk["content"] for chunk in contexto.knowledge)
    assert "Mineracao" in conteudos
    assert "ERP" not in conteudos


def test_campanha_de_outro_tenant_nao_entra_no_contexto(make_tenant):
    a = make_tenant()
    b = make_tenant()
    campanha_b = _seed(b["tenant_id"], marca="ERP")

    envelope = new_job(tenant_id=a["tenant_id"], agent="research", campaign_id=campanha_b)
    with tenant_session(a["tenant_id"]) as session:
        with pytest.raises(Exception) as exc:
            build_context(session, envelope)
    assert exc.value.code in {"not_found", "cross_tenant_access"}


def test_segunda_barreira_dispara_se_o_rls_for_contornado(make_tenant):
    """Se um objeto de outro tenant chegar na montagem, o erro é explícito."""
    a = make_tenant()
    b = make_tenant()
    agente_de_b = AIAgent(tenant_id=b["tenant_id"], kind="research", name="Agente B")
    with pytest.raises(CrossTenantAccess):
        assert_same_tenant(agente_de_b, a["tenant_id"])


def test_envelope_sem_tenant_e_rejeitado():
    with pytest.raises(InvalidEnvelope):
        JobEnvelope.from_dict({"agent": "research"})


def test_envelope_com_agente_desconhecido_e_rejeitado():
    with pytest.raises(InvalidEnvelope):
        JobEnvelope.from_dict({"tenant_id": str(uuid.uuid4()), "agent": "faxina"})


def test_envelope_sobrevive_a_ida_e_volta_pela_fila():
    original = JobEnvelope(
        tenant_id=uuid.uuid4(),
        agent=AgentKind.OUTREACH,
        campaign_id=uuid.uuid4(),
        entity_type="company",
        entity_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        params={"tom": "direto"},
    )
    devolta = JobEnvelope.from_dict(original.to_dict())
    assert devolta == original


def test_run_registra_execucao_consumo_e_auditoria(make_tenant):
    a = make_tenant()
    campanha = _seed(a["tenant_id"], marca="Mineracao")

    envelope = new_job(
        tenant_id=a["tenant_id"],
        agent="research",
        campaign_id=campanha,
        user_id=a["user_id"],
        entity_type="company",
        entity_id=uuid.uuid4(),
    )
    with tenant_session(a["tenant_id"]) as session:
        run = run_job(session, envelope)
        run_id, digest = run.id, run.context_digest

    from sqlalchemy import select

    from app.db.models.platform import AuditLog, UsageEvent

    with tenant_session(a["tenant_id"]) as session:
        evento = session.execute(select(UsageEvent)).scalars().one()
        assert evento.kind == "research" and evento.units == 1
        assert evento.agent_run_id == run_id
        acoes = {a.action for a in session.execute(select(AuditLog)).scalars()}
        assert "agent.run.succeeded" in acoes
    assert digest  # o contexto usado fica auditável


def test_run_e_rejeitado_quando_a_cota_acaba(make_tenant):
    from sqlalchemy import select

    from app.db.models.ai import AgentRun
    from app.db.models.platform import Tenant

    a = make_tenant()
    campanha = _seed(a["tenant_id"], marca="Mineracao")
    with tenant_session(a["tenant_id"]) as session:
        session.get(Tenant, a["tenant_id"]).limit_overrides = {"ai_units_per_month": 0}

    envelope = new_job(tenant_id=a["tenant_id"], agent="research", campaign_id=campanha)
    with pytest.raises(LimitExceeded):
        with tenant_session(a["tenant_id"]) as session:
            run_job(session, envelope)

    # A execução recusada precisa ficar registrada, não sumir.
    with tenant_session(a["tenant_id"]) as session:
        runs = session.execute(select(AgentRun)).scalars().all()
    assert [r.status for r in runs] == ["rejected"]
