"""Contrato por empresa e fechamento mensal.

A plataforma media tudo — unidades, micro-dólares por modelo, por agente — e não
tinha como transformar isso em cobrança. Faltava o passo que um gateway **não**
resolve: no Brasil, quem emite nota fiscal é o contador ou um serviço de NFe, então
o gateway é conveniência de recebimento, não o que destrava faturar.

Os três campos de contrato nascem zerados de propósito. Quanto custa o serviço é
número comercial de quem opera, não default para o código inventar; com zero, o
fechamento continua fechando — só não cobra nada, e mostra o consumo, que é o que
o operador precisa ver antes de decidir o preço.

`invoices` guarda os números **congelados**, e não recalculados na leitura, porque
fatura é documento: o consumo do mês passado precisa continuar dizendo o que dizia
quando foi emitida, mesmo que o preço mude depois. A chave única por
(tenant, ano, mês) é o que impede o fechamento rodado duas vezes de criar duas
cobranças do mesmo período — e a segunda pareceria legítima.

Entra no RLS com o mesmo predicado estrito das outras tabelas do cliente: fatura é
dado de cliente, e é dos mais sensíveis para vazar entre empresas.

Revision ID: 0012_faturamento
Revises: 0011_convite_de_calendario
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision = "0012_faturamento"
down_revision = "0011_convite_de_calendario"
branch_labels = None
depends_on = None

PREDICADO = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("contract_monthly_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column("contract_currency", sa.String(3), nullable=False, server_default="BRL"),
    )
    op.add_column(
        "tenants",
        sa.Column("overage_cents_per_unit", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "invoices",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("subscription_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_units_included", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_units_over", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overage_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_cost_micro_usd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    # O composto vem do mixin `TenantScoped`; sem ele o teste de divergência
    # entre modelo e migration falha, que é o serviço que aquele teste presta.
    op.create_index("ix_invoices_tenant_created", "invoices", ["tenant_id", "created_at"])
    op.create_unique_constraint(
        "uq_invoices_periodo", "invoices", ["tenant_id", "period_year", "period_month"]
    )

    op.execute("ALTER TABLE invoices ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoices FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON invoices USING {PREDICADO} WITH CHECK {PREDICADO}"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invoices")
    op.drop_constraint("uq_invoices_periodo", "invoices", type_="unique")
    op.drop_index("ix_invoices_tenant_created", table_name="invoices")
    op.drop_index("ix_invoices_tenant_id", table_name="invoices")
    op.drop_table("invoices")
    op.drop_column("tenants", "overage_cents_per_unit")
    op.drop_column("tenants", "contract_currency")
    op.drop_column("tenants", "contract_monthly_cents")
