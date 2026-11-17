"""senha trocada invalida os tokens antigos

Revision ID: 0005_password_changed
Revises: 0004_jobs
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_password_changed"
down_revision = "0004_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nasce nulo: para quem já existe, nenhum token em circulação é invalidado
    # retroativamente. A partir da primeira troca, o campo passa a valer.
    op.add_column(
        "users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
