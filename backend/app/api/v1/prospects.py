"""Prospects: quem entra na campanha e onde cada um está no funil."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import AppError, LimitExceeded, NotFound
from app.db.models.engagement import (
    Conversation,
    Meeting,
    Message,
    MessageDirection,
    MessageStatus,
    Qualification,
)
from app.db.models.sales import Campaign, Company, Contact, Prospect, ProspectStatus, Score
from app.rbac.roles import Permission
from app.services import audit, csv_import, email_sender, ravi
from app.services.csv_import import CsvInvalido
from app.services.usage import UsageKind, count_this_month, effective_limits, record_usage
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/prospects", tags=["prospects"])

#: Teto do upload, checado antes de ler o arquivo na memória. O limite de
#: linhas está em `services/csv_import`.
MAX_CSV_BYTES = 5 * 1024 * 1024

UNLIMITED = -1

#: Saídas do funil. Elas continuam contando nos estágios que a pessoa **já
#: atravessou**: o email voltou porque foi enviado, e o descadastro só existe
#: porque alguém recebeu a mensagem e clicou no link dela. Sem isso a contagem
#: de contatados *encolhia* conforme os retornos chegavam — o oposto do que o
#: funil cumulativo promete — e a taxa de retorno, que é o número que queima o
#: domínio de quem envia, não aparecia em lugar nenhum.
#:
#: `disqualified` é escrito por três caminhos: descadastro pelo link público,
#: veredito do agente de qualificação e encerramento pelo agente de conversa.
#: Todos exigem que a abordagem tenha saído.
_SAIDAS = {"bounced", "disqualified"}

#: Estágios já alcançados contam para trás no funil: quem foi qualificado
#: também foi contatado. Sem isso, o funil só mostraria o estágio atual e
#: pareceria que os números somem conforme as pessoas avançam.
_REACHED = {
    "researched": {"researched", "scored", "contacted", "engaged", "qualified", "meeting_booked"}
    | _SAIDAS,
    "scored": {"scored", "contacted", "engaged", "qualified", "meeting_booked"} | _SAIDAS,
    "contacted": {"contacted", "engaged", "qualified", "meeting_booked"} | _SAIDAS,
    # Engajado é quem respondeu. Email que voltou não respondeu, e quem clicou
    # em "não quero mais" também não — então as saídas não entram aqui.
    "engaged": {"engaged", "qualified", "meeting_booked"},
    "qualified": {"qualified", "meeting_booked"},
}


def _check_import_budget(session: Session, tenant_id: uuid.UUID, quantidade: int) -> None:
    limite = effective_limits(session, tenant_id)["prospects_per_month"]
    if limite == UNLIMITED:
        return
    usados = count_this_month(session, tenant_id, UsageKind.PROSPECT_IMPORTED)
    if usados + quantidade > limite:
        raise LimitExceeded(
            "Cota mensal de prospects esgotada para este tenant",
            details={"limit": limite, "used": usados, "requested": quantidade},
        )


@router.post(
    "/import/csv", response_model=schemas.ProspectCsvResult, status_code=status.HTTP_201_CREATED
)
async def import_prospects_csv(
    campaign_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    source: str = Form(default="csv"),
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
):
    """A mesma entrada em lote, a partir do arquivo que a pessoa já tem.

    Quem monta lista exporta do Sales Navigator, do Apollo ou de uma planilha,
    e cada um chama as colunas de um jeito. O mapeamento aceita os apelidos
    conhecidos em português e inglês; o que não bate é ignorado, e linha
    inválida volta com o número dela para a pessoa abrir o arquivo e corrigir.
    """
    bruto = await file.read()
    if len(bruto) > MAX_CSV_BYTES:
        raise CsvInvalido(
            f"O arquivo passa de {MAX_CSV_BYTES // (1024 * 1024)} MB. Divida em partes."
        )
    itens, erros = csv_import.ler(bruto)

    resultado = import_prospects(
        payload=schemas.ProspectImportRequest(campaign_id=campaign_id, items=itens, source=source),
        ctx=ctx,
        db=db,
    )
    return schemas.ProspectCsvResult(
        **resultado.model_dump(),
        rows_read=len(itens) + len(erros),
        row_errors=erros,
        missing_email=sum(1 for item in itens if item.email is None),
    )


@router.post(
    "/import", response_model=schemas.ProspectImportResult, status_code=status.HTTP_201_CREATED
)
def import_prospects(
    payload: schemas.ProspectImportRequest,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
):
    """Entrada em lote de contas e pessoas numa campanha.

    Reaproveita empresa por domínio e pessoa por email dentro do tenant: subir
    a mesma lista duas vezes não duplica a base nem consome cota de novo.
    """
    campaign = db.get(Campaign, payload.campaign_id)
    if campaign is None:
        raise NotFound("Campanha não encontrada")

    _check_import_budget(db, ctx.tenant_id, len(payload.items))

    criados: list[uuid.UUID] = []
    duplicados = 0

    for item in payload.items:
        company = None
        if item.company_domain:
            company = db.execute(
                select(Company).where(Company.domain == item.company_domain)
            ).scalar_one_or_none()
        if company is None:
            company = db.execute(
                select(Company).where(Company.name == item.company_name)
            ).scalar_one_or_none()
        if company is None:
            company = Company(
                tenant_id=ctx.tenant_id,
                name=item.company_name,
                domain=item.company_domain,
                industry=item.industry,
                country=item.country,
                employee_count=item.employee_count,
            )
            db.add(company)
            db.flush()

        contact = None
        if item.email:
            contact = db.execute(
                select(Contact).where(Contact.email == str(item.email))
            ).scalar_one_or_none()
        else:
            # Lista de LinkedIn vem sem email, e sem esta busca a mesma pessoa
            # entrava de novo a cada reimportação: a promessa de não duplicar
            # valia só para quem tinha endereço. Nome dentro da mesma conta é o
            # critério que uma pessoa usaria para dizer que é a mesma pessoa.
            contact = db.execute(
                select(Contact)
                .where(Contact.company_id == company.id)
                .where(Contact.email.is_(None))
                .where(func.lower(Contact.full_name) == item.full_name.strip().lower())
            ).scalar_one_or_none()
        if contact is None:
            contact = Contact(
                tenant_id=ctx.tenant_id,
                company_id=company.id,
                full_name=item.full_name,
                email=str(item.email) if item.email else None,
                title=item.title,
                persona=item.persona,
                linkedin_url=item.linkedin_url,
            )
            db.add(contact)
            db.flush()

        existente = db.execute(
            select(Prospect)
            .where(Prospect.campaign_id == campaign.id)
            .where(Prospect.contact_id == contact.id)
        ).scalar_one_or_none()
        if existente is not None:
            duplicados += 1
            continue

        prospect = Prospect(
            tenant_id=ctx.tenant_id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            company_id=company.id,
            status=ProspectStatus.NEW.value,
            source=payload.source,
        )
        db.add(prospect)
        db.flush()
        criados.append(prospect.id)

    if criados:
        record_usage(
            db,
            tenant_id=ctx.tenant_id,
            kind=UsageKind.PROSPECT_IMPORTED,
            quantity=len(criados),
            campaign_id=campaign.id,
            user_id=ctx.user_id,
        )
    audit.record(
        db,
        action="prospects.imported",
        resource_type="campaign",
        resource_id=campaign.id,
        payload={"imported": len(criados), "duplicates": duplicados, "source": payload.source},
        context=ctx,
    )
    return schemas.ProspectImportResult(
        imported=len(criados), duplicates=duplicados, prospect_ids=criados
    )


def _nomes(db: Session, prospects: list[Prospect]) -> dict[uuid.UUID, dict]:
    """Contato, empresa e campanha de cada prospect, em uma consulta.

    Resolver por linha seria uma consulta por prospect na tela — rápido com dez
    prospects, inutilizável com mil.
    """
    if not prospects:
        return {}
    linhas = db.execute(
        select(Prospect.id, Contact.full_name, Contact.email, Company.name, Campaign.name)
        .join(Contact, Prospect.contact_id == Contact.id, isouter=True)
        .join(Company, Prospect.company_id == Company.id, isouter=True)
        .join(Campaign, Prospect.campaign_id == Campaign.id, isouter=True)
        .where(Prospect.id.in_([p.id for p in prospects]))
    ).all()
    return {
        pid: {
            "contact_name": nome,
            "contact_email": email,
            "company_name": empresa,
            "campaign_name": campanha,
        }
        for pid, nome, email, empresa, campanha in linhas
    }


@router.get("", response_model=list[schemas.ProspectListItem])
def list_prospects(
    campaign_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(Prospect).order_by(Prospect.created_at.desc()).limit(limit)
    if campaign_id:
        stmt = stmt.where(Prospect.campaign_id == campaign_id)
    if status_filter:
        stmt = stmt.where(Prospect.status == status_filter)
    prospects = list(db.execute(stmt).scalars())

    nomes = _nomes(db, prospects)
    return [
        schemas.ProspectListItem.model_validate(
            {**schemas.ProspectResponse.model_validate(p).model_dump(), **nomes.get(p.id, {})}
        )
        for p in prospects
    ]


@router.get("/{prospect_id}/scores", response_model=list[schemas.ScoreResponse])
def list_scores(
    prospect_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    if db.get(Prospect, prospect_id) is None:
        raise NotFound("Prospect não encontrado")
    return list(
        db.execute(
            select(Score).where(Score.prospect_id == prospect_id).order_by(Score.created_at.desc())
        ).scalars()
    )


@router.post(
    "/{prospect_id}/messages/inbound",
    response_model=schemas.MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_inbound(
    prospect_id: uuid.UUID,
    payload: schemas.InboundMessageCreate,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Registra a resposta do lead e move o prospect para engajado.

    Quem responde está engajado, independentemente do que o agente vai fazer
    com a mensagem depois.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise NotFound("Prospect não encontrado")

    conversa = db.execute(
        select(Conversation).where(Conversation.prospect_id == prospect_id).limit(1)
    ).scalar_one_or_none()
    if conversa is None:
        conversa = Conversation(
            tenant_id=ctx.tenant_id,
            prospect_id=prospect_id,
            campaign_id=prospect.campaign_id,
            channel="email",
            subject=payload.subject,
        )
        db.add(conversa)
        db.flush()

    mensagem = Message(
        tenant_id=ctx.tenant_id,
        conversation_id=conversa.id,
        direction=MessageDirection.INBOUND.value,
        status=MessageStatus.REPLIED.value,
        channel=conversa.channel,
        subject=payload.subject or conversa.subject,
        body=payload.body,
        external_message_id=payload.external_message_id,
    )
    db.add(mensagem)
    conversa.last_message_at = mensagem.created_at
    if prospect.status not in (
        ProspectStatus.QUALIFIED.value,
        ProspectStatus.MEETING_BOOKED.value,
        ProspectStatus.DISQUALIFIED.value,
    ):
        prospect.status = ProspectStatus.ENGAGED.value
    db.flush()

    audit.record(
        db,
        action="conversation.inbound_received",
        resource_type="conversation",
        resource_id=conversa.id,
        payload={"prospect_id": str(prospect_id)},
        context=ctx,
    )
    return mensagem


@router.get("/{prospect_id}/messages", response_model=list[schemas.MessageResponse])
def list_messages(
    prospect_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CONVERSATION_READ)),
    db: Session = Depends(get_db),
):
    """Rascunhos e mensagens deste prospect, mais novos primeiro."""
    if db.get(Prospect, prospect_id) is None:
        raise NotFound("Prospect não encontrado")
    return list(
        db.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.prospect_id == prospect_id)
            .order_by(Message.created_at.desc())
        ).scalars()
    )


@router.get("/{prospect_id}/qualifications", response_model=list[schemas.QualificationResponse])
def list_qualifications(
    prospect_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    if db.get(Prospect, prospect_id) is None:
        raise NotFound("Prospect não encontrado")
    return list(
        db.execute(
            select(Qualification)
            .where(Qualification.prospect_id == prospect_id)
            .order_by(Qualification.created_at.desc())
        ).scalars()
    )


@router.post(
    "/{prospect_id}/meetings",
    response_model=schemas.MeetingResponse,
    status_code=status.HTTP_201_CREATED,
)
def book_meeting(
    prospect_id: uuid.UUID,
    payload: schemas.MeetingCreate,
    ctx: TenantContext = Depends(require(Permission.MEETING_WRITE)),
    db: Session = Depends(get_db),
):
    """Agenda a reunião — a conversão que a plataforma existe para produzir.

    Hoje o horário vem de quem marca; a integração de calendário, no Sprint 4,
    entra por este mesmo caminho.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise NotFound("Prospect não encontrado")

    reuniao = Meeting(
        tenant_id=ctx.tenant_id,
        prospect_id=prospect_id,
        campaign_id=prospect.campaign_id,
        owner_user_id=payload.owner_user_id or ctx.user_id,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        location=payload.location,
        notes=payload.notes,
    )
    db.add(reuniao)
    prospect.status = ProspectStatus.MEETING_BOOKED.value
    db.flush()

    audit.record(
        db,
        action="meeting.booked",
        resource_type="meeting",
        resource_id=reuniao.id,
        payload={"prospect_id": str(prospect_id), "scheduled_at": payload.scheduled_at.isoformat()},
        context=ctx,
    )

    resposta = schemas.MeetingResponse.model_validate(reuniao)
    if payload.send_invite:
        # A reunião fica gravada mesmo que o convite não saia: conta de email não
        # configurada ou servidor fora não pode apagar o registro da conversão que
        # esta plataforma existe para produzir. O erro volta na resposta e a tela
        # oferece reenviar.
        try:
            email_sender.send_calendar_invite(db, tenant_id=ctx.tenant_id, meeting_id=reuniao.id)
        except AppError as exc:
            return resposta.model_copy(update={"invite_error": exc.message})
        db.flush()
        return schemas.MeetingResponse.model_validate(reuniao)
    return resposta


@router.post(
    "/{prospect_id}/meetings/{meeting_id}/invite",
    response_model=schemas.MeetingResponse,
)
def send_meeting_invite(
    prospect_id: uuid.UUID,
    meeting_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.MEETING_WRITE)),
    db: Session = Depends(get_db),
):
    """Manda (ou reenvia) o convite de calendário da reunião.

    Reenviar é caso de uso de verdade: o lead apagou o email, o endereço estava
    errado, ou a reunião foi remarcada. O `SEQUENCE` do iCalendar sobe a cada
    envio, então o calendário do outro lado trata o novo arquivo como atualização
    do mesmo compromisso em vez de ignorá-lo.
    """
    reuniao = db.get(Meeting, meeting_id)
    if reuniao is None or reuniao.prospect_id != prospect_id:
        raise NotFound("Reunião não encontrada")
    email_sender.send_calendar_invite(db, tenant_id=ctx.tenant_id, meeting_id=meeting_id)
    db.flush()
    return reuniao


@router.get("/{prospect_id}/meetings", response_model=list[schemas.MeetingResponse])
def list_meetings(
    prospect_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
):
    if db.get(Prospect, prospect_id) is None:
        raise NotFound("Prospect não encontrado")
    return list(
        db.execute(
            select(Meeting)
            .where(Meeting.prospect_id == prospect_id)
            .order_by(Meeting.scheduled_at.desc())
        ).scalars()
    )


@router.post("/{prospect_id}/sync-crm", response_model=schemas.CrmSyncResult)
def sync_prospect_to_crm(
    prospect_id: uuid.UUID,
    force: bool = Query(default=False),
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
):
    """Empurra este prospect para o RAVI agora.

    O worker faz isso sozinho a cada ciclo. O endpoint existe para não ser
    preciso esperar o relógio — e para ter onde olhar quando alguém pergunta por
    que o lead não apareceu no CRM: a resposta diz `skipped` com o motivo.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise NotFound("Prospect não encontrado nesta empresa")
    resultado = ravi.push_prospect(db, ctx.tenant_id, prospect, force=force)
    if resultado["status"] in ("created", "updated"):
        audit.record(
            db,
            action="crm.lead_pushed",
            resource_type="prospect",
            resource_id=prospect_id,
            payload={"lead_id": resultado.get("lead_id"), "status": resultado["status"]},
            context=ctx,
        )
    return schemas.CrmSyncResult(**resultado)


@router.get("/funnel", response_model=schemas.FunnelResponse)
def funnel(
    campaign_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    """Os números da tela inicial, para o tenant ou para uma campanha."""

    def _count(estagio: str | None = None) -> int:
        stmt = select(func.count(Prospect.id))
        if campaign_id:
            stmt = stmt.where(Prospect.campaign_id == campaign_id)
        if estagio:
            stmt = stmt.where(Prospect.status.in_(_REACHED[estagio]))
        return int(db.execute(stmt).scalar_one())

    reunioes = select(func.count(Meeting.id))
    if campaign_id:
        reunioes = reunioes.where(Meeting.campaign_id == campaign_id)

    bandas = select(Score.band, func.count(func.distinct(Score.prospect_id))).group_by(Score.band)
    if campaign_id:
        bandas = bandas.where(Score.campaign_id == campaign_id)

    def _agora_em(status: str) -> int:
        """Quantos estão **neste** estado agora — as saídas, não os estágios."""
        stmt = select(func.count(Prospect.id)).where(Prospect.status == status)
        if campaign_id:
            stmt = stmt.where(Prospect.campaign_id == campaign_id)
        return int(db.execute(stmt).scalar_one())

    return schemas.FunnelResponse(
        campaign_id=campaign_id,
        prospects=_count(),
        researched=_count("researched"),
        scored=_count("scored"),
        contacted=_count("contacted"),
        engaged=_count("engaged"),
        qualified=_count("qualified"),
        meetings=int(db.execute(reunioes).scalar_one()),
        bounced=_agora_em(ProspectStatus.BOUNCED.value),
        disqualified=_agora_em(ProspectStatus.DISQUALIFIED.value),
        by_band={b: int(n) for b, n in db.execute(bandas).all() if b},
    )


#: Declarada no fim de propósito: FastAPI casa rotas na ordem em que foram
#: declaradas, e `/{prospect_id}` antes de `/funnel` faria `GET
#: /prospects/funnel` responder 422 dizendo que "funnel" não é um UUID.
@router.get("/{prospect_id}", response_model=schemas.ProspectListItem)
def get_prospect(
    prospect_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    """Um prospect, com os nomes resolvidos.

    A tela de detalhe existe para a pessoa que assume quando o agente escala, e
    ela precisa saber para quem está olhando. Sem esta rota, a tela teria de
    baixar a lista inteira e procurar — que é o que uma tela faz quando falta
    uma rota, e é lento exatamente quando a base cresce.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise NotFound("Prospect não encontrado")
    nomes = _nomes(db, [prospect])
    return schemas.ProspectListItem.model_validate(
        {
            **schemas.ProspectResponse.model_validate(prospect).model_dump(),
            **nomes.get(prospect.id, {}),
        }
    )
