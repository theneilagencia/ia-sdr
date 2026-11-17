"""Contatos: as pessoas dentro das contas-alvo.

A tabela era escrita só pelo import em lote. Corrigir um email digitado errado,
atualizar um cargo depois de uma promoção ou registrar um descadastro pedido
por telefone exigia ir ao banco.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import ConflictError, NotFound
from app.db.models.sales import Company, Contact, Prospect
from app.rbac.roles import Permission
from app.services import audit
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _empresa_existe(db: Session, company_id: uuid.UUID | None) -> None:
    if company_id is None:
        return
    if db.get(Company, company_id) is None:
        raise NotFound("Conta-alvo não encontrada nesta empresa")


@router.get("", response_model=list[schemas.ContactResponse])
def list_contacts(
    company_id: uuid.UUID | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    opted_out: bool | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    stmt = select(Contact)
    if company_id:
        stmt = stmt.where(Contact.company_id == company_id)
    if opted_out is not None:
        stmt = stmt.where(Contact.opted_out.is_(opted_out))
    if q:
        alvo = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.full_name).like(alvo),
                func.lower(func.coalesce(Contact.email, "")).like(alvo),
            )
        )
    return list(db.execute(stmt.order_by(Contact.created_at.desc()).limit(limit)).scalars())


@router.post("", response_model=schemas.ContactDetail, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: schemas.ContactCreate,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
):
    _empresa_existe(db, payload.company_id)
    dados = payload.model_dump()
    email = (dados.get("email") or "").lower() or None
    if email:
        # O import em lote já deduplica por email; criar à mão não deveria ser
        # a porta dos fundos para o mesmo lead entrar duas vezes na campanha.
        existente = db.execute(
            select(Contact).where(func.lower(Contact.email) == email)
        ).scalar_one_or_none()
        if existente is not None:
            raise ConflictError(
                f"Já existe um contato com o email {email} nesta empresa",
                details={"contact_id": str(existente.id)},
            )
    contato = Contact(tenant_id=ctx.tenant_id, **{**dados, "email": email})
    db.add(contato)
    db.flush()
    audit.record(
        db,
        action="contact.created",
        resource_type="contact",
        resource_id=contato.id,
        payload={"full_name": contato.full_name},
        context=ctx,
    )
    return contato


@router.get("/{contact_id}", response_model=schemas.ContactDetail)
def get_contact(
    contact_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_READ)),
    db: Session = Depends(get_db),
):
    contato = db.get(Contact, contact_id)
    if contato is None:
        raise NotFound("Contato não encontrado nesta empresa")
    return contato


@router.patch("/{contact_id}", response_model=schemas.ContactDetail)
def update_contact(
    contact_id: uuid.UUID,
    payload: schemas.ContactUpdate,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
):
    contato = db.get(Contact, contact_id)
    if contato is None:
        raise NotFound("Contato não encontrado nesta empresa")

    mudancas = payload.model_dump(exclude_unset=True)
    if "company_id" in mudancas:
        _empresa_existe(db, mudancas["company_id"])
    if "email" in mudancas and mudancas["email"]:
        mudancas["email"] = mudancas["email"].lower()
    for campo, valor in mudancas.items():
        setattr(contato, campo, valor)
    db.flush()

    audit.record(
        db,
        action="contact.updated",
        resource_type="contact",
        resource_id=contato.id,
        payload={"campos": sorted(mudancas)},
        context=ctx,
    )
    return contato


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.PROSPECT_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    """Apaga o contato — e só enquanto ele não tiver entrado numa campanha.

    Depois que virou prospect existe conversa, mensagem enviada e, possivelmente,
    um pedido de descadastro registrado nele. Apagar ali apagaria a prova de que
    o pedido foi feito, e a plataforma voltaria a escrever para quem pediu para
    parar no próximo import. Para esses, o caminho é marcar `opted_out`.
    """
    contato = db.get(Contact, contact_id)
    if contato is None:
        raise NotFound("Contato não encontrado nesta empresa")

    em_campanha = db.execute(
        select(func.count(Prospect.id)).where(Prospect.contact_id == contact_id)
    ).scalar_one()
    if em_campanha:
        raise ConflictError(
            f"{contato.full_name} já está em {em_campanha} campanha(s) e tem histórico. "
            "Para parar de escrever, marque o descadastro em vez de apagar.",
            details={"prospects": int(em_campanha)},
        )

    nome = contato.full_name
    db.delete(contato)
    db.flush()
    audit.record(
        db,
        action="contact.deleted",
        resource_type="contact",
        resource_id=contact_id,
        payload={"full_name": nome},
        context=ctx,
    )
