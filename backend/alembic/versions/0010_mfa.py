"""Segundo fator por TOTP nas colunas de `users`.

Fica no usuário, não no vínculo: a identidade é global nesta plataforma, então
quem serve duas empresas protege uma conta, não duas.

Seis colunas, e cada uma existe por um motivo que aparece quando falta:

* `mfa_secret` — cifrado com a chave Fernet, como toda credencial daqui. Em
  claro no banco, um dump de leitura entrega o segundo fator de todo mundo.
* `mfa_enabled_at` — nulo enquanto ninguém confirmou um código de verdade.
  Gerar segredo não liga nada; sem essa separação, quem configura errado o
  aplicativo se tranca fora da própria conta.
* `mfa_last_step` — o último passo de trinta segundos aceito, que é o que recusa
  reuso. Sem ele, um código interceptado vale a janela inteira para quem o
  pegou no caminho.
* `mfa_recovery_hashes` — SHA-256 dos códigos de recuperação, cada um de uso
  único. Perder o celular não pode significar perder a empresa.
* `mfa_failed_attempts` / `mfa_locked_until` — seis dígitos são um milhão de
  combinações e quem chega aqui já acertou a senha; sem contar tentativa, o
  segundo fator aceita chute infinito.

`users` é tabela de identidade e não tem `tenant_id`: nada de RLS aqui.

Revision ID: 0010_mfa
Revises: 0009_invitations
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0010_mfa"
down_revision = "0009_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret", sa.String(500)))
    op.add_column("users", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("mfa_last_step", sa.BigInteger()))
    op.add_column("users", sa.Column("mfa_recovery_hashes", JSONB()))
    op.add_column(
        "users",
        sa.Column("mfa_failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("mfa_locked_until", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for coluna in (
        "mfa_locked_until",
        "mfa_failed_attempts",
        "mfa_recovery_hashes",
        "mfa_last_step",
        "mfa_enabled_at",
        "mfa_secret",
    ):
        op.drop_column("users", coluna)
