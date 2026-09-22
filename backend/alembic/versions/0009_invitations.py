"""Convites com aceite: quem escolhe a senha é quem entra.

A tabela existe para tirar do fluxo de entrada duas coisas erradas ao mesmo
tempo. A primeira é a senha: um admin digitava a senha de outra pessoa, o que
significa que a senha inicial de alguém passava pela mão — e pelo histórico do
navegador — de um terceiro. A segunda é a resposta: para explicar que a senha
seria ignorada quando o email já tinha conta, a API dizia justamente isso —
"esta pessoa já existe na plataforma" —, e quem convida descobria, de graça,
quem já é usuário de qual concorrente.

O token não é guardado: guarda-se o SHA-256 dele. Hash rápido de propósito, o
mesmo raciocínio do link de descadastro — o segredo tem 32 bytes de aleatório,
não é uma senha escolhida por gente, e não há dicionário que o alcance.

`invitations` entra no RLS com o mesmo predicado estrito das outras: o aceite é
público e roda na sessão sem escopo, com motivo declarado, porque nesse momento
não existe tenant ativo — quem clica ainda não é membro de nada.

Revision ID: 0009_invitations
Revises: 0008_rls_memberships
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0009_invitations"
down_revision = "0008_rls_memberships"
branch_labels = None
depends_on = None

PREDICADO = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column(
            "id", PGUUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by", PGUUID(as_uuid=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_invitations_tenant_id", "invitations", ["tenant_id"])
    # O composto vem do mixin `TenantScoped`, que o declara para toda tabela do
    # cliente. Sem ele aqui, o teste de divergência entre modelo e migration
    # falha — que é exatamente o serviço que aquele teste presta.
    op.create_index(
        "ix_invitations_tenant_created", "invitations", ["tenant_id", "created_at"]
    )
    op.create_index("ix_invitations_email", "invitations", ["email"])
    # Único e indexado: é por ele que o aceite encontra o convite, e é o que
    # impede dois convites compartilharem segredo por acidente.
    op.create_index("ix_invitations_token_hash", "invitations", ["token_hash"], unique=True)

    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON invitations "
        f"USING {PREDICADO} WITH CHECK {PREDICADO}"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invitations")
    op.drop_index("ix_invitations_token_hash", table_name="invitations")
    op.drop_index("ix_invitations_email", table_name="invitations")
    op.drop_index("ix_invitations_tenant_created", table_name="invitations")
    op.drop_index("ix_invitations_tenant_id", table_name="invitations")
    op.drop_table("invitations")
