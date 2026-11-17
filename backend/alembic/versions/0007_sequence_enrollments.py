"""estado de cada prospect dentro de uma cadência

Revision ID: 0007_sequence_enroll
Revises: 0006_knowledge_search

A tabela `sequences` existia desde a primeira migration e nada a usava. O que
faltava para ela valer alguma coisa era esta: sem estado por prospect,
"follow-up automático" viraria uma varredura que recalcula tudo a cada ciclo e
não tem como saber o que já mandou.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_sequence_enroll"
down_revision = "0006_knowledge_search"
branch_labels = None
depends_on = None

PREDICADO = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    op.create_table(
        "sequence_enrollments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sequence_id", sa.UUID(), nullable=False),
        sa.Column("prospect_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_step_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=60), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sequence_id"], ["sequences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sequence_enrollments_due", "sequence_enrollments", ["status", "next_run_at"]
    )
    op.create_index(
        "ix_sequence_enrollments_tenant_created",
        "sequence_enrollments",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        op.f("ix_sequence_enrollments_prospect_id"), "sequence_enrollments", ["prospect_id"]
    )
    op.create_index(
        op.f("ix_sequence_enrollments_sequence_id"), "sequence_enrollments", ["sequence_id"]
    )
    op.create_index(
        op.f("ix_sequence_enrollments_tenant_id"), "sequence_enrollments", ["tenant_id"]
    )
    # Um prospect em duas cadências ativas receberia dois emails no mesmo dia,
    # de duas linhas de raciocínio diferentes. Quem garante que isso não
    # acontece é o banco, não a aplicação: o worker roda concorrente.
    op.create_index(
        "uq_sequence_enrollment_ativa",
        "sequence_enrollments",
        ["tenant_id", "prospect_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    _aplicar_rls()


def _aplicar_rls() -> None:
    """Tabela com dado de cliente nasce isolada, sem exceção."""
    op.execute("ALTER TABLE sequence_enrollments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sequence_enrollments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON sequence_enrollments "
        f"USING {PREDICADO} WITH CHECK {PREDICADO}"
    )


def downgrade() -> None:
    op.drop_index("uq_sequence_enrollment_ativa", table_name="sequence_enrollments")
    op.drop_index(op.f("ix_sequence_enrollments_tenant_id"), table_name="sequence_enrollments")
    op.drop_index(op.f("ix_sequence_enrollments_sequence_id"), table_name="sequence_enrollments")
    op.drop_index(op.f("ix_sequence_enrollments_prospect_id"), table_name="sequence_enrollments")
    op.drop_index("ix_sequence_enrollments_tenant_created", table_name="sequence_enrollments")
    op.drop_index("ix_sequence_enrollments_due", table_name="sequence_enrollments")
    op.drop_table("sequence_enrollments")
