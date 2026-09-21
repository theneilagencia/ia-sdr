"""Envio: os freios, o descadastro e o que vai dentro do email.

Nenhum teste aqui manda email de verdade — o transporte é substituído. O que
está sendo verificado é a política em volta dele, que é a parte capaz de
queimar o domínio de um cliente.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.engagement import Conversation, Message, MessageStatus
from app.db.models.platform import Tenant
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session
from app.services import email_accounts, email_sender
from app.services.email_sender import SendBlocked, SendFailed
from app.services.unsubscribe import InvalidUnsubscribeToken, make_token, parse_token

# Uma terça-feira, 10h em São Paulo — dentro do horário comercial.
HORA_BOA = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)


@pytest.fixture
def enviados(monkeypatch):
    """Captura o que sairia pelo SMTP, sem sair."""
    capturados = []
    monkeypatch.setattr(
        email_sender, "_transport", lambda credenciais, mensagem: capturados.append(mensagem)
    )
    return capturados


@pytest.fixture
def pronto_para_enviar(make_tenant):
    """Empresa com conta de email configurada e um rascunho já aprovado."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        email_accounts.store(
            session,
            t["tenant_id"],
            provider="gmail",
            from_email="vendas@apymine.com",
            from_name="Vendas Apy Mine",
            username=None,
            password="senha-de-app",
            host=None,
            port=None,
            created_by=t["user_id"],
        )
        campanha = Campaign(tenant_id=t["tenant_id"], name="Mining Canada", slug="mc")
        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"],
            company_id=empresa.id,
            full_name="Alice",
            email="alice@northernore.ca",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="scored",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=t["tenant_id"],
            prospect_id=prospect.id,
            campaign_id=campanha.id,
            subject="Turnos em Sudbury",
        )
        session.add(conversa)
        session.flush()
        mensagem = Message(
            tenant_id=t["tenant_id"],
            conversation_id=conversa.id,
            direction="outbound",
            status=MessageStatus.QUEUED.value,
            subject="Turnos em Sudbury",
            body="Alice, vi as vagas em Sudbury.",
        )
        session.add(mensagem)
        session.flush()
        return {
            **t,
            "message_id": mensagem.id,
            "prospect_id": prospect.id,
            "contact_id": contato.id,
        }


def _enviar(cenario, **kwargs):
    with tenant_session(cenario["tenant_id"]) as session:
        return email_sender.send_message(
            session,
            tenant_id=cenario["tenant_id"],
            message_id=cenario["message_id"],
            agora=kwargs.pop("agora", HORA_BOA),
            **kwargs,
        )


# ------------------------------------------------------------------ o envio


def test_envio_marca_mensagem_e_move_o_prospect(pronto_para_enviar, enviados):
    _enviar(pronto_para_enviar)

    assert len(enviados) == 1
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        mensagem = session.get(Message, pronto_para_enviar["message_id"])
        assert mensagem.status == "sent" and mensagem.sent_at is not None
        # Só agora o prospect vira "contatado": antes do envio, ninguém foi contatado.
        assert session.get(Prospect, pronto_para_enviar["prospect_id"]).status == "contacted"


def test_email_leva_descadastro_no_corpo_e_no_cabecalho(pronto_para_enviar, enviados):
    """O botão nativo de cancelar inscrição do Gmail depende desse cabeçalho."""
    _enviar(pronto_para_enviar)
    email = enviados[0]

    assert email["To"] == "alice@northernore.ca"
    assert email["From"] == "Vendas Apy Mine <vendas@apymine.com>"
    assert "unsubscribe" in email["List-Unsubscribe"]
    assert email["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "cancele aqui" in email.get_content()


def test_message_id_do_email_fica_guardado(pronto_para_enviar, enviados):
    """É por ele que a resposta do lead volta a ser ligada a esta conversa."""
    _enviar(pronto_para_enviar)

    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        mensagem = session.get(Message, pronto_para_enviar["message_id"])
    assert mensagem.external_message_id == enviados[0]["Message-ID"]
    assert "@apymine.com>" in mensagem.external_message_id


def test_rascunho_nao_aprovado_nao_sai(pronto_para_enviar, enviados):
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        session.get(Message, pronto_para_enviar["message_id"]).status = "draft"

    with pytest.raises(SendBlocked) as exc:
        _enviar(pronto_para_enviar)
    assert "aprovado" in exc.value.message
    assert enviados == []


def test_descadastrado_nao_recebe_nem_com_mensagem_aprovada(pronto_para_enviar, enviados):
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        session.get(Contact, pronto_para_enviar["contact_id"]).opted_out = True

    with pytest.raises(SendBlocked):
        _enviar(pronto_para_enviar)
    assert enviados == []


def test_fora_do_horario_nao_envia(pronto_para_enviar, enviados):
    """3h da manhã denuncia robô, e provedor de email lê isso como sinal."""
    madrugada = datetime(2026, 9, 22, 6, 0, tzinfo=UTC)  # 3h em São Paulo
    with pytest.raises(SendBlocked) as exc:
        _enviar(pronto_para_enviar, agora=madrugada)
    assert "horário" in exc.value.message
    assert enviados == []


def test_servidor_inalcancavel_vira_mensagem_util(pronto_para_enviar, monkeypatch):
    """Rede é o modo de falha mais comum — e não pode virar 500 sem registro."""

    def sem_rede(credenciais, mensagem):
        raise TimeoutError("timed out")

    monkeypatch.setattr(email_sender, "_transport", sem_rede)
    with pytest.raises(SendFailed) as exc:
        _enviar(pronto_para_enviar)

    assert "alcançar smtp.gmail.com:587" in exc.value.message
    assert "Configurações" in exc.value.message
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        mensagem = session.get(Message, pronto_para_enviar["message_id"])
    assert mensagem.status == "failed"
    # O técnico para quem depura, o legível para quem opera.
    assert "TimeoutError" in mensagem.metrics["send_error"]
    assert "Confira o servidor" in mensagem.metrics["send_error_message"]


def test_senha_errada_diz_o_que_fazer(pronto_para_enviar, monkeypatch):
    import smtplib

    def recusa(credenciais, mensagem):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(email_sender, "_transport", recusa)
    with pytest.raises(SendFailed) as exc:
        _enviar(pronto_para_enviar)
    assert "senha de app" in exc.value.message


def test_falha_do_servidor_marca_a_mensagem_e_nao_some(pronto_para_enviar, monkeypatch):
    import smtplib

    def explode(credenciais, mensagem):
        raise smtplib.SMTPException("mailbox unavailable")

    monkeypatch.setattr(email_sender, "_transport", explode)
    with pytest.raises(SendFailed):
        _enviar(pronto_para_enviar)

    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        mensagem = session.get(Message, pronto_para_enviar["message_id"])
    assert mensagem.status == "failed"
    assert "mailbox unavailable" in mensagem.metrics["send_error"]


# ------------------------------------------------------------------ aquecimento


def test_primeiro_dia_de_aquecimento_limita_ao_inicio(pronto_para_enviar):
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        tenant = session.get(Tenant, pronto_para_enviar["tenant_id"])
        cota = email_sender.allowance_today(session, tenant, HORA_BOA)

    assert cota["limit"] == 10  # warmup_start, não os 30 do limite diário
    assert cota["warmup_day"] == 1
    assert "aquecimento" in cota["reason"]


def test_aquecimento_sobe_com_os_dias(pronto_para_enviar, enviados):
    """Depois do primeiro envio, a cota cresce dia a dia até o limite."""
    _enviar(pronto_para_enviar)  # marca o início do aquecimento

    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        tenant = session.get(Tenant, pronto_para_enviar["tenant_id"])
        quarto_dia = email_sender.allowance_today(
            session, tenant, HORA_BOA + timedelta(days=3)
        )
        muito_depois = email_sender.allowance_today(
            session, tenant, HORA_BOA + timedelta(days=60)
        )

    assert quarto_dia["limit"] == 25  # 10 + 5 * 3
    # Passado o aquecimento, vale o limite diário configurado.
    assert muito_depois["limit"] == 30
    assert muito_depois["reason"] == "limite diário configurado"


def test_cota_esgotada_bloqueia(pronto_para_enviar, enviados):
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        tenant = session.get(Tenant, pronto_para_enviar["tenant_id"])
        tenant.settings = {
            **(tenant.settings or {}),
            "sending_policy": {"daily_limit": 1, "warmup_enabled": False},
        }

    _enviar(pronto_para_enviar)  # o único que cabe hoje

    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        outra = Message(
            tenant_id=pronto_para_enviar["tenant_id"],
            conversation_id=session.execute(select(Conversation)).scalars().one().id,
            direction="outbound",
            status=MessageStatus.QUEUED.value,
            subject="Segunda",
            body="…",
        )
        session.add(outra)
        session.flush()
        segunda_id = outra.id

    with pytest.raises(SendBlocked) as exc:
        with tenant_session(pronto_para_enviar["tenant_id"]) as session:
            email_sender.send_message(
                session,
                tenant_id=pronto_para_enviar["tenant_id"],
                message_id=segunda_id,
                agora=HORA_BOA,
            )
    assert exc.value.details["limit"] == 1
    assert len(enviados) == 1


def test_fila_para_quando_a_cota_acaba(pronto_para_enviar, enviados):
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        tenant = session.get(Tenant, pronto_para_enviar["tenant_id"])
        tenant.settings = {
            **(tenant.settings or {}),
            "sending_policy": {"daily_limit": 2, "warmup_enabled": False},
        }
        conversa_id = session.execute(select(Conversation)).scalars().one().id
        for i in range(4):
            session.add(
                Message(
                    tenant_id=pronto_para_enviar["tenant_id"],
                    conversation_id=conversa_id,
                    direction="outbound",
                    status=MessageStatus.QUEUED.value,
                    subject=f"Fila {i}",
                    body="…",
                )
            )

    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        resultado = email_sender.send_queued(
            session, tenant_id=pronto_para_enviar["tenant_id"], agora=HORA_BOA
        )

    assert resultado["sent"] == 2
    assert len(enviados) == 2
    assert "Limite" in resultado["blocked"][0]["reason"]


# ------------------------------------------------------------------ descadastro


def test_token_de_descadastro_e_assinado():
    tenant, contato = uuid.uuid4(), uuid.uuid4()
    token = make_token(tenant, contato)
    assert parse_token(token) == (tenant, contato)

    with pytest.raises(InvalidUnsubscribeToken):
        parse_token(token[:-2] + "xx")  # assinatura adulterada
    with pytest.raises(InvalidUnsubscribeToken):
        parse_token("qualquer-coisa")


def test_clique_no_link_descadastra_de_verdade(client, pronto_para_enviar):
    token = make_token(pronto_para_enviar["tenant_id"], pronto_para_enviar["contact_id"])
    resposta = client.get(f"/api/v1/public/unsubscribe/{token}")

    assert resposta.status_code == 200
    assert "não receberá mais" in resposta.text

    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        assert session.get(Contact, pronto_para_enviar["contact_id"]).opted_out is True
        assert session.get(Prospect, pronto_para_enviar["prospect_id"]).status == "disqualified"


def test_descadastro_aceita_post_do_botao_nativo(client, pronto_para_enviar):
    token = make_token(pronto_para_enviar["tenant_id"], pronto_para_enviar["contact_id"])
    assert client.post(f"/api/v1/public/unsubscribe/{token}").status_code == 200


def test_link_adulterado_nao_descadastra_ninguem(client, pronto_para_enviar):
    resposta = client.get("/api/v1/public/unsubscribe/token-inventado")
    assert resposta.status_code == 400
    with tenant_session(pronto_para_enviar["tenant_id"]) as session:
        assert session.get(Contact, pronto_para_enviar["contact_id"]).opted_out is False
