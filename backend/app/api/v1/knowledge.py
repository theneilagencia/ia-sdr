"""Base de conhecimento do tenant.

Faltava inteira: as tabelas existiam desde a primeira migration e nenhum
endpoint escrevia nelas. O efeito era pior do que uma lacuna de API — o
Conversation Agent responde *apenas* com o que está na base e escala para
humano quando não encontra, então uma base que ninguém consegue alimentar
significa um agente que escala tudo, todo dia.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.core.errors import AppError, NotFound
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.db.models.sales import Campaign
from app.rbac.roles import Permission
from app.services import audit, knowledge, limits
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

#: Extensões que são texto. A lista é explícita porque o resto do mundo manda
#: PDF e .docx, e a mensagem de recusa precisa dizer o que fazer com eles.
EXTENSOES_DE_TEXTO = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log"}

#: Assinaturas de formato binário que chegam com mais frequência. Reconhecer
#: para responder "converta para texto" em vez de "documento vazio".
ASSINATURAS = {
    b"%PDF": "PDF",
    b"PK\x03\x04": "Word, Excel ou outro arquivo compactado (.docx, .xlsx)",
    b"\xd0\xcf\x11\xe0": "Word ou Excel antigo (.doc, .xls)",
}

#: Teto do upload. O limite de conteúdo indexável está em `services/knowledge`;
#: este existe antes de ler o arquivo, para não carregar 500 MB na memória.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class FormatoNaoSuportado(AppError):
    code = "unsupported_document_format"

    def __init__(self, formato: str) -> None:
        super().__init__(
            f"Este arquivo é {formato}. A base aceita texto: .txt, .md, .csv, .json "
            "ou .yaml. Abra o arquivo, copie o texto e cole — ou exporte como texto "
            "antes de enviar."
        )


class ArquivoGrande(AppError):
    #: Código próprio: "o arquivo que você subiu passa do teto" e "o texto que
    #: você colou é longo demais" são dois problemas com duas soluções
    #: diferentes, e compartilhar o código faz a tela tratá-los como um só.
    code = "file_too_large"

    def __init__(self) -> None:
        super().__init__(
            f"O arquivo passa de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Divida em partes menores."
        )


def _texto_do_upload(nome: str, bruto: bytes) -> str:
    for assinatura, formato in ASSINATURAS.items():
        if bruto.startswith(assinatura):
            raise FormatoNaoSuportado(formato)

    sufixo = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
    if sufixo and sufixo not in EXTENSOES_DE_TEXTO:
        raise FormatoNaoSuportado(f"um arquivo {sufixo}")

    try:
        return bruto.decode("utf-8")
    except UnicodeDecodeError:
        # Muito arquivo exportado de sistema antigo vem em Latin-1. Tentar é
        # melhor do que devolver "documento vazio" para um texto legível.
        try:
            return bruto.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise FormatoNaoSuportado("um arquivo binário") from exc


def _validar_campanhas(db: Session, campaign_ids: list[uuid.UUID]) -> list[str]:
    """Campanha de outro tenant não existe daqui — o RLS já filtra, e a
    consulta confirma para a mensagem de erro sair certa."""
    if not campaign_ids:
        return []
    encontradas = {
        c.id for c in db.execute(select(Campaign).where(Campaign.id.in_(campaign_ids))).scalars()
    }
    faltando = [str(c) for c in campaign_ids if c not in encontradas]
    if faltando:
        raise NotFound("Campanha não encontrada nesta empresa", details={"campaign_ids": faltando})
    return [str(c) for c in campaign_ids]


def _resposta(documento: KnowledgeDocument, chunk_count: int) -> schemas.KnowledgeDocumentResponse:
    return schemas.KnowledgeDocumentResponse(
        id=documento.id,
        title=documento.title,
        source_type=documento.source_type,
        source_uri=documento.source_uri,
        mime_type=documento.mime_type,
        status=documento.status,
        campaign_ids=[uuid.UUID(str(c)) for c in (documento.campaign_ids or [])],
        char_count=documento.char_count,
        chunk_count=chunk_count,
        error=documento.error,
        created_at=documento.created_at,
    )


@router.get("/documents", response_model=list[schemas.KnowledgeDocumentResponse])
def list_documents(
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.KnowledgeDocumentResponse]:
    documentos = list(
        db.execute(
            select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())
        ).scalars()
    )
    contagens = knowledge.counts_by_document(db, ctx.tenant_id)
    return [_resposta(d, contagens.get(d.id, 0)) for d in documentos]


@router.post(
    "/documents",
    response_model=schemas.KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document(
    payload: schemas.KnowledgeDocumentCreate,
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.KnowledgeDocumentResponse:
    """Texto colado direto. É o caminho mais curto para a base sair do zero."""
    limits.check_can_add_document(db, ctx.tenant_id)
    escopo = _validar_campanhas(db, payload.campaign_ids)

    documento, fatias = knowledge.ingest_document(
        db,
        ctx.tenant_id,
        title=payload.title,
        content=payload.content,
        source_type=payload.source_type,
        source_uri=payload.source_uri,
        mime_type="text/plain",
        campaign_ids=escopo,
        uploaded_by=ctx.user_id,
    )
    audit.record(
        db,
        action="knowledge.document_created",
        resource_type="knowledge_document",
        resource_id=documento.id,
        payload={"title": documento.title, "chunks": fatias},
        context=ctx,
    )
    return _resposta(documento, fatias)


@router.post(
    "/documents/upload",
    response_model=schemas.KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    campaign_ids: str = Form(default=""),
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_WRITE)),
    db: Session = Depends(get_db),
) -> schemas.KnowledgeDocumentResponse:
    """Upload de arquivo de texto.

    `campaign_ids` vem como lista separada por vírgula porque o formulário é
    multipart, e não JSON — quem manda é um `<input type=file>`.
    """
    limits.check_can_add_document(db, ctx.tenant_id)

    bruto = await file.read()
    if len(bruto) > MAX_UPLOAD_BYTES:
        raise ArquivoGrande()
    texto = _texto_do_upload(file.filename or "", bruto)

    ids = [uuid.UUID(p.strip()) for p in campaign_ids.split(",") if p.strip()]
    escopo = _validar_campanhas(db, ids)

    documento, fatias = knowledge.ingest_document(
        db,
        ctx.tenant_id,
        title=title or (file.filename or "Documento"),
        content=texto,
        source_type="upload",
        source_uri=file.filename,
        mime_type=file.content_type,
        campaign_ids=escopo,
        uploaded_by=ctx.user_id,
    )
    audit.record(
        db,
        action="knowledge.document_uploaded",
        resource_type="knowledge_document",
        resource_id=documento.id,
        payload={"title": documento.title, "chunks": fatias, "file": file.filename},
        context=ctx,
    )
    return _resposta(documento, fatias)


@router.get("/documents/{document_id}", response_model=schemas.KnowledgeDocumentDetail)
def get_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_READ)),
    db: Session = Depends(get_db),
) -> schemas.KnowledgeDocumentDetail:
    documento = db.get(KnowledgeDocument, document_id)
    if documento is None:
        raise NotFound("Documento não encontrado nesta empresa")
    fatias = list(
        db.execute(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.ordinal)
        ).scalars()
    )
    base = _resposta(documento, len(fatias))
    return schemas.KnowledgeDocumentDetail(
        **base.model_dump(),
        chunks=[
            schemas.KnowledgeChunkResponse(
                id=c.id, ordinal=c.ordinal, content=c.content, token_count=c.token_count
            )
            for c in fatias
        ],
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    documento = db.get(KnowledgeDocument, document_id)
    if documento is None:
        raise NotFound("Documento não encontrado nesta empresa")
    titulo = documento.title
    # As fatias vão embora por ON DELETE CASCADE, declarado no modelo.
    db.delete(documento)
    db.flush()
    audit.record(
        db,
        action="knowledge.document_deleted",
        resource_type="knowledge_document",
        resource_id=document_id,
        payload={"title": titulo},
        context=ctx,
    )


@router.get("/search", response_model=list[schemas.KnowledgeSearchHit])
def search(
    q: str = Query(default="", max_length=500),
    campaign_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(require(Permission.KNOWLEDGE_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.KnowledgeSearchHit]:
    """A mesma busca que o agente usa.

    Existir como endpoint é o que permite conferir, antes de culpar o modelo, se
    o trecho que faltou na resposta estava mesmo na base.
    """
    achados = knowledge.search_chunks(db, ctx.tenant_id, q, campaign_id=campaign_id, limit=limit)
    return [schemas.KnowledgeSearchHit(**a) for a in achados]
