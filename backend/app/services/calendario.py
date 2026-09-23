"""Convite de calendário em iCalendar (RFC 5545), sem OAuth de ninguém.

A alternativa óbvia era integrar Google Calendar ou Microsoft Graph. Num produto
multiempresa isso custa caro antes de entregar nada: aplicativo OAuth verificado,
tela de consentimento por empresa, escopo de calendário aprovado, e um cliente
que não quer autorizar fica sem o recurso.

O `.ics` faz o trabalho pelo caminho que já existe. O email sai pela conta da
própria empresa — a mesma que manda a prospecção — com um `VEVENT` anexado, e
Gmail, Outlook e Apple Mail mostram "Aceitar / Recusar" nativamente, porque isso
é padrão desde antes de qualquer API dessas. A resposta volta como um email
`METHOD:REPLY`, que é email comum entrando na caixa que o worker já lê.

O que o `.ics` **não** dá, e vale dizer em voz alta: ler a agenda do vendedor.
Oferecer só horários de fato livres exige acesso de leitura ao calendário, e isso
sim precisa de OAuth. Marcar reunião continua sendo trabalho de quem opera.

Formatação: o RFC exige CRLF entre linhas e dobra em 75 octetos. Não é
preciosismo — cliente de calendário rejeita arquivo malformado em silêncio, e o
sintoma é "o convite não aparece" sem nada no log de ninguém.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

LIMITE_DA_LINHA = 75


def _texto(valor: str) -> str:
    """Escapa um valor TEXT do iCalendar (RFC 5545, seção 3.3.11).

    Vírgula e ponto e vírgula separam campos na gramática, e barra invertida
    escapa. Um endereço com vírgula — "Av. Paulista, 1000" — quebraria o evento
    inteiro sem isto, e o cliente descartaria o arquivo calado.
    """
    return (
        valor.replace("\\", "\\\\")
        .replace(";", "\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _dobrar(linha: str) -> list[str]:
    """Dobra em 75 octetos, contando bytes e não caracteres.

    Dobrar por caractere partiria um acento no meio — dois bytes em UTF-8 — e o
    arquivo chegaria corrompido em nome de empresa com cedilha.
    """
    bruto = linha.encode("utf-8")
    if len(bruto) <= LIMITE_DA_LINHA:
        return [linha]

    partes: list[str] = []
    atual = bytearray()
    limite = LIMITE_DA_LINHA
    for caractere in linha:
        octetos = caractere.encode("utf-8")
        if len(atual) + len(octetos) > limite:
            partes.append(atual.decode("utf-8"))
            atual = bytearray()
            # A continuação começa com um espaço, que conta no limite.
            limite = LIMITE_DA_LINHA - 1
        atual += octetos
    if atual:
        partes.append(atual.decode("utf-8"))
    return [partes[0]] + [f" {p}" for p in partes[1:]]


def _instante(valor: datetime) -> str:
    """Sempre em UTC com Z no fim: fuso do cliente é problema do cliente."""
    return valor.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def uid_da_reuniao(meeting_id: uuid.UUID, dominio: str) -> str:
    """Identidade estável do evento.

    Precisa ser o mesmo em todo convite da mesma reunião: é o UID que faz um
    reenvio virar "atualização" em vez de um segundo compromisso no calendário de
    quem recebeu.
    """
    return f"reuniao-{meeting_id}@{dominio}"


def convite(
    *,
    meeting_id: uuid.UUID,
    inicio: datetime,
    duracao_minutos: int,
    organizador_email: str,
    organizador_nome: str | None,
    convidado_email: str,
    convidado_nome: str | None,
    assunto: str,
    descricao: str = "",
    local: str | None = None,
    sequencia: int = 0,
    agora: datetime | None = None,
) -> str:
    """O `VEVENT` completo, pronto para ir como `text/calendar`.

    `METHOD:REQUEST` é o que transforma o anexo em convite: com `PUBLISH` o
    cliente mostra "adicionar ao calendário", sem botão de resposta — e é a
    resposta que interessa, porque é dela que se sabe se a reunião existe.
    """
    dominio = organizador_email.split("@")[-1]
    fim = inicio + timedelta(minutes=duracao_minutos)
    organizador = (
        f'ORGANIZER;CN="{_texto(organizador_nome)}":mailto:{organizador_email}'
        if organizador_nome
        else f"ORGANIZER:mailto:{organizador_email}"
    )
    convidado = (
        'ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE'
        + (f';CN="{_texto(convidado_nome)}"' if convidado_nome else "")
        + f":mailto:{convidado_email}"
    )

    linhas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Sales Workforce//Convite de reuniao//PT",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid_da_reuniao(meeting_id, dominio)}",
        f"DTSTAMP:{_instante(agora or datetime.now(UTC))}",
        f"DTSTART:{_instante(inicio)}",
        f"DTEND:{_instante(fim)}",
        f"SEQUENCE:{sequencia}",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        f"SUMMARY:{_texto(assunto)}",
        organizador,
        convidado,
    ]
    if descricao:
        linhas.append(f"DESCRIPTION:{_texto(descricao)}")
    if local:
        linhas.append(f"LOCATION:{_texto(local)}")
    linhas += [
        # Um aviso quinze minutos antes. Reunião marcada por email costuma ser a
        # primeira conversa: chegar atrasado nela é caro.
        "BEGIN:VALARM",
        "TRIGGER:-PT15M",
        "ACTION:DISPLAY",
        "DESCRIPTION:Lembrete",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    dobradas: list[str] = []
    for linha in linhas:
        dobradas.extend(_dobrar(linha))
    # CRLF, como o RFC pede, e uma linha final vazia.
    return "\r\n".join(dobradas) + "\r\n"


__all__ = ["convite", "uid_da_reuniao"]
