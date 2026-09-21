"""Company Brain e base de conhecimento do tenant.

O que a IA sabe sobre o negócio do cliente. É o ativo mais sensível da
plataforma: nunca pode atravessar a fronteira de um tenant.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScoped, TimestampMixin, uuid_pk


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class CompanyProfile(Base, TenantScoped, TimestampMixin):
    """O Company Brain: quem é o cliente e como ele vende.

    Um por tenant. Cada bloco é JSONB porque o formato ainda vai evoluir
    bastante, e versionar schema rígido no MVP custa mais do que rende.
    """

    __tablename__ = "company_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_company_profile_tenant"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    legal_name: Mapped[str | None] = mapped_column(String(300))
    website: Mapped[str | None] = mapped_column(String(500))
    positioning: Mapped[str | None] = mapped_column(Text)
    products: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    services: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    icp: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    personas: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pricing: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    faqs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    objections: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    competitors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sales_playbook: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    brand_voice: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ai_policies: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class KnowledgeDocument(Base, TenantScoped, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="upload")
    source_uri: Mapped[str | None] = mapped_column(String(1000))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.UPLOADED.value
    )
    # Escopo de leitura: tenant inteiro ou campanhas específicas
    campaign_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    checksum: Mapped[str | None] = mapped_column(String(128))
    #: Tamanho do texto extraído. Serve para a tela mostrar o que foi indexado
    #: e para diagnosticar upload que chegou vazio.
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class KnowledgeChunk(Base, TenantScoped, TimestampMixin):
    """Trecho indexado.

    O embedding fica como JSONB no MVP; quando o volume justificar, migra para
    pgvector sem mexer no resto do modelo.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        # Declarar __table_args__ aqui substitui o índice que o TenantScoped
        # daria de graça, então ele vem repetido de propósito.
        Index("ix_knowledge_chunks_tenant_created", "tenant_id", "created_at"),
        # GIN sobre a coluna gerada: é o que faz a busca por relevância ser
        # busca, e não varredura.
        Index("ix_knowledge_chunks_search", "search_vector", postgresql_using="gin"),
        # A recuperação filtra por tenant antes de ranquear, sempre.
        Index("ix_knowledge_chunks_tenant_document", "tenant_id", "document_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Reservada para busca vetorial, quando houver um fornecedor de
    #: embeddings no desenho. A recuperação de hoje usa `search_vector`.
    embedding: Mapped[list | None] = mapped_column(JSONB)
    #: Coluna gerada pelo banco a partir de `content` — quem escreve o chunk
    #: não precisa saber que a busca existe, e o índice não dessincroniza.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('portuguese'::regconfig, coalesce(content, ''))", persisted=True),
        nullable=True,
    )
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
