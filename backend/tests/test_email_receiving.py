"""Recebimento: a resposta do lead volta para a conversa certa — ou para nenhuma.

O risco aqui não é perder uma resposta: é colocá-la na conversa errada, o que
faria o agente responder a um lead com o contexto de outro.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from sqlalchemy import select

from app.db.models.engagement import Conversation, Message
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session
from app.services import email_receiver
from app.services.email_receiver import InboxUnavailable, parse_email, record_inbound

ORIGINAL_ID = "<abordagem-1@apymine.com>"

#: Índices das mensagens que a caixa falsa marcou como lidas na última leitura.
_MARCADAS: list[int] = []


def _caixa_falsa(monkeypatch, mensagens: list[bytes]):
    """Substitui a leitura IMAP, preservando a ordem que importa.

    A caixa falsa entrega cada mensagem ao `processar` e **só** registra como
    lida aquela para a qual ele devolveu `True`. É essa ordem — processar antes
    de marcar — que impede a resposta de um lead de desaparecer quando outra
    mensagem do mesmo lote dá defeito.
    """
    _MARCADAS.clear()

    def ler(host, port, username, password, limite, processar):
        for indice, bruto in enumerate(mensagens[:limite]):
            if processar(bruto):
                _MARCADAS.append(indice)
        return len(mensagens[:limite])

    monkeypatch.setattr(email_receiver, "_ler_caixa", ler)
    return _MARCADAS


def marcadas() -> list[int]:
    return list(_MARCADAS)


def _email_bruto(
    *, de: str, assunto: str, corpo: str, in_reply_to: str | None = None, msg_id: str = "<r1@lead>"
) -> bytes:
    email = EmailMessage()
    email["Message-ID"] = msg_id
    email["From"] = de
    email["To"] = "vendas@apymine.com"
    email["Subject"] = assunto
    if in_reply_to:
        email["In-Reply-To"] = in_reply_to
        email["References"] = in_reply_to
    email.set_content(corpo)
    return email.as_bytes()


@pytest.fixture
def conversa_com_abordagem_enviada(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
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
            status="contacted",
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
        session.add(
            Message(
                tenant_id=t["tenant_id"],
                conversation_id=conversa.id,
                direction="outbound",
                status="sent",
                subject="Turnos em Sudbury",
                body="Alice, vi as vagas…",
                external_message_id=ORIGINAL_ID,
            )
        )
        session.flush()
        return {
            **t,
            "conversation_id": conversa.id,
            "prospect_id": prospect.id,
            "contact_id": contato.id,
        }


def _receber(cenario, bruto: bytes):
    with tenant_session(cenario["tenant_id"]) as session:
        return record_inbound(
            session, tenant_id=cenario["tenant_id"], recebido=parse_email(bruto)
        )


# ------------------------------------------------------------------ pareamento


def test_resposta_com_in_reply_to_volta_para_a_conversa(conversa_com_abordagem_enviada):
    resultado = _receber(
        conversa_com_abordagem_enviada,
        _email_bruto(
            de="Alice <alice@northernore.ca>",
            assunto="Re: Turnos em Sudbury",
            corpo="Faz sentido. Pode ser quinta?",
            in_reply_to=ORIGINAL_ID,
        ),
    )
    assert resultado is not None

    with tenant_session(conversa_com_abordagem_enviada["tenant_id"]) as session:
        entrada = session.execute(
            select(Message).where(Message.direction == "inbound")
        ).scalars().one()
        assert entrada.conversation_id == conversa_com_abordagem_enviada["conversation_id"]
        assert "quinta" in entrada.body
        # Quem respondeu está engajado, seja qual for o teor.
        prospect = session.get(Prospect, conversa_com_abordagem_enviada["prospect_id"])
        assert prospect.status == "engaged"


def test_sem_cabecalho_cai_para_o_email_do_remetente(conversa_com_abordagem_enviada):
    """Encaminhamento e clientes que reescrevem cabeçalho são comuns."""
    resultado = _receber(
        conversa_com_abordagem_enviada,
        _email_bruto(
            de="alice@northernore.ca",
            assunto="sobre turnos",
            corpo="Me manda mais detalhes.",
        ),
    )
    assert resultado is not None
    assert resultado.conversation_id == conversa_com_abordagem_enviada["conversation_id"]


def test_email_de_desconhecido_e_ignorado(conversa_com_abordagem_enviada):
    """Inventar a conversa seria pior do que perder a mensagem."""
    assert (
        _receber(
            conversa_com_abordagem_enviada,
            _email_bruto(de="spam@qualquer.com", assunto="Promoção", corpo="Compre agora"),
        )
        is None
    )
    with tenant_session(conversa_com_abordagem_enviada["tenant_id"]) as session:
        assert (
            session.execute(select(Message).where(Message.direction == "inbound"))
            .scalars()
            .all()
            == []
        )


def test_ler_a_caixa_duas_vezes_nao_duplica(conversa_com_abordagem_enviada):
    bruto = _email_bruto(
        de="alice@northernore.ca",
        assunto="Re: Turnos",
        corpo="Pode ser quinta.",
        in_reply_to=ORIGINAL_ID,
    )
    assert _receber(conversa_com_abordagem_enviada, bruto) is not None
    assert _receber(conversa_com_abordagem_enviada, bruto) is None

    with tenant_session(conversa_com_abordagem_enviada["tenant_id"]) as session:
        entradas = session.execute(
            select(Message).where(Message.direction == "inbound")
        ).scalars().all()
    assert len(entradas) == 1


def test_resposta_nao_atravessa_tenant(conversa_com_abordagem_enviada, make_tenant):
    """O mesmo Message-ID não pode parear na conversa de outra empresa."""
    outro = make_tenant()
    with tenant_session(outro["tenant_id"]) as session:
        resultado = record_inbound(
            session,
            tenant_id=outro["tenant_id"],
            recebido=parse_email(
                _email_bruto(
                    de="alice@northernore.ca",
                    assunto="Re: Turnos",
                    corpo="…",
                    in_reply_to=ORIGINAL_ID,
                )
            ),
        )
    assert resultado is None


# ------------------------------------------------------------------ leitura


def test_multipart_usa_o_texto_puro(conversa_com_abordagem_enviada):
    email = EmailMessage()
    email["Message-ID"] = "<multi@lead>"
    email["From"] = "alice@northernore.ca"
    email["Subject"] = "Re: Turnos"
    email["In-Reply-To"] = ORIGINAL_ID
    email.set_content("Texto puro da resposta.")
    email.add_alternative("<p>Versão HTML cheia de citação</p>", subtype="html")

    recebido = parse_email(email.as_bytes())
    assert "Texto puro" in recebido.body
    assert "<p>" not in recebido.body


def test_assunto_codificado_e_decodificado():
    email = EmailMessage()
    email["Message-ID"] = "<acentos@lead>"
    email["From"] = "alice@northernore.ca"
    email["Subject"] = "Re: Operação em expansão"
    email.set_content("…")

    assert parse_email(email.as_bytes()).subject == "Re: Operação em expansão"


def test_servidor_proprio_sem_imap_avisa_o_que_falta():
    with pytest.raises(InboxUnavailable) as exc:
        email_receiver.imap_settings("smtp", {})
    assert "Configurações" in exc.value.message


def test_gmail_e_outlook_tem_padrao_de_leitura():
    assert email_receiver.imap_settings("gmail", {}) == ("imap.gmail.com", 993)
    assert email_receiver.imap_settings("outlook", {}) == ("outlook.office365.com", 993)
    # O que a empresa informar vence o padrão.
    assert email_receiver.imap_settings(
        "gmail", {"imap_host": "imap.proprio.com", "imap_port": 143}
    ) == ("imap.proprio.com", 143)


def test_sem_conta_configurada_a_leitura_avisa(make_tenant):
    t = make_tenant()
    with pytest.raises(InboxUnavailable) as exc:
        with tenant_session(t["tenant_id"]) as session:
            email_receiver.fetch_inbox(session, tenant_id=t["tenant_id"])
    assert "Nenhuma conta de email" in exc.value.message


def _com_conta_de_email(tenant_id):
    """A leitura exige conta configurada; sem isso a recusa vem antes do laço."""
    from app.services import email_accounts

    with tenant_session(tenant_id) as session:
        email_accounts.store(
            session,
            tenant_id,
            provider="gmail",
            from_email="vendas@apymine.com",
            from_name="Vendas",
            username=None,
            password="senha-de-app",
            host=None,
            port=None,
            created_by=None,
        )


def test_caixa_lida_grava_o_que_parear(conversa_com_abordagem_enviada, monkeypatch):
    """O caminho inteiro, com o IMAP substituído."""
    _com_conta_de_email(conversa_com_abordagem_enviada["tenant_id"])

    _caixa_falsa(
        monkeypatch,
        [
            _email_bruto(
                de="alice@northernore.ca",
                assunto="Re: Turnos",
                corpo="Pode ser quinta.",
                in_reply_to=ORIGINAL_ID,
            ),
            _email_bruto(de="ninguem@outro.com", assunto="oi", corpo="?", msg_id="<x@y>"),
        ],
    )

    with tenant_session(conversa_com_abordagem_enviada["tenant_id"]) as session:
        resultado = email_receiver.fetch_inbox(
            session, tenant_id=conversa_com_abordagem_enviada["tenant_id"]
        )

    assert resultado["fetched"] == 2
    assert resultado["recorded"] == 1
    assert resultado["ignored"] == 1
    assert resultado["bounced"] == 0 and resultado["failed"] == 0
    # A conversa que recebeu mensagem **nesta** leitura, para o worker acionar o
    # agente só nela. A que não recebeu nada não entra.
    assert len(resultado["conversations"]) == 1
    # As duas foram processadas, então as duas podem ser marcadas como lidas.
    assert marcadas() == [0, 1]


def test_mensagem_com_defeito_nao_leva_as_outras_nem_se_perde(
    conversa_com_abordagem_enviada, monkeypatch
):
    """A pior falha possível deste produto: a resposta do lead desaparecer.

    `fetch (RFC822)` marcava a caixa inteira como lida antes de gravar qualquer
    coisa, e o laço não tinha guarda por mensagem. Uma mensagem defeituosa no
    meio do lote abortava o processamento — e as outras, já marcadas como lidas,
    nunca voltavam na busca seguinte, que só pede UNSEEN.

    Agora: a defeituosa é contada, **fica não lida** para a próxima tentativa, e
    as outras seguem.
    """
    t = conversa_com_abordagem_enviada
    boa = _email_bruto(
        de="alice@northernore.ca",
        assunto="Re: Turnos",
        corpo="Pode ser quinta.",
        in_reply_to=ORIGINAL_ID,
    )
    defeituosa = _email_bruto(de="alice@northernore.ca", assunto="ruim", corpo="x", msg_id="<d@x>")
    _com_conta_de_email(t["tenant_id"])
    marcadas_pela_caixa = _caixa_falsa(monkeypatch, [defeituosa, boa])

    original = email_receiver.parse_email

    def parse_com_defeito(bruto: bytes):
        if bruto is defeituosa:
            raise ValueError("mensagem que a biblioteca não consegue ler")
        return original(bruto)

    monkeypatch.setattr(email_receiver, "parse_email", parse_com_defeito)

    with tenant_session(t["tenant_id"]) as session:
        resultado = email_receiver.fetch_inbox(session, tenant_id=t["tenant_id"])

    assert resultado["failed"] == 1
    assert resultado["recorded"] == 1, resultado
    # A defeituosa (índice 0) **não** foi marcada como lida; a boa (índice 1) sim.
    assert marcadas_pela_caixa == [1]

    with tenant_session(t["tenant_id"]) as session:
        recebidas = list(
            session.execute(
                select(Message).where(Message.direction == "inbound")
            ).scalars()
        )
    assert [m.body.strip() for m in recebidas] == ["Pode ser quinta."]
