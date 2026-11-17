"""Estado do convite de calendário na reunião.

A reunião já era marcada na plataforma e não chegava ao calendário do lead. O
convite vai como `.ics` pelo email da própria empresa — sem OAuth de Google ou
Microsoft, que num produto multiempresa custaria aplicativo verificado e
consentimento por cliente antes de entregar nada.

Duas colunas, e as duas são sobre não mentir para quem opera:

* `invite_sent_at` — nulo significa "está marcado aqui e o outro lado não sabe".
  Sem isso, a tela não consegue distinguir compromisso combinado de compromisso
  anotado, que é a diferença entre reunião e esperança.
* `invite_sequence` — o `SEQUENCE` do iCalendar. Reenviar com a sequência parada
  faz o cliente de calendário ignorar o arquivo, porque ele já conhece aquele
  UID: o lead receberia o email e o calendário dele não mudaria.

Revision ID: 0011_convite_de_calendario
Revises: 0010_mfa
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_convite_de_calendario"
down_revision = "0010_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("invite_sent_at", sa.DateTime(timezone=True)))
    op.add_column(
        "meetings",
        sa.Column("invite_sequence", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("meetings", "invite_sequence")
    op.drop_column("meetings", "invite_sent_at")
