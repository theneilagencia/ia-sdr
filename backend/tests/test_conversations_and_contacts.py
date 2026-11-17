"""Conversas e contatos.

A tabela `conversations` existia desde a primeira migration sem endpoint
nenhum: as respostas dos leads chegavam por IMAP, eram ligadas à conversa certa
e ficavam no banco, sem nenhuma forma de alguém ler o que o lead escreveu. O
agente até escalava para humano — e o humano não tinha onde olhar.
"""

from __future__ import annotations

import uuid

from app.db.models.engagement import Conversation, Message
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session


def _cenario(tenant_id, *, com_resposta_do_lead=True, opted_out=False):
    """Um prospect que recebeu abordagem e (talvez) respondeu."""
    with tenant_session(tenant_id) as session:
        campanha = Campaign(tenant_id=tenant_id, name="C", slug=f"c-{uuid.uuid4().hex[:6]}")
        empresa = Company(tenant_id=tenant_id, name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=tenant_id,
            company_id=empresa.id,
            full_name="Alice Tremblay",
            email=f"alice-{uuid.uuid4().hex[:6]}@northernore.ca",
            title="CFO",
            opted_out=opted_out,
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=tenant_id,
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="contacted",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            campaign_id=campanha.id,
            subject="Turnos em Sudbury",
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversa.id,
                direction="outbound",
                status="sent",
                channel="email",
                body="Oi Alice, tudo bem?",
            )
        )
        session.flush()
        if com_resposta_do_lead:
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversa.id,
                    direction="inbound",
                    status="replied",
                    channel="email",
                    body="Vocês cobrem escala 24/7?",
                )
            )
            session.flush()
        return {
            "campaign_id": campanha.id,
            "prospect_id": prospect.id,
            "conversation_id": conversa.id,
            "contact_id": contato.id,
            "company_id": empresa.id,
        }


# -------------------------------------------------------------------- conversas
def test_lista_traz_quem_e_o_lead_sem_uma_chamada_por_linha(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _cenario(t["tenant_id"])

    linhas = client.get("/api/v1/conversations", headers=headers).json()
    assert len(linhas) == 1
    linha = linhas[0]
    assert linha["contact_name"] == "Alice Tremblay"
    assert linha["company_name"] == "Northern Ore"
    assert linha["message_count"] == 2
    assert linha["awaiting_reply"] is True


def test_esperando_resposta_e_a_ultima_mensagem_ser_do_lead(client, make_tenant, auth_headers):
    """É o que a operação precisa ver primeiro: alguém escreveu e ninguém
    respondeu."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _cenario(t["tenant_id"], com_resposta_do_lead=False)
    _cenario(t["tenant_id"], com_resposta_do_lead=True)

    esperando = client.get(
        "/api/v1/conversations", params={"awaiting_reply": True}, headers=headers
    ).json()
    assert len(esperando) == 1
    assert esperando[0]["awaiting_reply"] is True

    resto = client.get(
        "/api/v1/conversations", params={"awaiting_reply": False}, headers=headers
    ).json()
    assert len(resto) == 1


def test_thread_vem_em_ordem_cronologica(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"])

    detalhe = client.get(f"/api/v1/conversations/{c['conversation_id']}", headers=headers).json()
    direcoes = [m["direction"] for m in detalhe["messages"]]
    assert direcoes == ["outbound", "inbound"]
    assert detalhe["contact_email"].startswith("alice-")


def test_passar_para_uma_pessoa_marca_a_conversa(client, make_tenant, auth_headers):
    """O outro lado do escalonamento: o agente diz que precisa de humano, aqui
    se diz qual."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"])

    r = client.patch(
        f"/api/v1/conversations/{c['conversation_id']}",
        json={"handoff_to_user_id": str(t["user_id"])},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "handed_off"
    assert r.json()["handoff_to_user_id"] == str(t["user_id"])

    devolvida = client.patch(
        f"/api/v1/conversations/{c['conversation_id']}",
        json={"clear_handoff": True},
        headers=headers,
    ).json()
    assert devolvida["handoff_to_user_id"] is None
    assert devolvida["status"] == "open"


def test_nao_passa_para_quem_nao_e_da_empresa(client, make_tenant, auth_headers):
    """A conversa ficaria parada num responsável que não consegue abri-la."""
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    c = _cenario(a["tenant_id"])

    r = client.patch(
        f"/api/v1/conversations/{c['conversation_id']}",
        json={"handoff_to_user_id": str(b["user_id"])},
        headers=headers_a,
    )
    assert r.status_code == 404


def test_fechar_e_reabrir(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"])
    url = f"/api/v1/conversations/{c['conversation_id']}"

    fechar = client.patch(url, json={"status": "closed"}, headers=headers)
    assert fechar.json()["status"] == "closed"
    fechadas = client.get(
        "/api/v1/conversations", params={"status": "closed"}, headers=headers
    ).json()
    assert len(fechadas) == 1
    assert client.patch(url, json={"status": "open"}, headers=headers).json()["status"] == "open"


def test_resposta_escrita_a_mao_nasce_rascunho(client, make_tenant, auth_headers):
    """Mesmo portão de aprovação do que o agente escreve: o caminho de envio —
    limite diário, aquecimento, horário, descadastro — está todo depois dele."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"])

    r = client.post(
        f"/api/v1/conversations/{c['conversation_id']}/messages",
        json={"body": "Cobrimos sim, desde a versão 3."},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"
    assert r.json()["direction"] == "outbound"

    rascunhos = client.get("/api/v1/messages", params={"status": "draft"}, headers=headers).json()
    assert len(rascunhos) == 1


def test_nao_se_escreve_para_quem_pediu_descadastro(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"], opted_out=True)

    r = client.post(
        f"/api/v1/conversations/{c['conversation_id']}/messages",
        json={"body": "Oi de novo!"},
        headers=headers,
    )
    assert r.status_code == 409
    assert (
        "descadastro" in r.json()["error"]["message"]
        or "não receber" in (r.json()["error"]["message"])
    )


def test_conversa_de_outra_empresa_nao_existe_daqui(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_b = auth_headers(b["email"], b["password"])
    c = _cenario(a["tenant_id"])
    url = f"/api/v1/conversations/{c['conversation_id']}"

    assert client.get("/api/v1/conversations", headers=headers_b).json() == []
    assert client.get(url, headers=headers_b).status_code == 404
    assert client.patch(url, json={"status": "closed"}, headers=headers_b).status_code == 404
    assert client.post(url + "/messages", json={"body": "oi"}, headers=headers_b).status_code == 404


# --------------------------------------------------------------------- contatos
def test_cria_le_e_atualiza_contato(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"])

    novo = client.post(
        "/api/v1/contacts",
        json={
            "full_name": "Bob Silva",
            "company_id": str(c["company_id"]),
            "email": "BOB@northernore.ca",
            "title": "COO",
        },
        headers=headers,
    )
    assert novo.status_code == 201, novo.text
    # Email guardado em minúsculas: é a chave de deduplicação do import.
    assert novo.json()["email"] == "bob@northernore.ca"

    contact_id = novo.json()["id"]
    atualizado = client.patch(
        f"/api/v1/contacts/{contact_id}", json={"title": "CEO"}, headers=headers
    )
    assert atualizado.status_code == 200
    assert atualizado.json()["title"] == "CEO"


def test_email_repetido_e_recusado(client, make_tenant, auth_headers):
    """Criar à mão não deveria ser a porta dos fundos para o mesmo lead entrar
    duas vezes na campanha."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    corpo = {"full_name": "Bob", "email": "bob@exemplo.com"}

    assert client.post("/api/v1/contacts", json=corpo, headers=headers).status_code == 201
    repetido = client.post("/api/v1/contacts", json=corpo, headers=headers)
    assert repetido.status_code == 409
    assert "contact_id" in repetido.json()["error"]["details"]


def test_descadastro_nao_se_desfaz_pela_api(client, make_tenant, auth_headers):
    """Desmarcar seria a forma mais fácil de voltar a escrever para quem pediu
    para parar."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    novo = client.post(
        "/api/v1/contacts", json={"full_name": "Bob", "email": "b@e.com"}, headers=headers
    ).json()

    marcar = client.patch(
        f"/api/v1/contacts/{novo['id']}", json={"opted_out": True}, headers=headers
    )
    assert marcar.status_code == 200
    assert marcar.json()["opted_out"] is True

    desmarcar = client.patch(
        f"/api/v1/contacts/{novo['id']}", json={"opted_out": False}, headers=headers
    )
    assert desmarcar.status_code == 422


def test_contato_com_historico_nao_e_apagado(client, make_tenant, auth_headers):
    """Apagar apagaria a prova de que o descadastro foi pedido — e a plataforma
    voltaria a escrever para essa pessoa no próximo import."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    c = _cenario(t["tenant_id"])

    r = client.delete(f"/api/v1/contacts/{c['contact_id']}", headers=headers)
    assert r.status_code == 409
    assert "descadastro" in r.json()["error"]["message"]


def test_contato_sem_campanha_pode_ser_apagado(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    novo = client.post(
        "/api/v1/contacts", json={"full_name": "Erro de digitação"}, headers=headers
    ).json()

    assert client.delete(f"/api/v1/contacts/{novo['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/contacts/{novo['id']}", headers=headers).status_code == 404


def test_busca_por_nome_e_email(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.post(
        "/api/v1/contacts",
        json={"full_name": "Carla Mendes", "email": "carla@acme.com"},
        headers=headers,
    )
    client.post("/api/v1/contacts", json={"full_name": "Daniel Souza"}, headers=headers)

    achados = client.get("/api/v1/contacts", params={"q": "carla"}, headers=headers).json()
    assert [c["full_name"] for c in achados] == ["Carla Mendes"]
    por_email = client.get("/api/v1/contacts", params={"q": "acme.com"}, headers=headers).json()
    assert len(por_email) == 1


def test_contato_de_outra_empresa_nao_existe_daqui(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_b = auth_headers(b["email"], b["password"])
    c = _cenario(a["tenant_id"])
    url = f"/api/v1/contacts/{c['contact_id']}"

    assert client.get("/api/v1/contacts", headers=headers_b).json() == []
    assert client.get(url, headers=headers_b).status_code == 404
    assert client.patch(url, json={"title": "x"}, headers=headers_b).status_code == 404
    assert client.delete(url, headers=headers_b).status_code == 404
