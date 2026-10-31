"""busca na base de conhecimento por relevância

Revision ID: 0006_knowledge_search
Revises: 0005_password_changed

Até aqui a recuperação devolvia os trechos mais recentes. Numa base com dez
documentos isso passa; com cem, o agente recebe o que foi carregado por último
e responde "não está na minha base" sobre algo que está — e escalar para humano
todo dia, à toa, é o jeito mais rápido de a empresa desligar o agente.

Por que texto e não vetor: a Anthropic não tem API de embeddings, então busca
vetorial exigiria um segundo fornecedor, uma chave a mais e um custo por
documento. O `pgvector` também não vem na imagem oficial do PostgreSQL nem no
serviço do CI. Busca textual é nativa, roda em qualquer lugar e é testável — a
coluna `embedding` continua no modelo para quando valer a pena.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_knowledge_search"
down_revision = "0005_password_changed"
branch_labels = None
depends_on = None

#: Coluna gerada: o índice não pode ficar dessincronizado do conteúdo, porque
#: quem escreve o chunk não precisa saber que a busca existe.
EXPRESSAO = "to_tsvector('portuguese'::regconfig, coalesce(content, ''))"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE knowledge_chunks "
        f"ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ({EXPRESSAO}) STORED"
    )
    op.create_index(
        "ix_knowledge_chunks_search",
        "knowledge_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )
    # Recuperação sempre filtra por tenant antes de ranquear; sem este índice o
    # filtro viraria varredura na tabela inteira do banco.
    op.create_index(
        "ix_knowledge_chunks_tenant_document",
        "knowledge_chunks",
        ["tenant_id", "document_id", "ordinal"],
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "char_count")
    op.drop_index("ix_knowledge_chunks_tenant_document", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_search", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "search_vector")
