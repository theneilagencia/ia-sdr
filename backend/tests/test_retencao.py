"""Retenção automática: o que sai por prazo e, principalmente, o que não sai.

A ordem destes testes é a ordem do risco. Apagar não tem desfazer, então o que
mais importa aqui não é o descarte funcionar — é ele **não** pegar o que não
pode: o descadastro de quem pediu para não receber mais email, o consumo de um
mês que ainda não foi faturado, a auditoria de um incidente, o rascunho que
alguém ainda vai revisar, e o dado da empresa do lado.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.db.models.ai import AgentRun, RunStatus
from app.db.models.engagement import (
    Conversation,
    Meeting,
    Message,
    MessageDirection,
    MessageStatus,
    Qualification,
)
from app.db.models.jobs import Job, JobKind, JobStatus
from app.db.models.platform import AuditLog, Invoice, InvoiceStatus, Tenant, UsageEvent
from app.db.models.sales import Campaign, Company, Contact, Prospect, ProspectStatus
from app.db.session import tenant_session, unscoped_session
from app.services import retencao
from app.services.retencao import PISO_AUDITORIA_DIAS, Politica
from app.services.usage import UsageKind

AGORA = datetime.now(UTC)
VELHO = AGORA - timedelta(days=200)
RECENTE = AGORA - timedelta(days=2)


def _politica(tenant_id, *, dias: int, leads_frios: bool = False) -> None:
    with unscoped_session(reason="test:retencao") as session:
        retencao.salvar(
            session.get(Tenant, tenant_id), Politica(dias=dias, leads_frios=leads_frios)
        )


def _contar(tenant_id, modelo) -> int:
    with tenant_session(tenant_id) as session:
        return int(
            session.execute(
                select(func.count()).select_from(modelo).where(modelo.tenant_id == tenant_id)
            ).scalar_one()
        )


def _aplicar(tenant_id) -> dict[str, int]:
    with unscoped_session(reason="test:retencao") as session:
        return retencao.aplicar(session, tenant_id, agora=AGORA)


def _prever(tenant_id) -> dict[str, int]:
    with unscoped_session(reason="test:retencao") as session:
        return retencao.previsao(session, tenant_id, agora=AGORA)


@pytest.fixture
def com_rastro(make_tenant):
    """Uma empresa com rastro velho e recente de cada classe."""
    t = make_tenant()
    tid = t["tenant_id"]
    with tenant_session(tid) as session:
        for quando in (VELHO, RECENTE):
            session.add(
                Job(
                    tenant_id=tid,
                    kind=JobKind.SEND_QUEUED.value,
                    status=JobStatus.DONE.value,
                    payload={},
                    dedupe_key=f"job-{quando.isoformat()}",
                    run_at=quando,
                    created_at=quando,
                )
            )
            session.add(
                AgentRun(
                    tenant_id=tid,
                    job_id=f"run-{quando.isoformat()}",
                    agent_kind="research",
                    status=RunStatus.SUCCEEDED.value,
                    created_at=quando,
                )
            )
            session.add(
                AuditLog(
                    tenant_id=tid,
                    actor_role="owner",
                    action="teste",
                    resource_type="tenant",
                    source="api",
                    created_at=quando,
                )
            )
    return t


def test_sem_prazo_nada_sai(com_rastro):
    """O padrão é não descartar: a plataforma não escolhe o que o cliente perde."""
    tid = com_rastro["tenant_id"]
    assert _aplicar(tid) == {}
    assert _contar(tid, Job) == 2
    assert _contar(tid, AgentRun) == 2


def test_com_prazo_o_rastro_velho_sai_e_o_recente_fica(com_rastro):
    tid = com_rastro["tenant_id"]
    _politica(tid, dias=30)
    saiu = _aplicar(tid)
    assert saiu["jobs"] == 1
    assert saiu["agent_runs"] == 1
    assert _contar(tid, Job) == 1
    assert _contar(tid, AgentRun) == 1


def test_rodar_duas_vezes_nao_apaga_mais_nada(com_rastro):
    """Idempotente: o job é diário e vai rodar para sempre."""
    tid = com_rastro["tenant_id"]
    _politica(tid, dias=30)
    _aplicar(tid)
    segunda = _aplicar(tid)
    assert segunda["jobs"] == 0
    assert segunda["agent_runs"] == 0


def test_a_auditoria_tem_piso_proprio(com_rastro):
    """Prazo curto não apaga auditoria: é o registro de quem fez o quê.

    Com 7 dias de retenção, um log de 60 dias atrás **fica** — o piso de 90 dias
    vence o prazo. Seis meses depois de um incidente é quando alguém precisa
    dele, e quem configurou 7 dias estava pensando em rastro de job, não em
    apagar a prova de uma ação administrativa.
    """
    tid = com_rastro["tenant_id"]
    with tenant_session(tid) as session:
        session.add(
            AuditLog(
                tenant_id=tid,
                actor_role="owner",
                action="tenant.updated_by_platform_admin",
                resource_type="tenant",
                source="admin",
                created_at=AGORA - timedelta(days=PISO_AUDITORIA_DIAS - 30),
            )
        )
    _politica(tid, dias=7)
    saiu = _aplicar(tid)

    # Saiu só o de 200 dias; o de 60 e o de 2 ficaram.
    assert saiu["audit_logs"] == 1
    assert _contar(tid, AuditLog) == 2


def test_consumo_de_mes_sem_fatura_emitida_nao_sai(make_tenant):
    """Apagar consumo antes de faturar é faturar por estimativa depois."""
    t = make_tenant()
    tid = t["tenant_id"]
    with tenant_session(tid) as session:
        session.add(
            UsageEvent(
                tenant_id=tid,
                kind=UsageKind.RESEARCH.value,
                units=1,
                quantity=1,
                cost_micro_usd=100,
                created_at=VELHO,
            )
        )
    _politica(tid, dias=30)

    assert _aplicar(tid)["usage_events"] == 0
    assert _contar(tid, UsageEvent) == 1

    # Com a fatura daquele mês emitida, o consumo já cumpriu a função dele.
    with unscoped_session(reason="test:retencao") as session:
        session.add(
            Invoice(
                tenant_id=tid,
                period_year=VELHO.year,
                period_month=VELHO.month,
                status=InvoiceStatus.ISSUED.value,
                issued_at=AGORA,
            )
        )
    assert _aplicar(tid)["usage_events"] == 1
    assert _contar(tid, UsageEvent) == 0


def test_fatura_em_rascunho_nao_libera_o_consumo(make_tenant):
    """Rascunho é recalculado a cada fechamento: sem o consumo, viraria zero."""
    t = make_tenant()
    tid = t["tenant_id"]
    with tenant_session(tid) as session:
        session.add(
            UsageEvent(
                tenant_id=tid,
                kind=UsageKind.RESEARCH.value,
                units=1,
                quantity=1,
                cost_micro_usd=100,
                created_at=VELHO,
            )
        )
    with unscoped_session(reason="test:retencao") as session:
        session.add(
            Invoice(
                tenant_id=tid,
                period_year=VELHO.year,
                period_month=VELHO.month,
                status=InvoiceStatus.DRAFT.value,
            )
        )
    _politica(tid, dias=30)
    assert _aplicar(tid)["usage_events"] == 0
    assert _contar(tid, UsageEvent) == 1


# ------------------------------------------------------------------ mensagens


@pytest.fixture
def com_conversa(make_tenant):
    t = make_tenant()
    tid = t["tenant_id"]
    with tenant_session(tid) as session:
        campanha = Campaign(tenant_id=tid, name="C", slug="c")
        empresa = Company(tenant_id=tid, name="Alvo")
        contato = Contact(
            tenant_id=tid, full_name="Pessoa", email="pessoa@alvo.com", created_at=VELHO
        )
        session.add_all([campanha, empresa, contato])
        session.flush()
        prospect = Prospect(
            tenant_id=tid,
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status=ProspectStatus.CONTACTED.value,
            last_activity_at=VELHO,
            created_at=VELHO,
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(tenant_id=tid, prospect_id=prospect.id, created_at=VELHO)
        session.add(conversa)
        session.flush()
        session.add_all(
            [
                Message(
                    tenant_id=tid,
                    conversation_id=conversa.id,
                    direction=MessageDirection.OUTBOUND.value,
                    status=MessageStatus.SENT.value,
                    body="enviada há muito tempo",
                    created_at=VELHO,
                ),
                Message(
                    tenant_id=tid,
                    conversation_id=conversa.id,
                    direction=MessageDirection.OUTBOUND.value,
                    status=MessageStatus.DRAFT.value,
                    body="rascunho velho, à espera de revisão",
                    created_at=VELHO,
                ),
                Message(
                    tenant_id=tid,
                    conversation_id=conversa.id,
                    direction=MessageDirection.OUTBOUND.value,
                    status=MessageStatus.QUEUED.value,
                    body="aprovada, à espera de envio",
                    created_at=VELHO,
                ),
            ]
        )
        t["prospect_id"] = prospect.id
        t["contact_id"] = contato.id
        t["conversation_id"] = conversa.id
    return t


def test_mensagem_enviada_sai_mas_trabalho_pendente_fica(com_conversa):
    """Rascunho e aprovado-para-envio são trabalho, não histórico.

    Apagá-los por prazo jogaria fora o que alguém ainda vai revisar ou o que já
    foi aprovado e ainda não saiu — e o dono do texto descobriria pela ausência.
    """
    tid = com_conversa["tenant_id"]
    _politica(tid, dias=30)
    assert _aplicar(tid)["messages"] == 1
    assert _contar(tid, Message) == 2

    with tenant_session(tid) as session:
        restantes = {
            m.status
            for m in session.execute(select(Message).where(Message.tenant_id == tid))
            .scalars()
            .all()
        }
    assert restantes == {MessageStatus.DRAFT.value, MessageStatus.QUEUED.value}


# ---------------------------------------------------------------- leads frios


def test_lead_frio_so_sai_se_a_empresa_pedir(com_conversa):
    """Desligado por padrão: lead é ativo comercial, não rastro."""
    tid = com_conversa["tenant_id"]
    _politica(tid, dias=30)
    assert _aplicar(tid)["prospects"] == 0
    assert _contar(tid, Prospect) == 1


def test_lead_frio_sai_com_o_contato_dele(com_conversa):
    tid = com_conversa["tenant_id"]
    _politica(tid, dias=30, leads_frios=True)
    saiu = _aplicar(tid)
    assert saiu["prospects"] == 1
    assert saiu["contacts"] == 1
    assert _contar(tid, Prospect) == 0
    assert _contar(tid, Contact) == 0
    # A conversa e o que restava dela saem por cascata: sem prospect, não há
    # conversa de quem. Inclusive o rascunho que a regra de mensagem preservava —
    # e isso é coerente: quem pediu para apagar o lead pediu para apagar o lead.
    assert _contar(tid, Conversation) == 0
    assert _contar(tid, Message) == 0
    # A empresa-alvo fica: firmografia não é dado pessoal, e é o que faz o
    # próximo import reconhecer a conta em vez de duplicá-la.
    assert _contar(tid, Company) == 1


def test_contato_descadastrado_nunca_sai(com_conversa):
    """A trava mais importante deste arquivo.

    Apagar o registro do descadastro faz a plataforma escrever de novo para quem
    pediu para não receber mais — no próximo import da mesma lista. Honrar o
    pedido é obrigação legal, e um prazo de retenção não a dispensa.
    """
    tid = com_conversa["tenant_id"]
    with tenant_session(tid) as session:
        contato = session.get(Contact, com_conversa["contact_id"])
        contato.opted_out = True

    _politica(tid, dias=30, leads_frios=True)
    saiu = _aplicar(tid)

    assert saiu["prospects"] == 1, "o prospect frio sai"
    assert saiu["contacts"] == 0, "o contato descadastrado fica"
    assert _contar(tid, Contact) == 1
    with tenant_session(tid) as session:
        assert session.get(Contact, com_conversa["contact_id"]).opted_out is True


def test_lead_que_respondeu_nao_e_frio(com_conversa):
    """Uma mensagem de entrada é a pessoa falando com a empresa."""
    tid = com_conversa["tenant_id"]
    with tenant_session(tid) as session:
        session.add(
            Message(
                tenant_id=tid,
                conversation_id=com_conversa["conversation_id"],
                direction=MessageDirection.INBOUND.value,
                status=MessageStatus.SENT.value,
                body="me manda mais informação",
                created_at=VELHO,
            )
        )
    _politica(tid, dias=30, leads_frios=True)
    assert _aplicar(tid)["prospects"] == 0
    assert _contar(tid, Prospect) == 1


def test_lead_com_reuniao_nao_e_frio(com_conversa):
    """Reunião marcada é a conversão que a plataforma existe para produzir."""
    tid = com_conversa["tenant_id"]
    with tenant_session(tid) as session:
        session.add(
            Meeting(
                tenant_id=tid,
                prospect_id=com_conversa["prospect_id"],
                scheduled_at=AGORA - timedelta(days=100),
                created_at=VELHO,
            )
        )
    _politica(tid, dias=30, leads_frios=True)
    assert _aplicar(tid)["prospects"] == 0


def test_lead_com_veredito_nao_e_frio(com_conversa):
    tid = com_conversa["tenant_id"]
    with tenant_session(tid) as session:
        session.add(
            Qualification(
                tenant_id=tid,
                prospect_id=com_conversa["prospect_id"],
                outcome="qualified",
                confidence=90,
                criteria_results={},
                created_at=VELHO,
            )
        )
    _politica(tid, dias=30, leads_frios=True)
    assert _aplicar(tid)["prospects"] == 0


def test_lead_engajado_nao_sai_nem_com_prazo_curto(com_conversa):
    tid = com_conversa["tenant_id"]
    with tenant_session(tid) as session:
        session.get(Prospect, com_conversa["prospect_id"]).status = ProspectStatus.ENGAGED.value
    _politica(tid, dias=1, leads_frios=True)
    assert _aplicar(tid)["prospects"] == 0


# ------------------------------------------------------------------- previsão


def test_a_previsao_conta_sem_apagar(com_rastro):
    """A tela precisa mostrar o número antes de a pessoa ligar o prazo."""
    tid = com_rastro["tenant_id"]
    _politica(tid, dias=30)
    previsto = _prever(tid)
    assert previsto["jobs"] == 1
    assert _contar(tid, Job) == 2, "previsão não apaga nada"

    saiu = _aplicar(tid)
    assert saiu["jobs"] == previsto["jobs"], "o que foi previsto é o que sai"


def test_a_previsao_conta_o_contato_que_ficaria_orfao(com_conversa):
    """O caso que uma previsão ingênua erraria.

    Na hora da previsão o prospect ainda existe, então "contato sem prospect"
    seria zero — e a execução apagaria o contato junto. A tela mostraria zero e o
    job apagaria um: previsão que mente é pior do que previsão nenhuma.
    """
    tid = com_conversa["tenant_id"]
    _politica(tid, dias=30, leads_frios=True)
    assert _prever(tid)["contacts"] == 1
    assert _aplicar(tid)["contacts"] == 1


def test_a_previsao_por_hipotese_nao_grava_a_politica(client, auth_headers, make_tenant):
    """Simular 90 dias não pode ligar 90 dias: quem previu não pediu para mudar."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.get("/api/v1/settings/retention/preview?days=30", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 30
    assert client.get("/api/v1/settings/retention", headers=headers).json()["days"] == 0


# ------------------------------------------------------------------ isolamento


def test_a_retencao_de_uma_empresa_nao_toca_na_outra(make_tenant):
    """O de sempre, e o que mais assusta num serviço que apaga."""
    a, b = make_tenant(), make_tenant()
    for t in (a, b):
        with tenant_session(t["tenant_id"]) as session:
            session.add(
                Job(
                    tenant_id=t["tenant_id"],
                    kind=JobKind.SEND_QUEUED.value,
                    status=JobStatus.DONE.value,
                    payload={},
                    dedupe_key="x",
                    run_at=VELHO,
                    created_at=VELHO,
                )
            )
    _politica(a["tenant_id"], dias=30)
    # A empresa B não tem prazo: nada dela pode sair quando a de A rodar.
    assert _aplicar(a["tenant_id"])["jobs"] == 1
    assert _contar(a["tenant_id"], Job) == 0
    assert _contar(b["tenant_id"], Job) == 1


# ------------------------------------------------------------------------ API


def test_a_tela_salva_e_le_a_politica(client, auth_headers, make_tenant):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    assert client.get("/api/v1/settings/retention", headers=headers).json() == {
        "days": 0,
        "include_cold_prospects": False,
    }
    r = client.put(
        "/api/v1/settings/retention",
        headers=headers,
        json={"days": 180, "include_cold_prospects": True},
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/settings/retention", headers=headers).json() == {
        "days": 180,
        "include_cold_prospects": True,
    }


def test_prazo_negativo_e_recusado(client, auth_headers, make_tenant):
    t = make_tenant()
    r = client.put(
        "/api/v1/settings/retention",
        headers=auth_headers(t["email"], t["password"]),
        json={"days": -5},
    )
    assert r.status_code == 422


def test_mudar_a_politica_fica_na_auditoria(client, auth_headers, make_tenant):
    """ "Desde quando está assim?" é a pergunta que aparece depois do primeiro susto."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.put("/api/v1/settings/retention", headers=headers, json={"days": 90})
    acoes = [
        linha["action"] for linha in client.get("/api/v1/tenants/me/audit", headers=headers).json()
    ]
    assert "retention_policy.updated" in acoes


def test_quem_nao_administra_nao_muda_o_prazo(client, auth_headers, membro, make_tenant):
    """Prazo de retenção apaga dado da empresa inteira: não é para qualquer papel."""
    t = make_tenant()
    viewer = membro(t, role="viewer")
    r = client.put(
        "/api/v1/settings/retention",
        headers=auth_headers(viewer["email"], viewer["password"]),
        json={"days": 30},
    )
    assert r.status_code == 403


def test_a_borda_do_mes_e_a_mesma_do_fechamento(make_tenant):
    """O último instante do mês faturado conta como daquele mês, e sai.

    A definição de "mês" tem de ser a mesma nos dois lados. Enquanto a retenção
    usava `extract(month, ...)`, ela dependia do fuso da sessão do banco: num
    servidor em UTC-3, o evento de 31/08 23:30 UTC seria setembro para ela e
    agosto para o fechamento. O desencontro apagaria consumo que sustenta fatura
    ainda não emitida — e a conta errada só apareceria na cobrança seguinte.
    """
    t = make_tenant()
    tid = t["tenant_id"]
    ano, mes = VELHO.year, VELHO.month
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    quase_virando = datetime(ano, mes, ultimo_dia, 23, 30, tzinfo=UTC)
    ja_no_mes_seguinte = quase_virando + timedelta(hours=1)

    with tenant_session(tid) as session:
        for quando in (quase_virando, ja_no_mes_seguinte):
            session.add(
                UsageEvent(
                    tenant_id=tid,
                    kind=UsageKind.RESEARCH.value,
                    units=1,
                    quantity=1,
                    cost_micro_usd=1,
                    created_at=quando,
                )
            )
    with unscoped_session(reason="test:retencao") as session:
        session.add(
            Invoice(
                tenant_id=tid,
                period_year=ano,
                period_month=mes,
                status=InvoiceStatus.ISSUED.value,
                issued_at=AGORA,
            )
        )

    _politica(tid, dias=30)

    # A retenção roda numa sessão com fuso brasileiro de propósito: é a
    # configuração em que o defeito aparecia. Com `extract`, o evento de
    # 01/09 00:30 UTC (21:30 do dia 31/08 em São Paulo) era contado como agosto —
    # mês faturado — e saía junto, apagando o consumo que sustenta a fatura de
    # setembro, que ainda não existe.
    with unscoped_session(reason="test:retencao") as session:
        session.execute(text("SET TIME ZONE 'America/Sao_Paulo'"))
        saiu = retencao.aplicar(session, tid, agora=AGORA)

    assert saiu["usage_events"] == 1, "só o consumo do mês faturado sai"
    assert _contar(tid, UsageEvent) == 1
