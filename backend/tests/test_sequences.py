"""Cadência multi-passo.

A maior parte das respostas em prospecção fria vem do segundo ou do terceiro
contato. O que estes testes protegem, porém, não é a cadência — são as regras
de parada. Continuar mandando follow-up para quem já respondeu, já marcou
reunião ou pediu descadastro é pior do que não ter cadência nenhuma.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.engagement import (
    Conversation,
    Meeting,
    Message,
    Qualification,
    SequenceEnrollment,
)
from app.db.models.jobs import Job, JobKind
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session
from app.services import sequences
from app.services.sequences import MAX_PASSOS, PassosInvalidos, validar_passos

PASSOS = [
    {"instruction": "Primeira abordagem, ancorada na pesquisa"},
    {"instruction": "Lembrete curto com um caso parecido", "wait_days": 3},
    {"instruction": "Último toque, oferecendo encerrar o assunto", "wait_days": 5},
]


def _prospect(tenant_id, campanha_id, *, opted_out=False, status="scored"):
    with tenant_session(tenant_id) as session:
        empresa = Company(tenant_id=tenant_id, name=f"Conta {uuid.uuid4().hex[:6]}")
        session.add(empresa)
        session.flush()
        contato = Contact(
            tenant_id=tenant_id,
            company_id=empresa.id,
            full_name="Alice",
            email=f"a-{uuid.uuid4().hex[:6]}@exemplo.com",
            opted_out=opted_out,
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=tenant_id,
            campaign_id=campanha_id,
            contact_id=contato.id,
            company_id=empresa.id,
            status=status,
        )
        session.add(prospect)
        session.flush()
        return {"prospect_id": prospect.id, "contact_id": contato.id, "company_id": empresa.id}


def _campanha(client, headers, nome="Cadência"):
    r = client.post(
        "/api/v1/campaigns",
        json={"name": nome, "slug": f"s-{uuid.uuid4().hex[:8]}", "channels": ["email"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _sequencia(client, headers, campanha_id, passos=None):
    r = client.post(
        "/api/v1/sequences",
        json={
            "campaign_id": str(campanha_id),
            "name": "Cadência padrão",
            "steps": passos or PASSOS,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _jobs_de_outreach(tenant_id) -> list[Job]:
    with tenant_session(tenant_id) as session:
        return list(
            session.execute(select(Job).where(Job.kind == JobKind.AGENT_RUN.value)).scalars()
        )


# --------------------------------------------------------------- validação
def test_primeiro_passo_nao_espera():
    """Agendar o primeiro contato para daqui a três dias só confundiria quem
    montou a cadência."""
    passos = validar_passos([{"instruction": "abordagem", "wait_days": 7}])
    assert passos[0]["wait_days"] == 0
    assert passos[0]["order"] == 1


def test_passo_sem_instrucao_e_recusado():
    """Sem instrução, os toques saem todos iguais — e o segundo email igual ao
    primeiro prova que do outro lado não tem ninguém lendo."""
    with pytest.raises(PassosInvalidos, match="não diz o que escrever"):
        validar_passos([{"instruction": "ok"}, {"instruction": "  "}])


def test_cadencia_longa_demais_e_recusada():
    com_muitos = [{"instruction": f"toque {i}"} for i in range(MAX_PASSOS + 1)]
    with pytest.raises(PassosInvalidos, match="spam"):
        validar_passos(com_muitos)


def test_dois_toques_no_mesmo_dia_sao_recusados():
    with pytest.raises(PassosInvalidos, match="ignorado"):
        validar_passos([{"instruction": "a"}, {"instruction": "b", "wait_days": 0}])


def test_sem_passo_nenhum():
    with pytest.raises(PassosInvalidos):
        validar_passos([])


# ---------------------------------------------------------------- inscrição
def test_inscreve_e_ignora_quem_nao_pode_entrar(client, make_tenant, auth_headers):
    """Selecionar cem leads e perder a operação inteira porque três já estavam
    na cadência seria hostil."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)

    ok = _prospect(t["tenant_id"], campanha)
    descadastrado = _prospect(t["tenant_id"], campanha, opted_out=True)
    fora_do_funil = _prospect(t["tenant_id"], campanha, status="meeting_booked")

    r = client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={
            "prospect_ids": [
                str(ok["prospect_id"]),
                str(descadastrado["prospect_id"]),
                str(fora_do_funil["prospect_id"]),
            ]
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["enrolled"]) == 1
    motivos = {s["reason"] for s in r.json()["skipped"]}
    assert "contato pediu descadastro" in motivos
    assert any("meeting_booked" in m for m in motivos)


def test_prospect_nao_entra_em_duas_cadencias(client, make_tenant, auth_headers):
    """Receberia dois emails no mesmo dia, de duas linhas de raciocínio."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    a = _sequencia(client, headers, campanha)
    b = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)

    primeira = client.post(
        f"/api/v1/sequences/{a['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )
    assert len(primeira.json()["enrolled"]) == 1

    segunda = client.post(
        f"/api/v1/sequences/{b['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )
    assert segunda.json()["enrolled"] == []
    assert segunda.json()["skipped"][0]["reason"] == "já está numa cadência"


def test_prospect_de_outra_campanha_nao_entra(client, make_tenant, auth_headers):
    t = make_tenant(plan="enterprise")
    headers = auth_headers(t["email"], t["password"])
    campanha_a = _campanha(client, headers, "Campanha A")
    campanha_b = _campanha(client, headers, "Campanha B")
    sequencia = _sequencia(client, headers, campanha_a)
    p = _prospect(t["tenant_id"], campanha_b)

    r = client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )
    assert r.json()["skipped"][0]["reason"] == "é de outra campanha"


# --------------------------------------------------------------------- tick
def test_tick_gera_o_primeiro_toque_e_agenda_o_segundo(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )

    r = client.post("/api/v1/sequences/tick", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["gerados"] == 1

    jobs = _jobs_de_outreach(t["tenant_id"])
    assert len(jobs) == 1
    assert jobs[0].payload["agent"] == "outreach"
    assert jobs[0].payload["params"]["sequence_step"]["order"] == 1
    assert jobs[0].payload["params"]["sequence_step"]["total_steps"] == 3

    inscricoes = client.get(
        f"/api/v1/sequences/{sequencia['id']}/enrollments", headers=headers
    ).json()
    assert inscricoes[0]["current_step"] == 1
    assert inscricoes[0]["status"] == "active"
    # O passo 2 espera três dias: não sai hoje.
    assert client.post("/api/v1/sequences/tick", headers=headers).json()["gerados"] == 0


def test_cadencia_termina_no_ultimo_passo(client, make_tenant, auth_headers, db_for):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )

    # Três toques, adiantando o relógio entre eles.
    for esperado in (1, 2, 3):
        with tenant_session(t["tenant_id"]) as session:
            resultado = sequences.tick(
                session, t["tenant_id"], agora=datetime.now(UTC) + timedelta(days=30 * esperado)
            )
        assert resultado["gerados"] == 1, esperado

    inscricao = client.get(
        f"/api/v1/sequences/{sequencia['id']}/enrollments", headers=headers
    ).json()[0]
    assert inscricao["status"] == "completed"
    assert inscricao["current_step"] == 3
    assert inscricao["next_run_at"] is None
    assert len(_jobs_de_outreach(t["tenant_id"])) == 3


# ----------------------------------------------------------- regras de parada
@pytest.mark.parametrize("gatilho", ["respondeu", "descadastro", "reuniao", "qualificado"])
def test_cadencia_para_quando_o_lead_sai_do_funil(client, make_tenant, auth_headers, gatilho):
    """Verificado a cada passo, não uma vez na entrada: entre um toque e o
    seguinte passam dias, e é nesse intervalo que o lead responde."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(tenant_id, campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )
    client.post("/api/v1/sequences/tick", headers=headers)  # primeiro toque sai

    with tenant_session(tenant_id) as session:
        if gatilho == "respondeu":
            conversa = Conversation(tenant_id=tenant_id, prospect_id=p["prospect_id"])
            session.add(conversa)
            session.flush()
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversa.id,
                    direction="inbound",
                    status="replied",
                    channel="email",
                    body="Me manda mais detalhes",
                )
            )
        elif gatilho == "descadastro":
            session.get(Contact, p["contact_id"]).opted_out = True
        elif gatilho == "reuniao":
            session.add(
                Meeting(
                    tenant_id=tenant_id,
                    prospect_id=p["prospect_id"],
                    scheduled_at=datetime.now(UTC) + timedelta(days=2),
                )
            )
        elif gatilho == "qualificado":
            session.add(
                Qualification(
                    tenant_id=tenant_id,
                    prospect_id=p["prospect_id"],
                    outcome="qualified",
                    confidence=80,
                )
            )
        session.flush()

    with tenant_session(tenant_id) as session:
        resultado = sequences.tick(session, tenant_id, agora=datetime.now(UTC) + timedelta(days=30))
    assert resultado["parados"] == 1
    assert resultado["gerados"] == 0

    inscricao = client.get(
        f"/api/v1/sequences/{sequencia['id']}/enrollments", headers=headers
    ).json()[0]
    assert inscricao["status"] == "stopped"
    assert inscricao["stop_reason"]
    # Um toque saiu (o primeiro) e nada depois disso.
    assert len(_jobs_de_outreach(tenant_id)) == 1


def test_campanha_pausada_adia_em_vez_de_parar(client, make_tenant, auth_headers):
    """Quem pausou pretende retomar; parar exigiria reinscrever todo mundo."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )

    with tenant_session(t["tenant_id"]) as session:
        session.get(Campaign, campanha).status = "paused"

    r = client.post("/api/v1/sequences/tick", headers=headers)
    assert r.json() == {"gerados": 0, "parados": 0, "concluidos": 0, "adiados": 1}

    inscricao = client.get(
        f"/api/v1/sequences/{sequencia['id']}/enrollments", headers=headers
    ).json()[0]
    assert inscricao["status"] == "active"
    assert inscricao["current_step"] == 0


def test_sequencia_desativada_tambem_adia(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )
    client.patch(f"/api/v1/sequences/{sequencia['id']}", json={"is_active": False}, headers=headers)

    assert client.post("/api/v1/sequences/tick", headers=headers).json()["adiados"] == 1


def test_parada_manual(client, make_tenant, auth_headers):
    """Alguém descobre pelo telefone que o lead já comprou, e o follow-up de
    terça-feira precisa não sair."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)
    inscricao = client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    ).json()["enrolled"][0]

    r = client.post(f"/api/v1/sequences/enrollments/{inscricao['id']}/stop", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stopped"
    assert r.json()["stop_reason"] == "parada manualmente"

    assert client.post("/api/v1/sequences/tick", headers=headers).json()["gerados"] == 0
    repetida = client.post(f"/api/v1/sequences/enrollments/{inscricao['id']}/stop", headers=headers)
    assert repetida.status_code == 409


# -------------------------------------------------------------------- CRUD
def test_nao_apaga_cadencia_com_gente_dentro(client, make_tenant, auth_headers):
    """Apagar deixaria os prospects num limbo: fora da cadência, com toques já
    enviados e nenhum registro do porquê pararam."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(t["tenant_id"], campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )

    r = client.delete(f"/api/v1/sequences/{sequencia['id']}", headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["active_enrollments"] == 1


def test_apaga_cadencia_vazia(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)

    assert client.delete(f"/api/v1/sequences/{sequencia['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/sequences/{sequencia['id']}", headers=headers).status_code == 404


def test_cadencia_de_outra_empresa_nao_existe_daqui(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    headers_b = auth_headers(b["email"], b["password"])
    campanha = _campanha(client, headers_a)
    sequencia = _sequencia(client, headers_a, campanha)
    url = f"/api/v1/sequences/{sequencia['id']}"

    assert client.get("/api/v1/sequences", headers=headers_b).json() == []
    assert client.get(url, headers=headers_b).status_code == 404
    assert client.patch(url, json={"is_active": False}, headers=headers_b).status_code == 404
    assert client.delete(url, headers=headers_b).status_code == 404
    assert (
        client.post(
            url + "/enroll", json={"prospect_ids": [str(uuid.uuid4())]}, headers=headers_b
        ).status_code
        == 404
    )


def test_tick_de_uma_empresa_nao_mexe_na_outra(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    headers_b = auth_headers(b["email"], b["password"])
    campanha = _campanha(client, headers_a)
    sequencia = _sequencia(client, headers_a, campanha)
    p = _prospect(a["tenant_id"], campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers_a,
    )

    assert client.post("/api/v1/sequences/tick", headers=headers_b).json()["gerados"] == 0
    assert client.post("/api/v1/sequences/tick", headers=headers_a).json()["gerados"] == 1
    assert _jobs_de_outreach(b["tenant_id"]) == []


def test_inscricoes_orfas_sao_limpas(client, make_tenant, auth_headers):
    """Um prospect apagado não deixa a cadência girando em falso."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    headers = auth_headers(t["email"], t["password"])
    campanha = _campanha(client, headers)
    sequencia = _sequencia(client, headers, campanha)
    p = _prospect(tenant_id, campanha)
    client.post(
        f"/api/v1/sequences/{sequencia['id']}/enroll",
        json={"prospect_ids": [str(p["prospect_id"])]},
        headers=headers,
    )

    with tenant_session(tenant_id) as session:
        inscricao = session.execute(select(SequenceEnrollment)).scalar_one()
        inscricao.prospect_id = inscricao.prospect_id  # mantém o vínculo
        session.delete(session.get(Prospect, p["prospect_id"]))

    # A inscrição some junto (ON DELETE CASCADE) — o tick não encontra órfã.
    assert client.post("/api/v1/sequences/tick", headers=headers).json()["gerados"] == 0
