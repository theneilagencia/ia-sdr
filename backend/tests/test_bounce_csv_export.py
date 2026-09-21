"""Bounce, import por CSV e exportação.

Três coisas que faltavam e que têm o mesmo tema: o que acontece quando o mundo
real não coopera. O email volta, a planilha veio do Excel em português, e o
cliente quer ir embora levando os dados.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.engagement import (
    Conversation,
    EnrollmentStatus,
    Message,
    SequenceEnrollment,
)
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session
from app.services import ai_credentials, email_receiver
from app.services.csv_import import CsvInvalido, ler

# ------------------------------------------------------------------- fixtures
BOUNCE_PERMANENTE = b"""From: MAILER-DAEMON@mail.exemplo.com
To: vendas@minhaempresa.com
Subject: Undelivered Mail Returned to Sender
Message-ID: <bounce-1@mail.exemplo.com>
In-Reply-To: <original-1@minhaempresa.com>
Content-Type: multipart/report; report-type=delivery-status; boundary="XX"

--XX
Content-Type: text/plain

This is the mail system at host mail.exemplo.com.

--XX
Content-Type: message/delivery-status

Reporting-MTA: dns; mail.exemplo.com

Final-Recipient: rfc822; alice@northernore.ca
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 User unknown

--XX--
"""

BOUNCE_TEMPORARIO = BOUNCE_PERMANENTE.replace(b"Status: 5.1.1", b"Status: 4.2.2").replace(
    b"550 5.1.1 User unknown", b"452 4.2.2 Mailbox full"
)

RESPOSTA_NORMAL = b"""From: Alice <alice@northernore.ca>
To: vendas@minhaempresa.com
Subject: Re: Turnos
Message-ID: <resposta-1@northernore.ca>
In-Reply-To: <original-1@minhaempresa.com>
Content-Type: text/plain

Pode me mandar mais detalhes?
"""


@pytest.fixture
def prospect_abordado(make_tenant):
    """Um prospect com uma mensagem já enviada, com Message-ID conhecido."""
    t = make_tenant()
    tenant_id = t["tenant_id"]
    with tenant_session(tenant_id) as session:
        campanha = Campaign(tenant_id=tenant_id, name="Mining", slug="mining")
        empresa = Company(tenant_id=tenant_id, name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=tenant_id,
            company_id=empresa.id,
            full_name="Alice",
            email="alice@northernore.ca",
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
        conversa = Conversation(tenant_id=tenant_id, prospect_id=prospect.id)
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversa.id,
                direction="outbound",
                status="sent",
                channel="email",
                body="Oi Alice",
                external_message_id="<original-1@minhaempresa.com>",
            )
        )
        session.flush()
        return {
            **t,
            "prospect_id": prospect.id,
            "contact_id": contato.id,
            "conversation_id": conversa.id,
        }


def _receber(tenant_id, bruto):
    with tenant_session(tenant_id) as session:
        recebido = email_receiver.parse_email(bruto)
        resultado = email_receiver.record_inbound(session, tenant_id=tenant_id, recebido=recebido)
    return recebido, resultado


# --------------------------------------------------------------------- bounce
def test_bounce_permanente_tira_o_prospect_do_funil(prospect_abordado):
    """Contar bounce como resposta seria o pior dos mundos: o prospect viraria
    'engajado' e o agente escreveria uma réplica para um endereço que não
    existe."""
    tenant_id = prospect_abordado["tenant_id"]
    recebido, resultado = _receber(tenant_id, BOUNCE_PERMANENTE)

    assert recebido.bounce is not None
    assert recebido.bounce.permanent is True
    assert recebido.bounce.recipient == "alice@northernore.ca"
    assert recebido.bounce.status_code == "5.1.1"
    # Não vira mensagem da conversa: não foi o lead que escreveu.
    assert resultado is None

    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, prospect_abordado["prospect_id"])
        assert prospect.status == "bounced"
        original = session.execute(
            select(Message).where(Message.direction == "outbound")
        ).scalar_one()
        assert original.status == "bounced"
        assert original.metrics["bounce"]["status_code"] == "5.1.1"
        entradas = (
            session.execute(select(Message).where(Message.direction == "inbound")).scalars().all()
        )
        assert entradas == []


def test_bounce_temporario_nao_mexe_no_funil(prospect_abordado):
    """Caixa cheia esvazia; servidor fora do ar volta."""
    tenant_id = prospect_abordado["tenant_id"]
    recebido, _ = _receber(tenant_id, BOUNCE_TEMPORARIO)

    assert recebido.bounce.permanent is False
    with tenant_session(tenant_id) as session:
        assert session.get(Prospect, prospect_abordado["prospect_id"]).status == "contacted"


def test_bounce_permanente_para_a_cadencia(prospect_abordado):
    """Insistir num endereço inexistente é o jeito mais rápido de a reputação
    do domínio de quem manda cair."""
    from app.db.models.engagement import Sequence

    tenant_id = prospect_abordado["tenant_id"]
    with tenant_session(tenant_id) as session:
        prospect = session.get(Prospect, prospect_abordado["prospect_id"])
        sequencia = Sequence(
            tenant_id=tenant_id,
            campaign_id=prospect.campaign_id,
            name="C",
            steps=[{"order": 1, "wait_days": 0, "instruction": "a"}],
        )
        session.add(sequencia)
        session.flush()
        session.add(
            SequenceEnrollment(
                tenant_id=tenant_id,
                sequence_id=sequencia.id,
                prospect_id=prospect_abordado["prospect_id"],
                status=EnrollmentStatus.ACTIVE.value,
            )
        )
        session.flush()

    _receber(tenant_id, BOUNCE_PERMANENTE)

    with tenant_session(tenant_id) as session:
        inscricao = session.execute(select(SequenceEnrollment)).scalar_one()
        assert inscricao.status == "stopped"
        assert inscricao.stop_reason == "email inválido"


def test_resposta_de_verdade_continua_sendo_resposta(prospect_abordado):
    """A detecção de bounce não pode engolir o email que a operação espera."""
    tenant_id = prospect_abordado["tenant_id"]
    recebido, resultado = _receber(tenant_id, RESPOSTA_NORMAL)

    assert recebido.bounce is None
    assert resultado is not None
    with tenant_session(tenant_id) as session:
        assert session.get(Prospect, prospect_abordado["prospect_id"]).status == "engaged"


def test_aviso_sem_relatorio_e_tratado_como_temporario(prospect_abordado):
    """Sem código de status, tirar um lead do funil por causa de um assunto em
    inglês mal traduzido seria pior do que tentar de novo."""
    bruto = b"""From: postmaster@mail.exemplo.com
To: vendas@minhaempresa.com
Subject: Delivery Status Notification (Failure)
Message-ID: <aviso-1@mail.exemplo.com>
In-Reply-To: <original-1@minhaempresa.com>
Content-Type: text/plain

Sorry, we were unable to deliver your message.
"""
    tenant_id = prospect_abordado["tenant_id"]
    recebido, _ = _receber(tenant_id, bruto)

    assert recebido.bounce is not None
    assert recebido.bounce.permanent is False
    with tenant_session(tenant_id) as session:
        assert session.get(Prospect, prospect_abordado["prospect_id"]).status == "contacted"


# ------------------------------------------------------------------------ CSV
CSV_PADRAO = (
    "company_name,full_name,email,title\n"
    "Northern Ore,Alice Tremblay,alice@northernore.ca,CFO\n"
    "Sudbury Mining,Bob Silva,bob@sudbury.ca,COO\n"
)


def test_le_csv_simples():
    # Com BOM, que é o que o Excel escreve ao salvar como "CSV UTF-8".
    itens, erros = ler("\ufeff".encode() + CSV_PADRAO.encode())
    assert len(itens) == 2
    assert erros == []
    assert itens[0].company_name == "Northern Ore"
    assert str(itens[0].email) == "alice@northernore.ca"


def test_le_planilha_do_excel_em_portugues():
    """Ponto e vírgula, cabeçalhos em português e Latin-1 — que é como a
    planilha exportada do Excel em português chega."""
    bruto = (
        "Empresa;Nome;E-mail;Cargo;Funcionários\n"
        "Mineração Alfa;José Gonçalves;jose@alfa.com.br;Diretor;1.200\n"
    ).encode("latin-1")

    itens, erros = ler(bruto)
    assert erros == []
    assert itens[0].company_name == "Mineração Alfa"
    assert itens[0].full_name == "José Gonçalves"
    # "E-mail" vira "e_mail" na normalização do cabeçalho, e o apelido guardado
    # tinha hífen: a coluna nunca casava. Este teste já lia o cabeçalho certo e
    # não conferia o email — a lista inteira entrava sem endereço nenhum, em
    # silêncio, e o agente de abordagem se recusava a escrever depois.
    assert str(itens[0].email) == "jose@alfa.com.br"
    assert itens[0].title == "Diretor"
    # "1.200" vira 1200: perder a linha por causa do separador de milhar seria
    # absurdo, e o número é opcional.
    assert itens[0].employee_count == 1200


def test_linha_ruim_volta_com_o_numero_da_linha():
    """Import que diz '42 importados' e engole oito linhas é pior do que import
    que falha."""
    bruto = (
        b"company_name,full_name,email\n"
        b"Northern Ore,Alice,alice@northernore.ca\n"
        b"Sudbury,Bob,isso-nao-e-email\n"
    )

    itens, erros = ler(bruto)
    assert len(itens) == 1
    assert erros[0]["line"] == 3
    assert "email" in erros[0]["error"]


def test_csv_sem_as_colunas_obrigatorias_explica_o_que_falta():
    with pytest.raises(CsvInvalido) as erro:
        ler(b"telefone,cidade\n11999999999,Sudbury\n")
    mensagem = str(erro.value)
    assert "nome da empresa" in mensagem
    assert "telefone" in mensagem  # diz o que encontrou, para a pessoa comparar


def test_csv_vazio():
    with pytest.raises(CsvInvalido, match="vazio"):
        ler(b"   ")


def test_import_por_csv_pela_api(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = client.post(
        "/api/v1/campaigns",
        json={"name": "Mining", "slug": f"m-{uuid.uuid4().hex[:6]}", "channels": ["email"]},
        headers=headers,
    ).json()

    r = client.post(
        "/api/v1/prospects/import/csv",
        files={"file": ("lista.csv", CSV_PADRAO.encode(), "text/csv")},
        data={"campaign_id": campanha["id"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 2
    assert r.json()["rows_read"] == 2
    assert r.json()["row_errors"] == []

    # Subir de novo não duplica: a deduplicação do import em lote vale aqui.
    de_novo = client.post(
        "/api/v1/prospects/import/csv",
        files={"file": ("lista.csv", CSV_PADRAO.encode(), "text/csv")},
        data={"campaign_id": campanha["id"]},
        headers=headers,
    )
    assert de_novo.json()["imported"] == 0
    assert de_novo.json()["duplicates"] == 2


def test_linha_sem_email_entra_e_o_import_diz_quantas(client, make_tenant, auth_headers):
    """Lista de LinkedIn vem sem email, e isso não é erro — é consequência.

    A linha entra, e o relatório diz quantas vieram assim: quem opera descobre
    no import, não no disparo que não escreveu.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = client.post(
        "/api/v1/campaigns",
        json={"name": "Sem email", "slug": f"s-{uuid.uuid4().hex[:6]}"},
        headers=headers,
    ).json()

    arquivo = b"Empresa,Nome,E-mail\nCom Email,Alice,alice@comemail.com\nSem Email,Bruno,\n"
    r = client.post(
        "/api/v1/prospects/import/csv",
        files={"file": ("lista.csv", arquivo, "text/csv")},
        data={"campaign_id": campanha["id"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 2
    assert r.json()["missing_email"] == 1


def test_reimportar_lista_sem_email_nao_duplica(client, make_tenant, auth_headers):
    """Sem email, a deduplicação era por um campo vazio — ou seja, nenhuma.

    Quem sobe a mesma lista de LinkedIn duas vezes ganhava a base dobrada, e o
    import ainda dizia "importados" com orgulho. Nome dentro da mesma conta é o
    critério que uma pessoa usaria para dizer que é a mesma pessoa.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    campanha = client.post(
        "/api/v1/campaigns",
        json={"name": "LinkedIn", "slug": f"l-{uuid.uuid4().hex[:6]}"},
        headers=headers,
    ).json()
    arquivo = b"Empresa,Nome\nTerraplanagem Oeste,Marina Lopes\n"

    def subir():
        return client.post(
            "/api/v1/prospects/import/csv",
            files={"file": ("lista.csv", arquivo, "text/csv")},
            data={"campaign_id": campanha["id"]},
            headers=headers,
        ).json()

    assert subir()["imported"] == 1
    de_novo = subir()
    assert de_novo == {
        "imported": 0,
        "duplicates": 1,
        "prospect_ids": [],
        "rows_read": 1,
        "row_errors": [],
        "missing_email": 1,
    }
    assert len(client.get("/api/v1/prospects", headers=headers).json()) == 1


# ----------------------------------------------------------------- exportação
def test_exportacao_leva_os_dados_e_deixa_os_segredos(client, make_tenant, auth_headers):
    """Uma plataforma da qual não se sai é uma plataforma na qual não se entra."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.post(
        "/api/v1/campaigns",
        json={"name": "Mining", "slug": f"m-{uuid.uuid4().hex[:6]}", "channels": ["email"]},
        headers=headers,
    )
    # `PUT /settings/ai` testa a chave contra a API de verdade antes de salvar;
    # aqui interessa só que o segredo gravado não saia na exportação.
    with tenant_session(t["tenant_id"]) as session:
        ai_credentials.store(
            session, t["tenant_id"], api_key="sk-ant-teste-1234567890", created_by=t["user_id"]
        )

    r = client.get("/api/v1/tenants/me/export", headers=headers)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]

    pacote = r.json()
    assert pacote["tenant"]["id"] == str(t["tenant_id"])
    assert pacote["counts"]["campaigns"] == 1
    assert pacote["truncated"] == []

    # A integração aparece; o segredo dela, não — em lugar nenhum do arquivo.
    assert pacote["counts"]["integrations"] == 1
    assert "config" not in pacote["data"]["integrations"][0]
    assert "sk-ant-teste" not in r.text
    assert "config" in pacote["excluded"]


def test_exportacao_nao_traz_dado_de_outra_empresa(client, make_tenant, auth_headers):
    a, b = make_tenant(), make_tenant()
    headers_a = auth_headers(a["email"], a["password"])
    headers_b = auth_headers(b["email"], b["password"])
    client.post(
        "/api/v1/campaigns",
        json={"name": "Só da A", "slug": f"a-{uuid.uuid4().hex[:6]}", "channels": ["email"]},
        headers=headers_a,
    )

    pacote = client.get("/api/v1/tenants/me/export", headers=headers_b).json()
    assert pacote["counts"]["campaigns"] == 0
    assert "Só da A" not in str(pacote)


def test_operator_nao_exporta_a_base(client, make_tenant, auth_headers):
    """Levar a base inteira embora é decisão de quem responde pela empresa."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    email = f"op-{uuid.uuid4().hex[:8]}@example.com"
    client.post(
        "/api/v1/tenants/me/members",
        json={"email": email, "password": "senha-do-operador-1", "role": "operator"},
        headers=headers,
    )
    do_operator = auth_headers(email, "senha-do-operador-1")

    assert client.get("/api/v1/tenants/me/export", headers=do_operator).status_code == 403
