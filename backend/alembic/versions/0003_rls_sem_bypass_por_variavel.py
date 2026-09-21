"""Tira o escape de RLS por variável de sessão.

A política criada em 0002 aceitava `app.bypass_rls = 'on'` como passe livre.
Era conveniente — e errado: qualquer SQL executado na conexão da aplicação
podia ligar essa variável e sair do tenant. O privilégio de atravessar o
isolamento passa a ser atributo do role (BYPASSRLS), verificado pelo banco.

A partir daqui:

* a aplicação conecta com um role NOSUPERUSER/NOBYPASSRLS e só enxerga o
  tenant de `app.tenant_id`;
* migrations, autenticação e painel de plataforma conectam com o role
  administrativo, que tem BYPASSRLS;
* `FORCE ROW LEVEL SECURITY` continua valendo, então nem o dono da tabela
  escapa sem esse atributo.

Contexto de por que isso importava na prática: a imagem oficial do PostgreSQL
cria o `POSTGRES_USER` como superusuário. Uma aplicação apontada para ele
ignora RLS inteiro e serve dados de todos os tenants sem qualquer erro.

Revision ID: 0003_rls_role
Revises: 0002_rls
"""

from __future__ import annotations

from alembic import op

revision = "0003_rls_role"
down_revision = "0002_rls"
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

STRICT = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"

WITH_GUC_ESCAPE = """(
    current_setting('app.bypass_rls', true) = 'on'
    OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
)"""


def _recreate(predicate: str) -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING {predicate}
            WITH CHECK {predicate}
            """
        )


def upgrade() -> None:
    _recreate(STRICT)


def downgrade() -> None:
    _recreate(WITH_GUC_ESCAPE)
