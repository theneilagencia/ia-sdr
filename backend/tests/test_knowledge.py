"""Base de conhecimento: ingestão e recuperação.

O que estes testes protegem não é a API — é a resposta do agente. O
Conversation Agent responde *apenas* com o que a recuperação entrega e escala
para humano quando não encontra. Recuperação que devolve o trecho errado
produz um agente que escala tudo, e um agente que escala tudo é desligado.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.knowledge import MAX_CHARS, MIN_CHARS, chunk_text

PRECOS = (
    "Nossa política de reembolso devolve o valor integral em até trinta dias.\n\n"
    "Depois desse prazo, o crédito fica disponível para renovação."
)
IMPLANTACAO = (
    "A implantação leva seis semanas e inclui migração dos dados históricos.\n\n"
    "O treinamento da equipe acontece na quarta semana, remoto."
)


def _doc(client, headers, titulo, conteudo, campanhas=None):
    r = client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": titulo,
            "content": conteudo,
            "campaign_ids": [str(c) for c in (campanhas or [])],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _campanha(client, headers, nome="Campanha"):
    r = client.post(
        "/api/v1/campaigns",
        json={"name": nome, "slug": f"c-{uuid.uuid4().hex[:8]}", "channels": ["email"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ fatiamento
def test_fatia_respeita_paragrafo():
    texto = "\n\n".join(f"Parágrafo {i} com algum conteúdo." for i in range(3))
    assert chunk_text(texto) == [texto]


def test_paragrafo_maior_que_a_fatia_quebra_em_frases():
    frase = "Esta é uma frase completa com algum conteúdo relevante. "
    texto = frase * 60  # bem acima de MAX_CHARS, num parágrafo só
    fatias = chunk_text(texto)

    assert len(fatias) > 1
    assert all(len(f) <= MAX_CHARS for f in fatias)
    # Quebrar no meio da frase estraga a resposta do agente mais do que perde
    # na recuperação: cada fatia termina num ponto.
    assert all(f.rstrip().endswith(".") for f in fatias)


def test_sobra_curta_no_fim_junta_com_a_anterior():
    texto = ("a" * (MAX_CHARS - 50)) + "\n\n" + ("b" * (MIN_CHARS - 20))
    fatias = chunk_text(texto)
    assert len(fatias) == 1


def test_texto_vazio_nao_gera_fatia():
    assert chunk_text("   \n\n  ") == []


# -------------------------------------------------------------------- ingestão
def test_documento_nasce_indexado_e_com_fatias(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    doc = _doc(client, headers, "Reembolso", PRECOS)
    assert doc["status"] == "indexed"
    assert doc["chunk_count"] >= 1
    assert doc["char_count"] == len(PRECOS)

    detalhe = client.get(f"/api/v1/knowledge/documents/{doc['id']}", headers=headers).json()
    assert len(detalhe["chunks"]) == doc["chunk_count"]
    assert detalhe["chunks"][0]["token_count"] > 0


def test_mesmo_conteudo_nao_duplica(client, make_tenant, auth_headers):
    """Ver o agente citar o mesmo trecho em dobro faz a empresa desconfiar de
    tudo o que ele responde."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    primeiro = _doc(client, headers, "Reembolso", PRECOS)
    segundo = _doc(client, headers, "Reembolso de novo", PRECOS)

    assert primeiro["id"] == segundo["id"]
    assert len(client.get("/api/v1/knowledge/documents", headers=headers).json()) == 1


def test_documento_sem_texto_e_recusado(client, make_tenant, auth_headers):
    """A mensagem diz o que aceita: "documento vazio" mandaria a pessoa tentar
    de novo com o mesmo arquivo."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    r = client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Vazio", "content": "   "},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    assert "Markdown" in r.json()["error"]["message"]


def test_limite_do_plano_vale_para_documento(client, make_tenant, auth_headers, db_for):
    from app.db.models.platform import Tenant
    from app.db.session import unscoped_session

    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    with unscoped_session(reason="test:limit") as s:
        s.get(Tenant, t["tenant_id"]).limit_overrides = {"knowledge_documents": 1}

    _doc(client, headers, "Primeiro", PRECOS)
    r = client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Segundo", "content": IMPLANTACAO},
        headers=headers,
    )
    assert r.status_code == 402


# ---------------------------------------------------------------------- upload
@pytest.mark.parametrize(
    ("nome", "bytes_", "trecho"),
    [
        ("playbook.pdf", b"%PDF-1.7\n stuff", "PDF"),
        ("contrato.docx", b"PK\x03\x04 stuff", "Word"),
        ("planilha.xls", b"\xd0\xcf\x11\xe0 stuff", "Word"),
        ("imagem.png", b"\x89PNG\r\n\x1a\n", ".png"),
    ],
)
def test_upload_binario_explica_o_que_fazer(
    client, make_tenant, auth_headers, nome, bytes_, trecho
):
    """ "Documento vazio" mandaria a pessoa tentar de novo com o mesmo arquivo."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": (nome, bytes_, "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    assert trecho in r.json()["error"]["message"]


def test_upload_de_markdown_indexa(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("playbook.md", IMPLANTACAO.encode(), "text/markdown")},
        data={"title": "Playbook"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Playbook"
    assert r.json()["chunk_count"] >= 1


def test_upload_em_latin1_ainda_e_lido(client, make_tenant, auth_headers):
    """Arquivo exportado de sistema antigo vem em Latin-1; recusar seria pedir
    para a pessoa resolver encoding."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    r = client.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("caso.txt", "Implantação em três semanas".encode("latin-1"), "text/plain")},
        headers=headers,
    )
    assert r.status_code == 201, r.text


# ----------------------------------------------------------------------- busca
def test_busca_devolve_o_relevante_e_nao_o_recente(client, make_tenant, auth_headers):
    """O ponto todo da migration: antes, o último carregado ganhava sempre."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    _doc(client, headers, "Reembolso", PRECOS)
    _doc(client, headers, "Implantação", IMPLANTACAO)  # mais recente

    achados = client.get(
        "/api/v1/knowledge/search", params={"q": "reembolso"}, headers=headers
    ).json()
    assert achados
    assert achados[0]["title"] == "Reembolso"
    assert achados[0]["relevance"] > 0
    assert "reembolso" in achados[0]["excerpt"].lower()


def test_busca_acha_pelo_titulo(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _doc(client, headers, "Manual de integração com ERP", IMPLANTACAO)

    achados = client.get("/api/v1/knowledge/search", params={"q": "ERP"}, headers=headers).json()
    assert achados and achados[0]["title"].startswith("Manual")


def test_pergunta_so_com_palavra_vazia_cai_para_os_recentes(client, make_tenant, auth_headers):
    """Devolver nada faria o agente escalar por falta de contexto — pior do que
    devolver contexto imperfeito."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _doc(client, headers, "Reembolso", PRECOS)

    achados = client.get(
        "/api/v1/knowledge/search", params={"q": "e para isso de a"}, headers=headers
    ).json()
    assert achados
    assert achados[0]["relevance"] == 0


def test_escopo_por_campanha(client, make_tenant, auth_headers):
    # Duas campanhas: o plano starter só permite uma.
    t = make_tenant(plan="enterprise")
    headers = auth_headers(t["email"], t["password"])
    campanha_a = _campanha(client, headers, "Campanha A")
    campanha_b = _campanha(client, headers, "Campanha B")

    _doc(client, headers, "Só da A", PRECOS, campanhas=[campanha_a])
    _doc(client, headers, "De todas", IMPLANTACAO)

    def titulos(**params):
        return {
            a["title"]
            for a in client.get("/api/v1/knowledge/search", params=params, headers=headers).json()
        }

    # Com a campanha A, o documento restrito a ela entra e é o que casa.
    assert titulos(q="reembolso", campaign_id=campanha_a) == {"Só da A"}
    # Com a campanha B, ele não entra em nenhuma hipótese. A busca por
    # "reembolso" não casa com nada visível para B e cai para os recentes — o
    # que prova as duas coisas de uma vez: o recuo funciona, e o recuo também
    # respeita o escopo.
    assert "Só da A" not in titulos(q="reembolso", campaign_id=campanha_b)
    assert titulos(q="reembolso", campaign_id=campanha_b) == {"De todas"}
    assert "De todas" in titulos(q="implantação", campaign_id=campanha_b)
    # Sem campanha no pedido, documento com escopo não entra: ele foi restrito
    # de propósito, e vazar para outro contexto é o erro mais caro aqui.
    assert titulos(q="reembolso") == {"De todas"}
    assert titulos(q="") == {"De todas"}


def test_campanha_de_outra_empresa_nao_serve_de_escopo(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    headers_b = auth_headers(b["email"], b["password"])
    campanha_b = _campanha(client, headers_b, "Da B")

    r = client.post(
        "/api/v1/knowledge/documents",
        json={"title": "x", "content": PRECOS, "campaign_ids": [campanha_b]},
        headers=headers_a,
    )
    assert r.status_code == 404


# ------------------------------------------------------------------ isolamento
def test_base_de_uma_empresa_nao_aparece_na_outra(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    headers_b = auth_headers(b["email"], b["password"])

    doc = _doc(client, headers_a, "Reembolso", PRECOS)

    assert client.get("/api/v1/knowledge/documents", headers=headers_b).json() == []
    assert (
        client.get("/api/v1/knowledge/search", params={"q": "reembolso"}, headers=headers_b).json()
        == []
    )
    url = f"/api/v1/knowledge/documents/{doc['id']}"
    assert client.get(url, headers=headers_b).status_code == 404
    assert client.delete(url, headers=headers_b).status_code == 404


def test_apagar_documento_leva_as_fatias(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    doc = _doc(client, headers, "Reembolso", PRECOS)

    apagar = client.delete(f"/api/v1/knowledge/documents/{doc['id']}", headers=headers)
    assert apagar.status_code == 204
    assert (
        client.get("/api/v1/knowledge/search", params={"q": "reembolso"}, headers=headers).json()
        == []
    )


def test_viewer_le_mas_nao_escreve(client, make_tenant, auth_headers, membro):
    t = make_tenant()
    viewer = membro(t, role="viewer")
    do_viewer = auth_headers(viewer["email"], viewer["password"])

    assert client.get("/api/v1/knowledge/documents", headers=do_viewer).status_code == 200
    assert (
        client.post(
            "/api/v1/knowledge/documents",
            json={"title": "x", "content": PRECOS},
            headers=do_viewer,
        ).status_code
        == 403
    )


# --------------------------------------------- o que o agente de fato recebe
def test_agente_recebe_o_trecho_que_responde_a_pergunta_do_lead(make_tenant):
    """O teste que importa: não basta a busca funcionar por HTTP.

    O Conversation Agent responde *apenas* com o que a recuperação entrega. Se
    o contexto trouxer o documento mais recente em vez do que responde à
    pergunta, o agente escala para humano — e a empresa conclui que o agente
    não serve.
    """
    from app.db.models.engagement import Conversation, Message
    from app.db.models.sales import Campaign, Company, Contact, Prospect
    from app.db.session import tenant_session
    from app.orchestrator.context_builder import build_context
    from app.orchestrator.runner import new_job
    from app.services import knowledge

    t = make_tenant()
    tenant_id = t["tenant_id"]
    with tenant_session(tenant_id) as session:
        # Duas bases. A que responde à pergunta é a mais ANTIGA de propósito.
        knowledge.ingest_document(session, tenant_id, title="Reembolso", content=PRECOS)
        knowledge.ingest_document(session, tenant_id, title="Implantação", content=IMPLANTACAO)

        campanha = Campaign(tenant_id=tenant_id, name="C", slug="c")
        empresa = Company(tenant_id=tenant_id, name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=tenant_id, company_id=empresa.id, full_name="Alice", email="a@n.ca"
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
            tenant_id=tenant_id, prospect_id=prospect.id, campaign_id=campanha.id
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversa.id,
                direction="inbound",
                status="replied",
                channel="email",
                subject="Dúvida",
                body="Como funciona o reembolso se eu cancelar?",
            )
        )
        session.flush()

        envelope = new_job(
            tenant_id=tenant_id,
            agent="conversation",
            campaign_id=campanha.id,
            entity_type="conversation",
            entity_id=conversa.id,
        )
        contexto = build_context(session, envelope)

    assert contexto.knowledge
    assert contexto.knowledge[0]["title"] == "Reembolso"
    assert contexto.knowledge[0]["relevance"] > 0
