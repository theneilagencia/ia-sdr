"""Fechamento mensal: transformar o que foi medido em cobrança.

A plataforma media consumo desde o primeiro sprint e não tinha como faturar. O que
faltava não era gateway: no Brasil quem emite nota fiscal é o contador ou um
serviço de NFe, então o gateway é conveniência de recebimento. O que destrava
cobrar é **o fechamento** — um número por empresa, por mês, com o consumo por trás
dele.

Três decisões sustentam este módulo:

* **Nenhum preço é inventado.** O valor mensal, a moeda e o preço da unidade
  excedente vêm do contrato de cada empresa, e nascem zerados. Com zero o
  fechamento continua fechando: mostra consumo e cobra nada, que é o estado
  correto antes de alguém decidir o preço.
* **Os números ficam congelados na linha.** Fatura é documento: o consumo do mês
  passado precisa continuar dizendo o que dizia quando foi emitida, mesmo que o
  contrato mude depois.
* **Emitida não é recalculada.** Refazer o fechamento atualiza rascunho; a partir
  de `issued`, mexer no número é discussão com o cliente, não correção de sistema.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.platform import Invoice, InvoiceStatus, Tenant, UsageEvent
from app.services.usage import effective_limits


class FaturaJaEmitida(AppError):
    """Refazer o fechamento de uma fatura emitida mudaria um documento entregue."""

    code = "invoice_already_issued"
    status_code = 409

    def __init__(self) -> None:
        super().__init__(
            "Esta fatura já foi emitida: os números não são mais recalculados. "
            "Para corrigir, anule e feche o período de novo."
        )


class TransicaoInvalida(AppError):
    code = "invoice_transition_invalid"
    status_code = 409

    def __init__(self, de: str, para: str) -> None:
        super().__init__(f"Fatura em '{de}' não pode ir para '{para}'.")


#: Para onde cada estado pode ir. Pagar sem emitir não é atalho: é cobrança que o
#: cliente nunca recebeu aparecendo como quitada.
TRANSICOES: dict[str, set[str]] = {
    InvoiceStatus.DRAFT.value: {InvoiceStatus.ISSUED.value, InvoiceStatus.VOID.value},
    InvoiceStatus.ISSUED.value: {InvoiceStatus.PAID.value, InvoiceStatus.VOID.value},
    InvoiceStatus.PAID.value: {InvoiceStatus.VOID.value},
    InvoiceStatus.VOID.value: set(),
}


@dataclass(frozen=True, slots=True)
class Periodo:
    ano: int
    mes: int

    @property
    def inicio(self) -> datetime:
        return datetime(self.ano, self.mes, 1, tzinfo=UTC)

    @property
    def fim(self) -> datetime:
        ultimo = calendar.monthrange(self.ano, self.mes)[1]
        return datetime(self.ano, self.mes, ultimo, 23, 59, 59, 999999, tzinfo=UTC)

    @classmethod
    def do_mes_passado(cls, agora: datetime | None = None) -> Periodo:
        """O período que se fecha na prática: o mês que terminou.

        Fechar o mês corrente daria uma fatura que muda até o último dia — e
        alguém a emitiria no dia 10 sem perceber.
        """
        hoje = agora or datetime.now(UTC)
        return cls(hoje.year - 1, 12) if hoje.month == 1 else cls(hoje.year, hoje.month - 1)


def consumo(session: Session, tenant_id: uuid.UUID, periodo: Periodo) -> tuple[int, int]:
    """Unidades e custo real (micro-dólares) do período, do jeito que foram medidos."""
    linha = session.execute(
        select(
            func.coalesce(func.sum(UsageEvent.units), 0),
            func.coalesce(func.sum(UsageEvent.cost_micro_usd), 0),
        )
        .where(UsageEvent.tenant_id == tenant_id)
        .where(UsageEvent.created_at >= periodo.inicio)
        .where(UsageEvent.created_at <= periodo.fim)
    ).one()
    return int(linha[0]), int(linha[1])


def fechar(session: Session, tenant_id: uuid.UUID, periodo: Periodo) -> Invoice:
    """Fecha o mês daquela empresa. Rodar de novo atualiza o rascunho.

    Idempotente por construção — a chave única de (empresa, ano, mês) garante que
    não existam duas cobranças do mesmo período, e a segunda pareceria legítima.
    """
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError("Empresa não encontrada")

    fatura = session.execute(
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.period_year == periodo.ano)
        .where(Invoice.period_month == periodo.mes)
    ).scalar_one_or_none()

    if fatura is not None and fatura.status != InvoiceStatus.DRAFT.value:
        raise FaturaJaEmitida()

    unidades, custo = consumo(session, tenant_id, periodo)
    incluidas = effective_limits(session, tenant_id)["ai_units_per_month"]
    # -1 é "ilimitado" no vocabulário dos planos: nada é excedente.
    excedentes = 0 if incluidas < 0 else max(0, unidades - incluidas)
    excedente_cents = excedentes * tenant.overage_cents_per_unit

    if fatura is None:
        fatura = Invoice(tenant_id=tenant_id, period_year=periodo.ano, period_month=periodo.mes)
        session.add(fatura)

    fatura.currency = tenant.contract_currency
    fatura.subscription_cents = tenant.contract_monthly_cents
    fatura.ai_units = unidades
    fatura.ai_units_included = incluidas
    fatura.ai_units_over = excedentes
    fatura.overage_cents = excedente_cents
    fatura.ai_cost_micro_usd = custo
    fatura.total_cents = tenant.contract_monthly_cents + excedente_cents
    session.flush()
    return fatura


def mudar_estado(
    session: Session, fatura: Invoice, novo: str, *, agora: datetime | None = None
) -> Invoice:
    """Emitir, pagar ou anular — e só nessa ordem."""
    agora = agora or datetime.now(UTC)
    if novo not in TRANSICOES.get(fatura.status, set()):
        raise TransicaoInvalida(fatura.status, novo)

    fatura.status = novo
    if novo == InvoiceStatus.ISSUED.value:
        fatura.issued_at = agora
    elif novo == InvoiceStatus.PAID.value:
        fatura.paid_at = agora
    session.flush()
    return fatura


def do_periodo(session: Session, periodo: Periodo) -> list[Invoice]:
    """As faturas daquele mês na sessão atual — usada pelo painel da plataforma."""
    return list(
        session.execute(
            select(Invoice)
            .where(Invoice.period_year == periodo.ano)
            .where(Invoice.period_month == periodo.mes)
            .order_by(Invoice.created_at)
        ).scalars()
    )


__all__ = [
    "TRANSICOES",
    "FaturaJaEmitida",
    "Periodo",
    "TransicaoInvalida",
    "consumo",
    "do_periodo",
    "fechar",
    "mudar_estado",
]
