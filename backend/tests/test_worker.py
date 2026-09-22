"""A fila e o worker: o que faz a plataforma trabalhar sem ninguém olhando."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.engagement import Conversation, Message
from app.db.models.jobs import Job, JobKind
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session, unscoped_session
from app.orchestrator.executors.outreach import OutreachBlocked
from app.services import jobs
from app.workers import runner

AGORA = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)


def _enfileirar(tenant_id, **kwargs):
    with tenant_session(tenant_id) as session:
        return jobs.enqueue(session, tenant_id=tenant_id, **kwargs)


# ------------------------------------------------------------------ fila


def test_job_enfileirado_fica_pendente(make_tenant):
    t = make_tenant()
    job = _enfileirar(t["tenant_id"], kind=JobKind.FETCH_INBOX)
    assert job is not None

    with tenant_session(t["tenant_id"]) as session:
        gravado = session.execute(select(Job)).scalars().one()
        assert gravado.status == "pending" and gravado.attempts == 0


def test_deduplicacao_evita_fila_repetida(make_tenant):
    """Ler a caixa duas vezes ao mesmo tempo não adianta nada."""
    t = make_tenant()
    assert _enfileirar(t["tenant_id"], kind=JobKind.FETCH_INBOX, dedupe_key="inbox") is not None
    assert _enfileirar(t["tenant_id"], kind=JobKind.FETCH_INBOX, dedupe_key="inbox") is None

    with tenant_session(t["tenant_id"]) as session:
        assert len(session.execute(select(Job)).scalars().all()) == 1


def test_job_concluido_libera_a_chave(make_tenant):
    t = make_tenant()
    _enfileirar(t["tenant_id"], kind=JobKind.FETCH_INBOX, dedupe_key="inbox")

    with unscoped_session(reason="test:claim") as admin:
        job = jobs.claim_next(admin)
        jobs.complete(admin, job)

    # Terminado o anterior, o próximo ciclo pode agendar de novo.
    assert _enfileirar(t["tenant_id"], kind=JobKind.FETCH_INBOX, dedupe_key="inbox") is not None


def test_claim_pega_um_de_cada_vez(make_tenant):
    a = make_tenant()
    b = make_tenant()
    _enfileirar(a["tenant_id"], kind=JobKind.SEND_QUEUED)
    _enfileirar(b["tenant_id"], kind=JobKind.SEND_QUEUED)

    with unscoped_session(reason="test:claim") as admin:
        primeiro = jobs.claim_next(admin)
        assert primeiro.status == "running" and primeiro.attempts == 1
        segundo = jobs.claim_next(admin)
        # O worker atende todas as empresas: o segundo job é de outro tenant.
        assert segundo.tenant_id != primeiro.tenant_id
        assert jobs.claim_next(admin) is None


def test_job_futuro_nao_e_reservado(make_tenant):
    t = make_tenant()
    _enfileirar(
        t["tenant_id"], kind=JobKind.SEND_QUEUED, run_at=datetime.now(UTC) + timedelta(hours=1)
    )
    with unscoped_session(reason="test:claim") as admin:
        assert jobs.claim_next(admin) is None


def test_falha_reagenda_com_espera_crescente(make_tenant):
    t = make_tenant()
    _enfileirar(t["tenant_id"], kind=JobKind.SEND_QUEUED, max_attempts=3)

    with unscoped_session(reason="test:falha") as admin:
        job = jobs.claim_next(admin, agora=AGORA)
        jobs.fail(admin, job, "erro de rede", agora=AGORA)
        assert job.status == "pending"
        assert job.run_at == AGORA + timedelta(minutes=1)

        job.run_at = AGORA
        admin.flush()  # o claim lê do banco, não da sessão
        job2 = jobs.claim_next(admin, agora=AGORA)
        jobs.fail(admin, job2, "de novo", agora=AGORA)
        # A segunda espera é maior que a primeira.
        assert job2.run_at == AGORA + timedelta(minutes=5)


def test_tentativas_esgotadas_marcam_falha_definitiva(make_tenant):
    t = make_tenant()
    _enfileirar(t["tenant_id"], kind=JobKind.SEND_QUEUED, max_attempts=1)

    with unscoped_session(reason="test:falha") as admin:
        job = jobs.claim_next(admin, agora=AGORA)
        jobs.fail(admin, job, "erro fatal", agora=AGORA)
        assert job.status == "failed"
        assert job.last_error == "erro fatal"


def test_job_preso_volta_para_a_fila(make_tenant):
    """Worker que morre no meio não pode levar o job junto."""
    t = make_tenant()
    _enfileirar(t["tenant_id"], kind=JobKind.SEND_QUEUED)

    with unscoped_session(reason="test:preso") as admin:
        job = jobs.claim_next(admin)
        job.started_at = datetime.now(UTC) - timedelta(hours=1)
        admin.flush()

        assert jobs.requeue_stale(admin) == 1
        assert admin.get(Job, job.id).status == "pending"


def test_fila_de_uma_empresa_nao_aparece_na_outra(make_tenant):
    a = make_tenant()
    b = make_tenant()
    _enfileirar(a["tenant_id"], kind=JobKind.SEND_QUEUED)

    with tenant_session(b["tenant_id"]) as session:
        assert session.execute(select(Job)).scalars().all() == []


# ------------------------------------------------------------------ worker


@pytest.fixture
def empresa_com_conversa(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(tenant_id=t["tenant_id"], name="Mining", slug="mc")
        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"], company_id=empresa.id, full_name="Alice", email="a@b.ca"
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="contacted",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(tenant_id=t["tenant_id"], prospect_id=prospect.id)
        session.add(conversa)
        session.flush()
        return {**t, "conversation_id": conversa.id, "prospect_id": prospect.id}


def test_ciclo_executa_e_conclui(make_tenant, monkeypatch):
    t = make_tenant()
    executados = []
    monkeypatch.setattr(
        runner, "executar", lambda tenant_id, kind, payload: executados.append(kind)
    )
    _enfileirar(t["tenant_id"], kind=JobKind.SEND_QUEUED)

    resultado = runner.ciclo()

    assert resultado["done"] == 1 and resultado["failed"] == 0
    assert executados == ["send_queued"]
    with tenant_session(t["tenant_id"]) as session:
        assert session.execute(select(Job)).scalars().one().status == "done"


def test_job_que_estoura_nao_derruba_o_worker(make_tenant, monkeypatch):
    """Um job ruim não pode parar a fila inteira."""
    t = make_tenant()

    def explode(tenant_id, kind, payload):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(runner, "executar", explode)
    _enfileirar(t["tenant_id"], kind=JobKind.SEND_QUEUED, max_attempts=1)

    resultado = runner.ciclo()

    assert resultado == {"requeued": 0, "done": 0, "failed": 1}
    with tenant_session(t["tenant_id"]) as session:
        job = session.execute(select(Job)).scalars().one()
    assert job.status == "failed"
    assert "banco fora do ar" in job.last_error


def test_periodicos_agendados_por_empresa_ativa(make_tenant):
    a = make_tenant()
    b = make_tenant()
    from app.db.models.platform import Tenant

    with tenant_session(b["tenant_id"]) as session:
        session.get(Tenant, b["tenant_id"]).is_active = False

    criados = runner.agendar_periodicos(AGORA)

    # leitura de caixa, envio, avanço das cadências e envio para o CRM — só
    # para a empresa ativa
    assert criados == 4
    with tenant_session(a["tenant_id"]) as session:
        tipos = {j.kind for j in session.execute(select(Job)).scalars()}
    assert tipos == {"fetch_inbox", "send_queued", "sequence_tick", "ravi_sync"}
    with tenant_session(b["tenant_id"]) as session:
        assert session.execute(select(Job)).scalars().all() == []


def test_ciclo_seguinte_nao_duplica_periodicos(make_tenant):
    make_tenant()
    assert runner.agendar_periodicos(AGORA) == 4
    # Ciclo lento não acumula fila.
    assert runner.agendar_periodicos(AGORA) == 0


def test_resposta_recebida_aciona_o_agente_de_conversa(empresa_com_conversa, monkeypatch):
    """O ciclo que fecha sozinho: lead responde, agente escreve a réplica."""
    tenant_id = empresa_com_conversa["tenant_id"]
    with tenant_session(tenant_id) as session:
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=empresa_com_conversa["conversation_id"],
                direction="inbound",
                status="replied",
                body="Pode ser quinta?",
            )
        )

    # A leitura diz **quais** conversas receberam mensagem agora; é isso que o
    # worker aciona. Antes ele procurava por janela de tempo, e reacionava
    # conversas já respondidas.
    monkeypatch.setattr(
        runner.email_receiver,
        "fetch_inbox",
        lambda session, tenant_id, context=None: {
            "fetched": 1,
            "recorded": 1,
            "ignored": 0,
            "conversations": [str(empresa_com_conversa["conversation_id"])],
        },
    )
    runner.executar(tenant_id, JobKind.FETCH_INBOX.value, {})

    with tenant_session(tenant_id) as session:
        job = session.execute(
            select(Job).where(Job.kind == JobKind.AGENT_RUN.value)
        ).scalars().one()
    assert job.payload["agent"] == "conversation"
    assert job.payload["entity_id"] == str(empresa_com_conversa["conversation_id"])
    assert job.payload["tenant_id"] == str(tenant_id)


def test_caixa_sem_resposta_nao_aciona_agente(empresa_com_conversa, monkeypatch):
    tenant_id = empresa_com_conversa["tenant_id"]
    monkeypatch.setattr(
        runner.email_receiver,
        "fetch_inbox",
        lambda session, tenant_id, context=None: {
            "fetched": 0,
            "recorded": 0,
            "ignored": 0,
            "conversations": [],
        },
    )
    runner.executar(tenant_id, JobKind.FETCH_INBOX.value, {})

    with tenant_session(tenant_id) as session:
        assert session.execute(select(Job)).scalars().all() == []


def test_tipo_desconhecido_falha_explicitamente(make_tenant):
    t = make_tenant()
    with pytest.raises(ValueError, match="desconhecido"):
        runner.executar(t["tenant_id"], "faxina", {})


def test_agente_roda_no_tenant_do_job(make_tenant, monkeypatch):
    """O job diz em qual empresa o trabalho acontece — nunca o processo."""
    t = make_tenant()
    vistos = []

    def falso_run_job(session, envelope):
        vistos.append(envelope.tenant_id)

    monkeypatch.setattr(runner, "run_job", falso_run_job)
    runner.executar(
        t["tenant_id"],
        JobKind.AGENT_RUN.value,
        {"tenant_id": str(t["tenant_id"]), "agent": "research", "entity_id": str(uuid.uuid4())},
    )
    assert vistos == [t["tenant_id"]]


def test_recusa_de_politica_nao_e_tentada_tres_vezes(make_tenant, monkeypatch):
    """Descadastro não se desfaz em cinco minutos — nem a cota do mês.

    A fila tratava toda exceção como falha transitória: três tentativas com
    espera crescente. Para recusa de política isso só enche o log, e quando a
    recusa acontece **depois** da chamada ao modelo (teto de custo estourado) a
    segunda tentativa gasta de novo para ser recusada igual.
    """
    t = make_tenant()
    _enfileirar(
        t["tenant_id"],
        kind=JobKind.AGENT_RUN,
        payload={"tenant_id": str(t["tenant_id"]), "agent": "outreach"},
        max_attempts=3,
    )

    def recusa(*_args, **_kwargs):
        raise OutreachBlocked("Contato pediu para não ser abordado")

    monkeypatch.setattr(runner, "run_job", recusa)
    resultado = runner.ciclo()
    assert resultado["failed"] == 1

    with tenant_session(t["tenant_id"]) as session:
        job = session.execute(
            select(Job).where(Job.kind == JobKind.AGENT_RUN.value)
        ).scalars().one()
        assert job.status == "failed"
        assert job.attempts == 1 and job.finished_at is not None


def test_falha_de_verdade_continua_sendo_tentada(make_tenant, monkeypatch):
    """O outro lado da regra: erro de rede merece a segunda tentativa."""
    t = make_tenant()
    _enfileirar(
        t["tenant_id"],
        kind=JobKind.AGENT_RUN,
        payload={"tenant_id": str(t["tenant_id"]), "agent": "outreach"},
        max_attempts=3,
    )

    def cai(*_args, **_kwargs):
        raise TimeoutError("a Anthropic não respondeu")

    monkeypatch.setattr(runner, "run_job", cai)
    runner.ciclo()

    with tenant_session(t["tenant_id"]) as session:
        job = session.execute(
            select(Job).where(Job.kind == JobKind.AGENT_RUN.value)
        ).scalars().one()
        assert job.status == "pending"


# ------------------------------------------------------------------ pela API


def test_disparo_assincrono_devolve_o_job(client, make_tenant, auth_headers):
    """Pesquisa leva minutos: a tela não pode ficar pendurada esperando."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    alvo = str(uuid.uuid4())

    r = client.post(
        "/api/v1/agents/jobs",
        headers=headers,
        json={"agent": "research", "entity_type": "company", "entity_id": alvo},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["payload"]["agent"] == "research"

    # Pedir a mesma pesquisa de novo é desperdício, não fila dobrada.
    repetido = client.post(
        "/api/v1/agents/jobs",
        headers=headers,
        json={"agent": "research", "entity_type": "company", "entity_id": alvo},
    )
    assert repetido.status_code == 409


def test_fila_da_empresa_e_visivel_na_api(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.post(
        "/api/v1/agents/jobs",
        headers=headers,
        json={"agent": "research", "entity_type": "company", "entity_id": str(uuid.uuid4())},
    )

    fila = client.get("/api/v1/agents/jobs", headers=headers).json()
    assert len(fila) == 1 and fila[0]["kind"] == "agent_run"


def test_fila_nao_atravessa_empresas(client, make_tenant, auth_headers):
    a = make_tenant()
    b = make_tenant()
    client.post(
        "/api/v1/agents/jobs",
        headers=auth_headers(a["email"], a["password"]),
        json={"agent": "research", "entity_type": "company", "entity_id": str(uuid.uuid4())},
    )
    headers_b = auth_headers(b["email"], b["password"])
    assert client.get("/api/v1/agents/jobs", headers=headers_b).json() == []


def test_agente_desconhecido_nao_entra_na_fila(client, make_tenant, auth_headers):
    t = make_tenant()
    r = client.post(
        "/api/v1/agents/jobs",
        headers=auth_headers(t["email"], t["password"]),
        json={"agent": "faxina"},
    )
    assert r.status_code == 400


def test_conversa_ja_respondida_nao_volta_para_a_fila(empresa_com_conversa, monkeypatch):
    """Segundo rascunho para a mesma mensagem é dinheiro e confusão.

    O worker procurava toda conversa com mensagem de entrada nos últimos dez
    minutos, e a leitura roda a cada cinco: bastava outro lead responder para a
    conversa já respondida entrar de novo na fila do agente. A deduplicação por
    chave não pegava — ela impede dois jobs iguais **na fila**, não um job novo
    depois de o primeiro ter concluído.
    """
    tenant_id = empresa_com_conversa["tenant_id"]
    with tenant_session(tenant_id) as session:
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=empresa_com_conversa["conversation_id"],
                direction="inbound",
                status="replied",
                body="Respondi há pouco.",
            )
        )

    # Esta leitura gravou mensagem para **outra** conversa (lista vazia aqui
    # representa "nenhuma que interesse a esta"), mas a conversa antiga continua
    # dentro da janela de dez minutos que o código usava.
    monkeypatch.setattr(
        runner.email_receiver,
        "fetch_inbox",
        lambda session, tenant_id, context=None: {
            "fetched": 1,
            "recorded": 1,
            "ignored": 0,
            "conversations": [],
        },
    )
    runner.executar(tenant_id, JobKind.FETCH_INBOX.value, {})

    with tenant_session(tenant_id) as session:
        agentes = session.execute(
            select(Job).where(Job.kind == JobKind.AGENT_RUN.value)
        ).scalars().all()
    assert agentes == [], "conversa sem mensagem nova foi reacionada"
