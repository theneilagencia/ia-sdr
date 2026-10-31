"""Cadências: o follow-up que acontece sem ninguém lembrar.

A tabela `sequences` existia desde a primeira migration e nada a usava. A maior
parte das respostas em prospecção fria vem do segundo ou do terceiro contato —
mandar uma mensagem por lead e esperar é a forma mais cara de não vender.

O que a cadência automatiza é *lembrar* e *escrever*. Enviar continua passando
pela fila de revisão humana, como todo o resto.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.db.models.engagement import EnrollmentStatus, Sequence, SequenceEnrollment
from app.db.models.sales import Campaign
from app.rbac.roles import Permission
from app.services import audit, sequences
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/sequences", tags=["sequences"])


def _ativas_por_sequencia(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    linhas = db.execute(
        select(SequenceEnrollment.sequence_id, func.count(SequenceEnrollment.id))
        .where(SequenceEnrollment.sequence_id.in_(ids))
        .where(SequenceEnrollment.status == EnrollmentStatus.ACTIVE.value)
        .group_by(SequenceEnrollment.sequence_id)
    ).all()
    return {sid: int(total) for sid, total in linhas}


def _resposta(sequencia: Sequence, ativas: int) -> schemas.SequenceResponse:
    return schemas.SequenceResponse(
        id=sequencia.id,
        campaign_id=sequencia.campaign_id,
        name=sequencia.name,
        steps=sequencia.steps or [],
        is_active=sequencia.is_active,
        created_at=sequencia.created_at,
        active_enrollments=ativas,
    )


def _buscar(db: Session, sequence_id: uuid.UUID) -> Sequence:
    sequencia = db.get(Sequence, sequence_id)
    if sequencia is None:
        raise NotFound("Sequência não encontrada nesta empresa")
    return sequencia


@router.get("", response_model=list[schemas.SequenceResponse])
def list_sequences(
    campaign_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.SequenceResponse]:
    stmt = select(Sequence)
    if campaign_id:
        stmt = stmt.where(Sequence.campaign_id == campaign_id)
    lista = list(db.execute(stmt.order_by(Sequence.created_at.desc())).scalars())
    ativas = _ativas_por_sequencia(db, [s.id for s in lista])
    return [_resposta(s, ativas.get(s.id, 0)) for s in lista]


@router.post("", response_model=schemas.SequenceResponse, status_code=status.HTTP_201_CREATED)
def create_sequence(
    payload: schemas.SequenceCreate,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.SequenceResponse:
    if db.get(Campaign, payload.campaign_id) is None:
        raise NotFound("Campanha não encontrada nesta empresa")

    passos = sequences.validar_passos([p.model_dump() for p in payload.steps])
    sequencia = Sequence(
        tenant_id=ctx.tenant_id,
        campaign_id=payload.campaign_id,
        name=payload.name,
        steps=passos,
        is_active=payload.is_active,
    )
    db.add(sequencia)
    db.flush()
    audit.record(
        db,
        action="sequence.created",
        resource_type="sequence",
        resource_id=sequencia.id,
        payload={"name": sequencia.name, "steps": len(passos)},
        context=ctx,
    )
    return _resposta(sequencia, 0)


@router.get("/{sequence_id}", response_model=schemas.SequenceResponse)
def get_sequence(
    sequence_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_READ)),
    db: Session = Depends(get_db),
) -> schemas.SequenceResponse:
    sequencia = _buscar(db, sequence_id)
    return _resposta(sequencia, _ativas_por_sequencia(db, [sequence_id]).get(sequence_id, 0))


@router.patch("/{sequence_id}", response_model=schemas.SequenceResponse)
def update_sequence(
    sequence_id: uuid.UUID,
    payload: schemas.SequenceUpdate,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.SequenceResponse:
    """Editar passos vale para quem ainda não chegou neles.

    Quem já recebeu o toque 2 não recebe o novo toque 2 — a inscrição guarda
    em que passo está, não o texto que já saiu.
    """
    sequencia = _buscar(db, sequence_id)
    if payload.name is not None:
        sequencia.name = payload.name
    if payload.steps is not None:
        sequencia.steps = sequences.validar_passos([p.model_dump() for p in payload.steps])
    if payload.is_active is not None:
        sequencia.is_active = payload.is_active
    db.flush()
    audit.record(
        db,
        action="sequence.updated",
        resource_type="sequence",
        resource_id=sequencia.id,
        payload={"is_active": sequencia.is_active, "steps": len(sequencia.steps or [])},
        context=ctx,
    )
    return _resposta(sequencia, _ativas_por_sequencia(db, [sequence_id]).get(sequence_id, 0))


@router.delete("/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sequence(
    sequence_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    """Apaga a cadência — só quando não há ninguém dentro dela.

    Apagar com gente inscrita deixaria os prospects num limbo: fora da cadência,
    mas com toques já enviados e nenhum registro do porquê pararam. Desativar
    resolve o caso real, que é "parem de mandar".
    """
    sequencia = _buscar(db, sequence_id)
    ativas = _ativas_por_sequencia(db, [sequence_id]).get(sequence_id, 0)
    if ativas:
        raise ConflictError(
            f"Há {ativas} prospect(s) nesta cadência. Desative-a — os inscritos param "
            "no próximo passo e o histórico fica.",
            details={"active_enrollments": ativas},
        )
    nome = sequencia.name
    db.delete(sequencia)
    db.flush()
    audit.record(
        db,
        action="sequence.deleted",
        resource_type="sequence",
        resource_id=sequence_id,
        payload={"name": nome},
        context=ctx,
    )


@router.post(
    "/{sequence_id}/enroll",
    response_model=schemas.EnrollResult,
    status_code=status.HTTP_201_CREATED,
)
def enroll(
    sequence_id: uuid.UUID,
    payload: schemas.EnrollRequest,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.EnrollResult:
    sequencia = _buscar(db, sequence_id)
    resultado = sequences.enroll(
        db, ctx.tenant_id, sequence=sequencia, prospect_ids=payload.prospect_ids
    )
    audit.record(
        db,
        action="sequence.enrolled",
        resource_type="sequence",
        resource_id=sequence_id,
        payload={
            "enrolled": len(resultado["enrolled"]),
            "skipped": len(resultado["skipped"]),
        },
        context=ctx,
    )
    return schemas.EnrollResult(
        enrolled=[schemas.EnrollmentResponse.model_validate(e) for e in resultado["enrolled"]],
        skipped=resultado["skipped"],
    )


@router.get("/{sequence_id}/enrollments", response_model=list[schemas.EnrollmentResponse])
def list_enrollments(
    sequence_id: uuid.UUID,
    enrollment_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, le=500),
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_READ)),
    db: Session = Depends(get_db),
):
    _buscar(db, sequence_id)
    stmt = select(SequenceEnrollment).where(SequenceEnrollment.sequence_id == sequence_id)
    if enrollment_status:
        stmt = stmt.where(SequenceEnrollment.status == enrollment_status)
    return list(
        db.execute(stmt.order_by(SequenceEnrollment.created_at.desc()).limit(limit)).scalars()
    )


@router.post("/enrollments/{enrollment_id}/stop", response_model=schemas.EnrollmentResponse)
def stop_enrollment(
    enrollment_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.CAMPAIGN_WRITE)),
    db: Session = Depends(get_db),
):
    """Tira um prospect da cadência sem mexer nos outros.

    É a operação mais comum de todas: alguém descobre pelo telefone que o lead
    já comprou, e o follow-up de terça-feira precisa não sair.
    """
    inscricao = db.get(SequenceEnrollment, enrollment_id)
    if inscricao is None:
        raise NotFound("Inscrição não encontrada nesta empresa")
    if inscricao.status != EnrollmentStatus.ACTIVE.value:
        raise ConflictError(f"Esta inscrição já está em '{inscricao.status}'")

    sequences.parar(db, inscricao, "parada manualmente")
    audit.record(
        db,
        action="sequence.enrollment_stopped",
        resource_type="sequence_enrollment",
        resource_id=enrollment_id,
        context=ctx,
    )
    return inscricao


@router.post("/tick", response_model=schemas.SequenceTickResult)
def tick(
    ctx: TenantContext = Depends(require(Permission.AGENT_RUN)),
    db: Session = Depends(get_db),
) -> schemas.SequenceTickResult:
    """Avança agora as cadências desta empresa cuja hora chegou.

    O worker faz isso sozinho a cada ciclo. O endpoint existe para não ser
    preciso esperar o relógio quando se está montando a cadência — e para dar
    onde olhar quando alguém pergunta por que o follow-up não saiu.
    """
    return schemas.SequenceTickResult(**sequences.tick(db, ctx.tenant_id))
