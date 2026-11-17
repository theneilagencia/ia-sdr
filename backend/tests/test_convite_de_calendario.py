"""Convite de calendário por `.ics`: o padrão que dispensa OAuth.

Integrar Google Calendar ou Microsoft Graph custa aplicativo verificado, tela de
consentimento por empresa e escopo aprovado — antes de entregar nada, e com o
cliente que não autoriza ficando sem o recurso. O `.ics` chega pelo caminho que já
existe: o email da própria empresa, com um `VEVENT` anexado, e o botão de aceitar
é nativo em Gmail, Outlook e Apple Mail.

O que estes testes protegem são as duas pontas: o arquivo estar conforme (cliente
de calendário descarta arquivo malformado **em silêncio**, e o sintoma é "o
convite não apareceu", sem nada no log de ninguém), e as guardas do envio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from email import policy

import pytest
from sqlalchemy import select

from app.db.models.engagement import Meeting
from app.db.models.sales import Contact
from app.db.session import tenant_session
from app.services import calendario, email_sender
from app.services.email_sender import SendBlocked

# Uma terça-feira, 10h em São Paulo.
HORA_BOA = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
REUNIAO = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)


def _linhas(ics: str) -> list[str]:
    """Desdobra as linhas de continuação, como um cliente de calendário faz.

    Aceita LF e CRLF porque as duas formas aparecem no caminho: o objeto de email
    guarda o texto normalizado com LF, e é só na serialização para o fio
    (`policy.SMTP`) que volta o CRLF que o RFC exige. Qual das duas se está
    olhando depende de onde o arquivo foi pego — e há um teste só para o fio.
    """
    saida: list[str] = []
    for bruta in ics.replace("\r\n", "\n").split("\n"):
        if bruta.startswith(" ") and saida:
            saida[-1] += bruta[1:]
        elif bruta:
            saida.append(bruta)
    return saida


def _ics_no_fio(email) -> str:
    """O arquivo como o destinatário recebe, e não como o objeto o guarda.

    `get_payload(decode=True)` devolve o texto com LF, porque é assim que a
    biblioteca o mantém em memória. O que sai pelo SMTP é serializado com
    `policy.SMTP`, e é ali que o CRLF do RFC aparece — então é ali que se verifica.
    """
    fio = email.as_bytes(policy=policy.SMTP).decode("utf-8", "replace")
    inicio = fio.index("BEGIN:VCALENDAR")
    fim = fio.index("END:VCALENDAR", inicio) + len("END:VCALENDAR")
    return fio[inicio:fim]


def _valor(ics: str, chave: str) -> str:
    for linha in _linhas(ics):
        nome = linha.split(":", 1)[0].split(";", 1)[0]
        if nome == chave:
            return linha.split(":", 1)[1]
    raise AssertionError(f"{chave} não está no arquivo")


def _marcar(cenario, **kwargs) -> uuid.UUID:
    with tenant_session(cenario["tenant_id"]) as session:
        reuniao = Meeting(
            tenant_id=cenario["tenant_id"],
            prospect_id=cenario["prospect_id"],
            scheduled_at=kwargs.pop("scheduled_at", REUNIAO),
            duration_minutes=kwargs.pop("duration_minutes", 30),
            **kwargs,
        )
        session.add(reuniao)
        session.flush()
        return reuniao.id


# ------------------------------------------------------------------ o arquivo


def test_o_arquivo_tem_o_que_faz_o_botao_de_resposta_aparecer():
    """`METHOD:REQUEST` e um `ATTENDEE` com `RSVP=TRUE`.

    Com `PUBLISH` o cliente oferece "adicionar ao calendário" e nenhuma resposta
    volta — e é a resposta que diz se a reunião existe de verdade.
    """
    ics = calendario.convite(
        meeting_id=uuid.uuid4(),
        inicio=REUNIAO,
        duracao_minutos=45,
        organizador_email="vendas@apymine.com",
        organizador_nome="Vendas Apy Mine",
        convidado_email="alice@northernore.ca",
        convidado_nome="Alice",
        assunto="Conversa sobre controle de turno",
    )
    linhas = _linhas(ics)
    assert linhas[0] == "BEGIN:VCALENDAR"
    assert linhas[-1] == "END:VCALENDAR"
    assert "METHOD:REQUEST" in linhas
    assert _valor(ics, "DTSTART") == "20261001T140000Z"
    assert _valor(ics, "DTEND") == "20261001T144500Z"
    atendee = next(linha for linha in linhas if linha.startswith("ATTENDEE"))
    assert "RSVP=TRUE" in atendee and "mailto:alice@northernore.ca" in atendee
    assert "mailto:vendas@apymine.com" in next(
        linha for linha in linhas if linha.startswith("ORGANIZER")
    )


def test_virgula_no_endereco_nao_quebra_o_evento():
    """Vírgula e ponto e vírgula separam campos na gramática do iCalendar.

    Sem escapar, "Av. Paulista, 1000" transforma o LOCATION em dois valores e o
    cliente descarta o arquivo — calado, que é o pior modo de falhar.
    """
    ics = calendario.convite(
        meeting_id=uuid.uuid4(),
        inicio=REUNIAO,
        duracao_minutos=30,
        organizador_email="vendas@apymine.com",
        organizador_nome="Apy Mine, Comercial",
        convidado_email="alice@northernore.ca",
        convidado_nome=None,
        assunto="Reunião; sem título",
        descricao="Primeira linha\nsegunda linha",
        local="Av. Paulista, 1000, sala 12",
    )
    assert _valor(ics, "LOCATION") == "Av. Paulista\\, 1000\\, sala 12"
    assert _valor(ics, "SUMMARY") == "Reunião\; sem título"
    assert _valor(ics, "DESCRIPTION") == "Primeira linha\\nsegunda linha"


def test_toda_linha_cabe_em_setenta_e_cinco_octetos():
    """O RFC dobra em octetos, não em caracteres.

    Dobrar por caractere parte um acento no meio — dois bytes em UTF-8 — e o
    arquivo chega corrompido justamente para nome de empresa com cedilha.
    """
    ics = calendario.convite(
        meeting_id=uuid.uuid4(),
        inicio=REUNIAO,
        duracao_minutos=30,
        organizador_email="comercial@construcoes-e-manutencoes-industriais.com.br",
        organizador_nome="Construções e Manutenções Industriais Ltda — Comercial",
        convidado_email="maria.aparecida.goncalves@mineradora-do-vale-do-aco.com.br",
        convidado_nome="Maria Aparecida Gonçalves",
        assunto="Conversa sobre automação de controle de turno nas três unidades",
    )
    for linha in ics.split("\r\n"):
        assert len(linha.encode("utf-8")) <= 75, linha
    # E desdobrado, o conteúdo volta inteiro.
    assert "Maria Aparecida Gonçalves" in _valor(ics, "ATTENDEE") or "Maria" in " ".join(
        _linhas(ics)
    )


def test_o_uid_e_estavel_por_reuniao():
    """Reenviar não pode criar um segundo compromisso na agenda de quem recebeu."""
    reuniao = uuid.uuid4()
    argumentos = dict(
        inicio=REUNIAO,
        duracao_minutos=30,
        organizador_email="vendas@apymine.com",
        organizador_nome=None,
        convidado_email="alice@northernore.ca",
        convidado_nome=None,
        assunto="Conversa",
    )
    primeiro = calendario.convite(meeting_id=reuniao, sequencia=0, **argumentos)
    segundo = calendario.convite(meeting_id=reuniao, sequencia=1, **argumentos)
    assert _valor(primeiro, "UID") == _valor(segundo, "UID")
    assert _valor(primeiro, "SEQUENCE") == "0"
    assert _valor(segundo, "SEQUENCE") == "1"


# ------------------------------------------------------------------ o envio


def test_o_convite_sai_pela_conta_da_empresa_com_o_ics_anexado(pronto_para_enviar, enviados):
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario, location="Google Meet")

    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )

    assert len(enviados) == 1
    email = enviados[0]
    assert email["To"] == "alice@northernore.ca"
    assert "vendas@apymine.com" in email["From"]

    partes = list(email.walk())
    calendarios = [p for p in partes if p.get_content_type() == "text/calendar"]
    # Duas cópias: a alternativa (que faz aparecer o botão de resposta) e o anexo
    # (que salva quem usa cliente que ignora a alternativa).
    assert len(calendarios) == 2, [p.get_content_type() for p in partes]
    assert any(p.get_param("method") == "REQUEST" for p in calendarios)
    corpo_ics = calendarios[0].get_payload(decode=True).decode()
    assert "METHOD:REQUEST" in corpo_ics
    assert "alice@northernore.ca" in corpo_ics


def test_o_estado_da_reuniao_diz_se_o_outro_lado_sabe(pronto_para_enviar, enviados):
    """`invite_sent_at` nulo é "marcado aqui e o lead não sabe"."""
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario)

    with tenant_session(cenario["tenant_id"]) as session:
        antes = session.get(Meeting, reuniao_id)
        assert antes.invite_sent_at is None
        assert antes.invite_sequence == 0

    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )

    with tenant_session(cenario["tenant_id"]) as session:
        depois = session.get(Meeting, reuniao_id)
        assert depois.invite_sent_at is not None
        assert depois.invite_sequence == 1


def test_reenviar_sobe_a_sequencia(pronto_para_enviar, enviados):
    """Sequência parada faz o calendário do outro lado ignorar o arquivo.

    O lead receberia o email, clicaria, e a agenda dele não mudaria — porque o
    cliente já conhece aquele UID e a sequência não diz "isto é mais novo".
    """
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario)

    for _ in range(2):
        with tenant_session(cenario["tenant_id"]) as session:
            email_sender.send_calendar_invite(
                session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
            )

    assert len(enviados) == 2
    assert [_valor(_ics_no_fio(e), "SEQUENCE") for e in enviados] == ["0", "1"]


def test_no_fio_o_arquivo_vai_com_crlf(pronto_para_enviar, enviados):
    """O RFC 5545 exige CRLF entre linhas, e cliente de calendário é literal.

    Arquivo com LF é descartado por parte dos clientes **em silêncio** — o
    sintoma é "o convite não apareceu", sem erro em lugar nenhum. O que importa é
    a forma que sai pelo SMTP, não a que o objeto guarda em memória.
    """
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario)
    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )

    no_fio = _ics_no_fio(enviados[0])
    assert "BEGIN:VCALENDAR\r\n" in no_fio
    assert "\r\nEND:VEVENT\r\n" in no_fio
    # Nenhum LF solto: todo LF vem depois de um CR.
    assert all(no_fio[i - 1] == "\r" for i, ch in enumerate(no_fio) if ch == "\n" and i > 0)


def test_quem_pediu_para_nao_receber_nao_recebe_nem_convite(pronto_para_enviar, enviados):
    """Descadastro não tem exceção transacional.

    Marcar reunião com quem se descadastrou já é contradição; mandar email para
    essa pessoa é a parte com consequência legal.
    """
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario)
    with tenant_session(cenario["tenant_id"]) as session:
        contato = session.get(Contact, cenario["contact_id"])
        contato.opted_out = True

    with tenant_session(cenario["tenant_id"]) as session, pytest.raises(SendBlocked):
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )
    assert enviados == []


def test_o_teto_diario_nao_segura_o_convite(pronto_para_enviar, enviados, monkeypatch):
    """O teto protege reputação de volume frio; convite é resposta a um acordo.

    Segurar o convite por cota deixaria o lead sem o compromisso no calendário
    por causa de um limite que existe para outra coisa — e possivelmente para uma
    reunião que é amanhã de manhã.
    """
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario)
    monkeypatch.setattr(
        email_sender,
        "allowance_today",
        lambda *a, **k: {"limit": 10, "used": 10, "remaining": 0, "reason": "teto atingido"},
    )

    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )
    assert len(enviados) == 1


def test_fora_do_horario_o_convite_ainda_sai(pronto_para_enviar, enviados):
    """Quem marca reunião às 21h precisa que o convite chegue."""
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario)
    madrugada = datetime(2026, 9, 22, 6, 0, tzinfo=UTC)  # 3h em São Paulo

    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=madrugada
        )
    assert len(enviados) == 1


# ------------------------------------------------------------------ pela API


def test_marcar_reuniao_pode_mandar_o_convite(client, auth_headers, pronto_para_enviar, enviados):
    cenario = pronto_para_enviar
    headers = auth_headers(cenario["email"], cenario["password"])
    r = client.post(
        f"/api/v1/prospects/{cenario['prospect_id']}/meetings",
        headers=headers,
        json={
            "scheduled_at": REUNIAO.isoformat(),
            "duration_minutes": 30,
            "send_invite": True,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["invite_sent_at"] is not None
    assert r.json()["invite_error"] is None
    assert len(enviados) == 1


def test_sem_convite_por_padrao(client, auth_headers, pronto_para_enviar, enviados):
    """Quem registra reunião combinada por telefone não quer disparar convite."""
    cenario = pronto_para_enviar
    headers = auth_headers(cenario["email"], cenario["password"])
    r = client.post(
        f"/api/v1/prospects/{cenario['prospect_id']}/meetings",
        headers=headers,
        json={"scheduled_at": REUNIAO.isoformat()},
    )
    assert r.status_code == 201, r.text
    assert r.json()["invite_sent_at"] is None
    assert enviados == []


def test_a_reuniao_fica_mesmo_que_o_convite_falhe(client, auth_headers, make_tenant):
    """Conta de email não configurada não pode apagar a conversão registrada.

    É a reunião que esta plataforma existe para produzir; o convite é o
    acessório. Trocar um pelo outro seria perder o que importa.
    """
    from app.db.models.sales import Campaign, Company, Contact, Prospect

    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    with tenant_session(t["tenant_id"]) as session:
        campanha = Campaign(tenant_id=t["tenant_id"], name="Sem email", slug="se")
        empresa = Company(tenant_id=t["tenant_id"], name="Alvo")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"], company_id=empresa.id, full_name="Bob", email="bob@alvo.com"
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
        prospect_id = prospect.id

    r = client.post(
        f"/api/v1/prospects/{prospect_id}/meetings",
        headers=headers,
        json={"scheduled_at": REUNIAO.isoformat(), "send_invite": True},
    )
    assert r.status_code == 201, r.text
    assert r.json()["invite_error"], "a tela precisa saber que o convite não saiu"
    assert r.json()["invite_sent_at"] is None

    # E a reunião está lá, com o funil movido.
    with tenant_session(t["tenant_id"]) as session:
        assert session.execute(select(Meeting)).scalars().one() is not None


def test_reenviar_pela_api(client, auth_headers, pronto_para_enviar, enviados):
    cenario = pronto_para_enviar
    headers = auth_headers(cenario["email"], cenario["password"])
    criada = client.post(
        f"/api/v1/prospects/{cenario['prospect_id']}/meetings",
        headers=headers,
        json={"scheduled_at": REUNIAO.isoformat()},
    )
    reuniao_id = criada.json()["id"]

    r = client.post(
        f"/api/v1/prospects/{cenario['prospect_id']}/meetings/{reuniao_id}/invite",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["invite_sent_at"] is not None
    assert len(enviados) == 1


def test_reuniao_de_outro_prospect_nao_e_reenviavel(
    client, auth_headers, pronto_para_enviar, enviados
):
    """O id vem da URL: sem conferir o dono, seria convite para o alvo errado."""
    cenario = pronto_para_enviar
    headers = auth_headers(cenario["email"], cenario["password"])
    reuniao_id = _marcar(cenario)
    r = client.post(
        f"/api/v1/prospects/{uuid.uuid4()}/meetings/{reuniao_id}/invite", headers=headers
    )
    assert r.status_code == 404
    assert enviados == []


def test_o_convite_fica_na_auditoria(client, auth_headers, pronto_para_enviar, enviados):
    cenario = pronto_para_enviar
    headers = auth_headers(cenario["email"], cenario["password"])
    client.post(
        f"/api/v1/prospects/{cenario['prospect_id']}/meetings",
        headers=headers,
        json={"scheduled_at": REUNIAO.isoformat(), "send_invite": True},
    )
    acoes = [e["action"] for e in client.get("/api/v1/tenants/me/audit", headers=headers).json()]
    assert "meeting.invite_sent" in acoes


def test_o_horario_do_convite_sai_no_fuso_da_empresa(pronto_para_enviar, enviados):
    """O corpo é lido por gente: 14h UTC em São Paulo é 11h, e é isso que vai escrito."""
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario, scheduled_at=REUNIAO)

    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )

    texto = enviados[0].get_body(preferencelist=("plain",)).get_content()
    assert "11:00" in texto, texto
    # E o arquivo continua em UTC, que é o que o calendário do outro lado espera.
    assert "DTSTART:20261001T140000Z" in _ics_no_fio(enviados[0])


def test_duracao_longa_fecha_o_fim_certo(pronto_para_enviar, enviados):
    cenario = pronto_para_enviar
    reuniao_id = _marcar(cenario, duration_minutes=90)
    with tenant_session(cenario["tenant_id"]) as session:
        email_sender.send_calendar_invite(
            session, tenant_id=cenario["tenant_id"], meeting_id=reuniao_id, agora=HORA_BOA
        )
    assert _valor(_ics_no_fio(enviados[0]), "DTEND") == _instante_esperado()


def _instante_esperado() -> str:
    return (REUNIAO + timedelta(minutes=90)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
