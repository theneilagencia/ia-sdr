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


def test_entrega_repetida_do_mesmo_job_nao_executa_duas_vezes(make_tenant):
    """Job de agente que passa de quinze minutos volta para a fila **rodando**.

    O `requeue_stale` considera órfão o que não concluiu nesse prazo — e não tem
    como saber se o worker morreu ou se a pesquisa ainda está buscando na web.
    Sem idempotência, o segundo worker pesquisaria a mesma conta de novo, pagaria
    de novo e deixaria dois rascunhos quase iguais esperando aprovação.

    O `job_id` do envelope é estável (vive no payload do job e sobrevive ao
    requeue), então ele é a chave: execução já feita é devolvida, não refeita.
    """
    from sqlalchemy import func, select

    from app.db.models.ai import AgentRun
    from app.db.models.platform import UsageEvent

    t = make_tenant()
    campanha = _seed(t["tenant_id"], marca="Mineracao")
    envelope = new_job(
        tenant_id=t["tenant_id"],
        agent="research",
        campaign_id=campanha,
        user_id=t["user_id"],
        entity_type="company",
        entity_id=uuid.uuid4(),
    )

    with tenant_session(t["tenant_id"]) as session:
        primeira = run_job(session, envelope)

    # A mesma entrega, de novo — é o que o requeue produz.
    with tenant_session(t["tenant_id"]) as session:
        segunda = run_job(session, JobEnvelope.from_dict(envelope.to_dict()))

    assert segunda.id == primeira.id, "o mesmo job produziu duas execuções"

    with tenant_session(t["tenant_id"]) as session:
        execucoes = session.execute(
            select(func.count(AgentRun.id)).where(AgentRun.job_id == envelope.job_id)
        ).scalar_one()
        consumo = session.execute(
            select(func.coalesce(func.sum(UsageEvent.units), 0))
        ).scalar_one()

    assert execucoes == 1
    # E o consumo foi contado uma vez só: é aqui que a duplicata custa dinheiro.
    assert consumo == 1


def test_falha_que_nao_e_de_dominio_fecha_o_run(make_tenant, monkeypatch):
    """O erro mais provável em produção não é de domínio.

    É o SDK da Anthropic levantando o seu: corte de conexão, 429, 500 do outro
    lado. O runner só tratava `AppError`, então esse erro passava por fora: o run
    ficava em `running` para sempre e — por ser um run não-falho com o mesmo
    `job_id` — a tentativa seguinte o devolvia como "já executado" e marcava o
    job como concluído. O trabalho sumia em silêncio.
    """
    from sqlalchemy import select

    from app.db.models.ai import AgentRun
    from app.orchestrator.executors.base import ExecutionResult
    from app.orchestrator.runner import _EXECUTORS

    t = make_tenant()
    campanha = _seed(t["tenant_id"], marca="Mineracao")
    envelope = new_job(
        tenant_id=t["tenant_id"],
        agent="research",
        campaign_id=campanha,
        entity_type="company",
        entity_id=uuid.uuid4(),
    )

    chamadas = []

    def cai(session, context, envelope):
        chamadas.append("caiu")
        raise RuntimeError("Connection error.")

    monkeypatch.setitem(_EXECUTORS, "research", cai)
    with pytest.raises(RuntimeError):
        with tenant_session(t["tenant_id"]) as session:
            run_job(session, envelope)

    with tenant_session(t["tenant_id"]) as session:
        run = session.execute(select(AgentRun)).scalars().one()
        assert run.status == "failed"
        assert "RuntimeError" in (run.error or "")
        assert run.finished_at is not None

    def entrega(session, context, envelope):
        chamadas.append("entregou")
        return ExecutionResult(output={"ok": True}, model="claude-opus-5")

    monkeypatch.setitem(_EXECUTORS, "research", entrega)
    with tenant_session(t["tenant_id"]) as session:
        segunda = run_job(session, JobEnvelope.from_dict(envelope.to_dict()))

    # A tentativa seguinte faz o trabalho, em vez de herdar um run parado.
    assert segunda.status == "succeeded"
    assert chamadas == ["caiu", "entregou"]


def test_run_abandonado_nao_vale_como_entrega(make_tenant, monkeypatch):
    """Processo morto no meio deixa o run em `running`; ninguém o vai concluir.

    Passado o prazo de órfão da fila, tratar esse run como entrega feita fazia o
    job ser marcado como concluído sem nada ter acontecido.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db.models.ai import AgentRun
    from app.orchestrator.executors.base import ExecutionResult
    from app.orchestrator.runner import _EXECUTORS

    t = make_tenant()
    campanha = _seed(t["tenant_id"], marca="Mineracao")
    envelope = new_job(
        tenant_id=t["tenant_id"],
        agent="research",
        campaign_id=campanha,
        entity_type="company",
        entity_id=uuid.uuid4(),
    )

    with tenant_session(t["tenant_id"]) as session:
        session.add(
            AgentRun(
                tenant_id=t["tenant_id"],
                job_id=envelope.job_id,
                agent_kind="research",
                status="running",
                input={},
                started_at=datetime.now(UTC) - timedelta(minutes=20),
            )
        )

    executou = []

    def entrega(session, context, envelope):
        executou.append(1)
        return ExecutionResult(output={"ok": True}, model="claude-opus-5")

    monkeypatch.setitem(_EXECUTORS, "research", entrega)
    with tenant_session(t["tenant_id"]) as session:
        run = run_job(session, envelope)

    assert executou == [1]
    assert run.status == "succeeded"
    with tenant_session(t["tenant_id"]) as session:
        parado = session.execute(
            select(AgentRun).where(AgentRun.status == "failed")
        ).scalars().one()
        assert "abandonado" in (parado.error or "")


def test_run_que_ainda_esta_rodando_continua_protegido(make_tenant, monkeypatch):
    """O outro lado: dentro do prazo, o run em curso ainda vale como entrega.

    É a proteção original — o `requeue_stale` devolve à fila um job de agente que
    passou de quinze minutos sem saber se o worker morreu ou se a busca na web
    ainda está rodando. Dentro do prazo, refazer é pagar duas vezes.
    """
    from datetime import UTC, datetime

    from app.db.models.ai import AgentRun
    from app.orchestrator.executors.base import ExecutionResult
    from app.orchestrator.runner import _EXECUTORS

    t = make_tenant()
    campanha = _seed(t["tenant_id"], marca="Mineracao")
    envelope = new_job(
        tenant_id=t["tenant_id"],
        agent="research",
        campaign_id=campanha,
        entity_type="company",
        entity_id=uuid.uuid4(),
    )

    with tenant_session(t["tenant_id"]) as session:
        session.add(
            AgentRun(
                tenant_id=t["tenant_id"],
                job_id=envelope.job_id,
                agent_kind="research",
                status="running",
                input={},
                started_at=datetime.now(UTC),
            )
        )

    executou = []

    def entrega(session, context, envelope):
        executou.append(1)
        return ExecutionResult(output={"ok": True}, model="claude-opus-5")

    monkeypatch.setitem(_EXECUTORS, "research", entrega)
    with tenant_session(t["tenant_id"]) as session:
        run = run_job(session, envelope)

    assert executou == [], "o run em curso deveria ter sido devolvido, não refeito"
    assert run.status == "running"
