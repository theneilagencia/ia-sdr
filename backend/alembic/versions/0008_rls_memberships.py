"""RLS em `memberships`: a última tabela com tenant_id sem rede de proteção.

`memberships` carrega `tenant_id` e ficou fora da política desde a 0002. A
consequência não era teórica: `limits.check_can_add_user` conta os membros pela
sessão **com escopo**, protegido apenas por um `WHERE tenant_id = ...` escrito à
mão — exatamente o que o RLS existe para não depender. O módulo de exportação
deste mesmo código diz a regra em voz alta: "é o RLS que garante, não um `WHERE`
que alguém pode esquecer".

Por que dava para ficar de fora sem ninguém notar: a tabela é da camada de
identidade. Para descobrir em qual empresa alguém entra é preciso ler os
memberships **antes** de existir um tenant ativo — e todos esses caminhos
(registro, login, troca de tenant, `/auth/me`, resolução de permissão, gestão de
membros) já usam a sessão sem escopo, que exige um motivo declarado. Nenhum deles
muda com esta política; o que muda é que o caminho com escopo passa a ser fechado
pelo banco.

Revision ID: 0008_rls_memberships
Revises: 0007_sequence_enroll
"""

from __future__ import annotations

from alembic import op

revision = "0008_rls_memberships"
down_revision = "0007_sequence_enroll"
branch_labels = None
depends_on = None

#: O mesmo predicado estrito da 0003: sem escape por variável de sessão. Quem
#: atravessa o isolamento faz isso por atributo do role (BYPASSRLS), verificado
#: pelo banco.
PREDICADO = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON memberships USING {PREDICADO} WITH CHECK {PREDICADO}"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON memberships")
    op.execute("ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships DISABLE ROW LEVEL SECURITY")
