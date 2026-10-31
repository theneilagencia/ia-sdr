"""Cadência multi-passo: o follow-up que acontece sem ninguém lembrar.

A maior parte das respostas em prospecção fria vem do segundo ou do terceiro
contato, não do primeiro. Sem cadência, a plataforma manda uma mensagem por
lead e espera — que é a forma mais cara de não vender.

**O que "automático" significa aqui, e o que não significa.** O passo vence, o
Outreach Agent escreve — e o texto entra na fila de revisão humana, como todo
o resto. Nada sai sem alguém ler. O que a cadência automatiza é *lembrar* e
*escrever*, não *enviar*.

**As regras de parada são mais importantes que a cadência.** Continuar mandando
follow-up para quem já respondeu, já marcou reunião ou pediu descadastro é
pior do que não ter cadência nenhuma: queima o domínio, irrita o lead e faz a
empresa parecer um robô — que é exatamente o que ela está tentando não
parecer. Por isso a parada é verificada no momento de gerar cada passo, e não
uma vez na entrada.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError
from app.db.models.engagement import (
    Conversation,
    EnrollmentStatus,
    Meeting,
    Message,
    MessageDirection,
    Qualification,
    Sequence,
    SequenceEnrollment,
)
from app.db.models.jobs import JobKind
from app.db.models.sales import Campaign, CampaignStatus, Contact, Prospect, ProspectStatus
from app.services import jobs

#: Teto de passos. Uma cadência de vinte toques não é persistência, é spam —
#: e o domínio de quem manda é o que paga a conta.
MAX_PASSOS = 8
#: Intervalo mínimo entre toques. Dois emails no mesmo dia, do mesmo remetente,
#: para o mesmo lead, é a definição operacional de "ser ignorado".
MIN_DIAS_ENTRE_PASSOS = 1
MAX_DIAS_ENTRE_PASSOS = 90

#: Estágios em que o prospect saiu do funil de abordagem, para frente ou para
#: trás. Em qualquer um deles a cadência não tem mais o que dizer.
ESTAGIOS_TERMINAIS = {
    ProspectStatus.QUALIFIED.value,
    ProspectStatus.MEETING_BOOKED.value,
    ProspectStatus.DISQUALIFIED.value,
    ProspectStatus.BOUNCED.value,
}


class PassosInvalidos(AppError):
    code = "invalid_sequence_steps"


def validar_passos(passos: list[dict]) -> list[dict]:
    """Normaliza e recusa cadência que faria estrago.

    O primeiro passo tem espera zero por definição: é a abordagem inicial, e
    agendar o primeiro contato para daqui a três dias só confundiria quem
    montou a cadência.
    """
    if not passos:
        raise PassosInvalidos("Uma sequência precisa de pelo menos um passo")
    if len(passos) > MAX_PASSOS:
        raise PassosInvalidos(
            f"São {len(passos)} passos; o máximo é {MAX_PASSOS}. Uma cadência mais "
            "longa que isso não é persistência, é spam."
        )

    normalizados = []
    for indice, passo in enumerate(passos):
        instrucao = str(passo.get("instruction") or "").strip()
        if not instrucao:
            raise PassosInvalidos(
                f"O passo {indice + 1} não diz o que escrever. Sem instrução, os toques "
                "saem todos iguais — e o segundo email igual ao primeiro é pior que nenhum."
            )
        if indice == 0:
            espera = 0
        else:
            espera = int(passo.get("wait_days", MIN_DIAS_ENTRE_PASSOS))
            if espera < MIN_DIAS_ENTRE_PASSOS:
                raise PassosInvalidos(
                    f"O passo {indice + 1} esperaria {espera} dia(s). O mínimo é "
                    f"{MIN_DIAS_ENTRE_PASSOS}: dois emails no mesmo dia, do mesmo "
                    "remetente, para o mesmo lead, é a definição de ser ignorado."
                )
            if espera > MAX_DIAS_ENTRE_PASSOS:
                raise PassosInvalidos(
                    f"O passo {indice + 1} esperaria {espera} dias, acima de "
                    f"{MAX_DIAS_ENTRE_PASSOS}."
                )
        normalizados.append(
            {"order": indice + 1, "wait_days": espera, "instruction": instrucao[:2000]}
        )
    return normalizados


# ------------------------------------------------------------------- inscrição
def enroll(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    sequence: Sequence,
    prospect_ids: list[uuid.UUID],
    agora: datetime | None = None,
) -> dict:
    """Coloca prospects na cadência. Devolve o que entrou e o que não entrou.

    Não falha por prospect já inscrito: quem seleciona cem leads na tela e
    aperta "inscrever" não deveria perder a operação inteira porque três deles
    já estavam na cadência.
    """
    agora = agora or datetime.now(UTC)
    if not sequence.is_active:
        raise ConflictError("Esta sequência está desativada")

    inscritos, ignorados = [], []
    for prospect_id in prospect_ids:
        prospect = session.get(Prospect, prospect_id)
        if prospect is None:
            ignorados.append({"prospect_id": str(prospect_id), "reason": "não encontrado"})
            continue
        if prospect.campaign_id != sequence.campaign_id:
            ignorados.append({"prospect_id": str(prospect_id), "reason": "é de outra campanha"})
            continue

        ativa = session.execute(
            select(SequenceEnrollment)
            .where(SequenceEnrollment.prospect_id == prospect_id)
            .where(SequenceEnrollment.status == EnrollmentStatus.ACTIVE.value)
        ).scalar_one_or_none()
        if ativa is not None:
            ignorados.append({"prospect_id": str(prospect_id), "reason": "já está numa cadência"})
            continue

        motivo = motivo_de_parada(session, prospect)
        if motivo is not None:
            ignorados.append({"prospect_id": str(prospect_id), "reason": motivo})
            continue

        inscricao = SequenceEnrollment(
            tenant_id=tenant_id,
            sequence_id=sequence.id,
            prospect_id=prospect_id,
            status=EnrollmentStatus.ACTIVE.value,
            current_step=0,
            next_run_at=agora,
        )
        session.add(inscricao)
        session.flush()
        inscritos.append(inscricao)

    return {"enrolled": inscritos, "skipped": ignorados}


# -------------------------------------------------------------- regras de parada
def motivo_de_parada(session: Session, prospect: Prospect) -> str | None:
    """Por que esta cadência não deve mandar o próximo toque — ou `None`.

    Verificado a cada passo, e não uma vez na entrada: entre um toque e o
    seguinte podem passar dias, e é exatamente nesse intervalo que o lead
    responde, marca reunião ou pede para parar.
    """
    if prospect.status in ESTAGIOS_TERMINAIS:
        return f"prospect em {prospect.status}"

    contato = session.get(Contact, prospect.contact_id) if prospect.contact_id else None
    if contato is None:
        return "prospect sem contato"
    if contato.opted_out:
        return "contato pediu descadastro"

    respondeu = session.execute(
        select(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.prospect_id == prospect.id)
        .where(Message.direction == MessageDirection.INBOUND.value)
    ).scalar_one()
    if respondeu:
        return "lead respondeu"

    reuniao = session.execute(
        select(func.count(Meeting.id)).where(Meeting.prospect_id == prospect.id)
    ).scalar_one()
    if reuniao:
        return "reunião marcada"

    qualificado = session.execute(
        select(func.count(Qualification.id)).where(Qualification.prospect_id == prospect.id)
    ).scalar_one()
    if qualificado:
        return "prospect já qualificado"

    return None


def parar(session: Session, inscricao: SequenceEnrollment, motivo: str) -> SequenceEnrollment:
    inscricao.status = EnrollmentStatus.STOPPED.value
    inscricao.stop_reason = motivo
    inscricao.next_run_at = None
    session.flush()
    return inscricao


# ------------------------------------------------------------------------ tick
def tick(session: Session, tenant_id: uuid.UUID, *, agora: datetime | None = None) -> dict:
    """Avança as cadências cuja hora chegou. É o que o worker chama.

    Cada passo vencido vira um job do Outreach Agent com a instrução daquele
    toque; o texto gerado entra na fila de revisão como qualquer outro.
    """
    agora = agora or datetime.now(UTC)
    vencidas = list(
        session.execute(
            select(SequenceEnrollment)
            .where(SequenceEnrollment.status == EnrollmentStatus.ACTIVE.value)
            .where(SequenceEnrollment.next_run_at.isnot(None))
            .where(SequenceEnrollment.next_run_at <= agora)
            .order_by(SequenceEnrollment.next_run_at)
        ).scalars()
    )

    resultado = {"gerados": 0, "parados": 0, "concluidos": 0, "adiados": 0}
    for inscricao in vencidas:
        sequencia = session.get(Sequence, inscricao.sequence_id)
        prospect = session.get(Prospect, inscricao.prospect_id)
        if sequencia is None or prospect is None:
            parar(session, inscricao, "cadência ou prospect removido")
            resultado["parados"] += 1
            continue

        motivo = motivo_de_parada(session, prospect)
        if motivo is not None:
            parar(session, inscricao, motivo)
            resultado["parados"] += 1
            continue

        # Campanha pausada e sequência desativada **adiam**, não param: quem
        # pausou pretende retomar, e parar aqui exigiria reinscrever todo
        # mundo à mão depois.
        campanha = session.get(Campaign, sequencia.campaign_id)
        if not sequencia.is_active or (
            campanha is not None and campanha.status == CampaignStatus.PAUSED.value
        ):
            inscricao.next_run_at = agora + timedelta(days=1)
            session.flush()
            resultado["adiados"] += 1
            continue

        passos = sequencia.steps or []
        if inscricao.current_step >= len(passos):
            inscricao.status = EnrollmentStatus.COMPLETED.value
            inscricao.next_run_at = None
            session.flush()
            resultado["concluidos"] += 1
            continue

        passo = passos[inscricao.current_step]
        jobs.enqueue(
            session,
            tenant_id=tenant_id,
            kind=JobKind.AGENT_RUN,
            payload={
                "tenant_id": str(tenant_id),
                "agent": "outreach",
                "campaign_id": str(prospect.campaign_id) if prospect.campaign_id else None,
                "entity_type": "prospect",
                "entity_id": str(prospect.id),
                "params": {
                    "sequence_step": {
                        "order": passo.get("order", inscricao.current_step + 1),
                        "instruction": passo.get("instruction", ""),
                        "total_steps": len(passos),
                    }
                },
            },
            # Um passo por inscrição: se o tick rodar duas vezes antes de o
            # worker consumir, o segundo não duplica o email.
            dedupe_key=f"sequence:{inscricao.id}:step:{inscricao.current_step + 1}",
        )

        inscricao.current_step += 1
        inscricao.last_step_at = agora
        if inscricao.current_step >= len(passos):
            inscricao.status = EnrollmentStatus.COMPLETED.value
            inscricao.next_run_at = None
        else:
            espera = int(passos[inscricao.current_step].get("wait_days", MIN_DIAS_ENTRE_PASSOS))
            inscricao.next_run_at = agora + timedelta(days=espera)
        session.flush()
        resultado["gerados"] += 1

    return resultado
