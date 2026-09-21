"""Row Level Security em todas as tabelas com dados de cliente.

O isolamento não pode depender de o desenvolvedor lembrar do `WHERE tenant_id`.
Aqui o banco passa a recusar a linha do outro tenant mesmo que a query esqueça.

Como funciona:

* a aplicação define `app.tenant_id` no início de cada transação
  (`app.db.session.tenant_session`);
* a política `tenant_isolation` só deixa passar linhas daquele tenant, tanto na
  leitura (USING) quanto na escrita (WITH CHECK) — não dá para gravar linha com
  tenant_id alheio;
* `FORCE ROW LEVEL SECURITY` faz a política valer inclusive para o dono da
  tabela, que é quem a aplicação usa no MVP;
* a saída de emergência é `app.bypass_rls = 'on'`, usada só por autenticação,
  painel de plataforma e manutenção (`unscoped_session`, que exige um motivo).

Em produção, o próximo passo é a aplicação conectar com um role sem
BYPASSRLS e sem privilégio de SET nessa GUC — o código já está preparado,
porque só três pontos usam a sessão sem escopo.

Revision ID: 0002_rls
Revises: 0001_foundation
"""

from __future__ import annotations

from alembic import op

revision = "0002_rls"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "audit_logs",
    "usage_events",
    "companies",
    "contacts",
    "campaigns",
    "prospects",
    "research",
    "scores",
    "sequences",
    "conversations",
    "messages",
    "qualifications",
    "meetings",
    "company_profiles",
    "knowledge_documents",
    "knowledge_chunks",
    "ai_agents",
    "agent_runs",
    "integrations",
)

PREDICATE = """(
    current_setting('app.bypass_rls', true) = 'on'
    OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
)"""


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
        $$;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING {PREDICATE}
            WITH CHECK {PREDICATE}
            """
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")
