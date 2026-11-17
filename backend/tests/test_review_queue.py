"""Fila de revisão: o portão humano entre a IA e a caixa de entrada de alguém."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.engagement import Conversation, Message
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session


def _cenario(tenant_id, corpo: str = "Rascunho escrito pela IA"):
    with tenant_session(tenant_id) as session:
        campanha = Campaign(tenant_id=tenant_id, name="Mining Canada", slug="mc")
        empresa = Company(tenant_id=tenant_id, name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=tenant_id, company_id=empresa.id, full_name="Alice", email="a@b.ca"
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=tenant_id,
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(tenant_id=tenant_id, prospect_id=prospect.id)
        session.add(conversa)
        session.flush()
        mensagem = Message(
            tenant_id=tenant_id,
            conversation_id=conversa.id,
            direction="outbound",
            status="draft",
            body=corpo,
            metrics={"to": "a@b.ca"},
        )
        session.add(mensagem)
        session.flush()
        return mensagem.id


def test_fila_mostra_rascunhos_por_padrao(client, make_tenant, auth_headers):
    t = make_tenant()
    _cenario(t["tenant_id"])
    headers = auth_headers(t["email"], t["password"])

    fila = client.get("/api/v1/messages", headers=headers).json()
    assert len(fila) == 1 and fila[0]["status"] == "draft"


def test_aprovar_coloca_na_fila_de_envio_e_registra_quem_aprovou(client, make_tenant, auth_headers):
    t = make_tenant()
    message_id = _cenario(t["tenant_id"])
    headers = auth_headers(t["email"], t["password"])

    r = client.post(f"/api/v1/messages/{message_id}/approve", headers=headers)
    assert r.status_code == 200, r.text
    corpo = r.json()
    # Aprovar não envia: a responsabilidade muda de mãos, a mensagem não sai.
    assert corpo["status"] == "queued"
    assert corpo["sent_at"] is None
    assert corpo["metrics"]["approved_by"] == str(t["user_id"])

    logs = client.get("/api/v1/tenants/me/audit", headers=headers).json()
    assert "message.approved" in {entrada["action"] for entrada in logs}


def test_recusar_guarda_o_motivo(client, make_tenant, auth_headers):
    t = make_tenant()
    message_id = _cenario(t["tenant_id"])
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        f"/api/v1/messages/{message_id}/reject",
        headers=headers,
        json={"reason": "tom errado para um CFO"},
    )
    assert r.json()["status"] == "rejected"
    assert r.json()["metrics"]["rejection_reason"] == "tom errado para um CFO"

    # Some da fila, mas continua no histórico: o que foi barrado também ensina.
    assert client.get("/api/v1/messages", headers=headers).json() == []
    with tenant_session(t["tenant_id"]) as session:
        assert session.execute(select(Message)).scalars().one().status == "rejected"


def test_nao_da_para_revisar_duas_vezes(client, make_tenant, auth_headers):
    t = make_tenant()
    message_id = _cenario(t["tenant_id"])
    headers = auth_headers(t["email"], t["password"])

    client.post(f"/api/v1/messages/{message_id}/approve", headers=headers)
    segunda = client.post(f"/api/v1/messages/{message_id}/approve", headers=headers)
    assert segunda.status_code == 409
    assert segunda.json()["error"]["code"] == "conflict"


def test_rascunho_de_outro_tenant_nao_aparece_nem_e_aprovavel(client, make_tenant, auth_headers):
    a = make_tenant()
    b = make_tenant()
    message_id = _cenario(a["tenant_id"])
    headers_b = auth_headers(b["email"], b["password"])

    assert client.get("/api/v1/messages", headers=headers_b).json() == []
    tentativa = client.post(f"/api/v1/messages/{message_id}/approve", headers=headers_b)
    assert tentativa.status_code == 404


def test_viewer_nao_aprova(client, make_tenant, auth_headers):
    """Aprovar é assumir responsabilidade pelo texto: leitura não basta."""
    from app.core.security import hash_password
    from app.db.models.platform import Membership, User
    from app.db.session import unscoped_session

    t = make_tenant()
    message_id = _cenario(t["tenant_id"])
    with unscoped_session(reason="test:add-viewer") as session:
        viewer = User(
            email="viewer@example.com",
            password_hash=hash_password("senha-forte-123"),
            full_name="Viewer",
        )
        session.add(viewer)
        session.flush()
        session.add(Membership(tenant_id=t["tenant_id"], user_id=viewer.id, role="viewer"))

    headers = auth_headers("viewer@example.com", "senha-forte-123")
    assert client.get("/api/v1/messages", headers=headers).status_code == 200
    recusado = client.post(f"/api/v1/messages/{message_id}/approve", headers=headers)
    assert recusado.status_code == 403


def test_mensagem_que_falhou_volta_para_a_fila(client, make_tenant, auth_headers):
    """Falha de rede não deveria exigir SQL para se recuperar."""
    t = make_tenant()
    message_id = _cenario(t["tenant_id"])
    headers = auth_headers(t["email"], t["password"])

    with tenant_session(t["tenant_id"]) as session:
        mensagem = session.get(Message, message_id)
        mensagem.status = "failed"
        mensagem.metrics = {"send_error": "TimeoutError: timed out"}

    r = client.post(f"/api/v1/messages/{message_id}/requeue", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    # O erro anterior fica guardado: some da vista, não do histórico.
    assert "timed out" in r.json()["metrics"]["previous_error"]


def test_so_o_que_falhou_volta_para_a_fila(client, make_tenant, auth_headers):
    t = make_tenant()
    message_id = _cenario(t["tenant_id"])
    headers = auth_headers(t["email"], t["password"])

    recusado = client.post(f"/api/v1/messages/{message_id}/requeue", headers=headers)
    assert recusado.status_code == 409
