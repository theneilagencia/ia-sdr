"""Fechamento mensal: o que a plataforma media e não sabia cobrar.

O que falta para faturar não é gateway. No Brasil quem emite nota fiscal é o
contador ou um serviço de NFe, então gateway é conveniência de recebimento; o que
destrava cobrar é o fechamento — um número por empresa, por mês, com o consumo
medido por trás dele.

O que estes testes protegem, em ordem de importância: não cobrar duas vezes o
mesmo mês, não mexer no valor de fatura já emitida, não pagar o que nunca foi
emitido, e não vazar fatura de uma empresa para outra.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models.platform import Invoice, InvoiceStatus, Tenant, UsageEvent
from app.db.session import tenant_session, unscoped_session
from app.services import faturamento
from app.services.faturamento import Periodo
from app.services.usage import UsageKind

PERIODO = Periodo(2026, 8)


def _consumir(tenant_id, *, unidades: int, custo_micro: int, quando: datetime) -> None:
    """Grava consumo num instante escolhido — fechamento é sobre período."""
    with tenant_session(tenant_id) as session:
        session.add(
            UsageEvent(
                tenant_id=tenant_id,
                kind=UsageKind.RESEARCH.value,
                units=unidades,
                quantity=1,
                cost_micro_usd=custo_micro,
                created_at=quando,
            )
        )


def _contratar(tenant_id, *, mensal: int, excedente: int = 0, moeda: str = "BRL") -> None:
    with unscoped_session(reason="test:contrato") as session:
        tenant = session.get(Tenant, tenant_id)
        tenant.contract_monthly_cents = mensal
        tenant.contract_currency = moeda
        tenant.overage_cents_per_unit = excedente


def _fechar(tenant_id, periodo: Periodo = PERIODO) -> dict:
    with unscoped_session(reason="test:fechar") as session:
        return _copia(faturamento.fechar(session, tenant_id, periodo))


def _copia(fatura: Invoice) -> dict:
    """Os números, fora da sessão — devolver ORM de sessão fechada é DetachedInstanceError."""
    return {
        "id": fatura.id,
        "status": fatura.status,
        "currency": fatura.currency,
        "subscription_cents": fatura.subscription_cents,
        "ai_units": fatura.ai_units,
        "ai_units_included": fatura.ai_units_included,
        "ai_units_over": fatura.ai_units_over,
        "overage_cents": fatura.overage_cents,
        "ai_cost_micro_usd": fatura.ai_cost_micro_usd,
        "total_cents": fatura.total_cents,
    }


# ------------------------------------------------------------------ o período


def test_o_mes_que_se_fecha_e_o_que_terminou():
    """Fechar o mês corrente daria fatura que muda até o último dia."""
    assert Periodo.do_mes_passado(datetime(2026, 9, 15, tzinfo=UTC)) == Periodo(2026, 8)
    # Virada de ano: janeiro fecha dezembro do ano anterior.
    assert Periodo.do_mes_passado(datetime(2027, 1, 3, tzinfo=UTC)) == Periodo(2026, 12)


def test_o_periodo_pega_o_mes_inteiro_e_so_ele():
    fevereiro = Periodo(2026, 2)
    assert fevereiro.inicio.day == 1
    # 2026 não é bissexto; um mês com 28 dias não pode terminar no dia 30.
    assert fevereiro.fim.day == 28
    assert fevereiro.fim.month == 2


def test_consumo_de_outro_mes_nao_entra(make_tenant):
    t = make_tenant()
    dentro = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    _consumir(t["tenant_id"], unidades=10, custo_micro=5_000, quando=dentro)
    # Um dia antes e um dia depois da fronteira.
    _consumir(t["tenant_id"], unidades=99, custo_micro=99_000, quando=dentro.replace(month=7))
    _consumir(t["tenant_id"], unidades=77, custo_micro=77_000, quando=dentro.replace(month=9))

    fatura = _fechar(t["tenant_id"])
    assert fatura["ai_units"] == 10
    assert fatura["ai_cost_micro_usd"] == 5_000


# ------------------------------------------------------------------ a conta


def test_sem_contrato_fecha_mostrando_consumo_e_cobrando_nada(make_tenant):
    """O estado correto antes de alguém decidir o preço.

    Inventar um valor mensal padrão seria a plataforma escolhendo o preço do
    serviço de quem opera — e um número inventado num campo de dinheiro tem
    chance de virar cobrança de verdade.
    """
    t = make_tenant()
    _consumir(
        t["tenant_id"], unidades=42, custo_micro=123_456, quando=datetime(2026, 8, 2, tzinfo=UTC)
    )

    fatura = _fechar(t["tenant_id"])
    assert fatura["subscription_cents"] == 0
    assert fatura["total_cents"] == 0
    assert fatura["ai_units"] == 42
    assert fatura["ai_cost_micro_usd"] == 123_456


def test_o_contrato_entra_congelado_na_fatura(make_tenant):
    """Fatura é documento: mudar o contrato depois não reescreve o mês passado."""
    t = make_tenant()
    _contratar(t["tenant_id"], mensal=250_000, moeda="BRL")
    fatura = _fechar(t["tenant_id"])
    assert fatura["subscription_cents"] == 250_000
    assert fatura["total_cents"] == 250_000
    assert fatura["currency"] == "BRL"

    # Emite e depois muda o contrato: a fatura emitida não se mexe.
    with unscoped_session(reason="test:emitir") as session:
        faturamento.mudar_estado(
            session, session.get(Invoice, fatura["id"]), InvoiceStatus.ISSUED.value
        )
    _contratar(t["tenant_id"], mensal=900_000)
    with unscoped_session(reason="test:conferir") as session:
        assert session.get(Invoice, fatura["id"]).subscription_cents == 250_000


def test_excedente_so_existe_com_preco_e_acima_da_cota(make_tenant):
    """Zero em excedente é "não cobra excedente", e é o padrão.

    O limite do plano já recusa a execução, então excedente só aparece quando o
    limite foi levantado por contrato — e mesmo aí, cobrar por ele é decisão.
    """
    t = make_tenant(plan="starter")  # 5.000 unidades incluídas
    _consumir(
        t["tenant_id"], unidades=5_300, custo_micro=1_000, quando=datetime(2026, 8, 9, tzinfo=UTC)
    )

    sem_preco = _fechar(t["tenant_id"])
    assert sem_preco["ai_units_over"] == 300
    assert sem_preco["overage_cents"] == 0
    assert sem_preco["total_cents"] == 0

    _contratar(t["tenant_id"], mensal=100_000, excedente=7)
    com_preco = _fechar(t["tenant_id"])
    assert com_preco["ai_units_over"] == 300
    assert com_preco["overage_cents"] == 2_100
    assert com_preco["total_cents"] == 102_100


def test_plano_ilimitado_nao_tem_excedente(make_tenant):
    t = make_tenant(plan="enterprise")
    _contratar(t["tenant_id"], mensal=500_000, excedente=50)
    _consumir(
        t["tenant_id"], unidades=99_999, custo_micro=10, quando=datetime(2026, 8, 9, tzinfo=UTC)
    )
    fatura = _fechar(t["tenant_id"])
    assert fatura["ai_units_over"] == 0
    assert fatura["total_cents"] == 500_000


# ------------------------------------------------------------- idempotência


def test_fechar_duas_vezes_nao_cria_duas_cobrancas(make_tenant):
    """A segunda cobrança do mesmo mês pareceria legítima — é o pior tipo de bug."""
    t = make_tenant()
    _contratar(t["tenant_id"], mensal=150_000)
    _consumir(t["tenant_id"], unidades=5, custo_micro=100, quando=datetime(2026, 8, 5, tzinfo=UTC))

    primeira = _fechar(t["tenant_id"])
    # Mais consumo no mesmo mês, e fecha de novo.
    _consumir(t["tenant_id"], unidades=7, custo_micro=200, quando=datetime(2026, 8, 20, tzinfo=UTC))
    segunda = _fechar(t["tenant_id"])

    assert primeira["id"] == segunda["id"]
    assert segunda["ai_units"] == 12, "refazer o rascunho atualiza os números"
    with unscoped_session(reason="test:contar") as session:
        assert len(session.execute(select(Invoice)).scalars().all()) == 1


def test_fatura_emitida_nao_e_refeita(make_tenant):
    t = make_tenant()
    _contratar(t["tenant_id"], mensal=100_000)
    fatura = _fechar(t["tenant_id"])
    with unscoped_session(reason="test:emitir") as session:
        faturamento.mudar_estado(
            session, session.get(Invoice, fatura["id"]), InvoiceStatus.ISSUED.value
        )

    with (
        unscoped_session(reason="test:refazer") as session,
        pytest.raises(faturamento.FaturaJaEmitida),
    ):
        faturamento.fechar(session, t["tenant_id"], PERIODO)


# ------------------------------------------------------------------ o ciclo


def test_pagar_sem_emitir_nao_vale(make_tenant):
    """Cobrança que o cliente nunca recebeu aparecendo como quitada."""
    t = make_tenant()
    fatura = _fechar(t["tenant_id"])
    with (
        unscoped_session(reason="test:pagar") as session,
        pytest.raises(faturamento.TransicaoInvalida),
    ):
        faturamento.mudar_estado(
            session, session.get(Invoice, fatura["id"]), InvoiceStatus.PAID.value
        )


def test_o_ciclo_completo_guarda_as_datas(make_tenant):
    t = make_tenant()
    _contratar(t["tenant_id"], mensal=100_000)
    fatura = _fechar(t["tenant_id"])

    with unscoped_session(reason="test:ciclo") as session:
        linha = session.get(Invoice, fatura["id"])
        faturamento.mudar_estado(session, linha, InvoiceStatus.ISSUED.value)
        assert linha.issued_at is not None
        faturamento.mudar_estado(session, linha, InvoiceStatus.PAID.value)
        assert linha.paid_at is not None
        # Paga ainda pode ser anulada: estorno existe, e apagar não é opção.
        faturamento.mudar_estado(session, linha, InvoiceStatus.VOID.value)
        assert linha.status == InvoiceStatus.VOID.value

    with (
        unscoped_session(reason="test:anulada") as session,
        pytest.raises(faturamento.TransicaoInvalida),
    ):
        faturamento.mudar_estado(
            session, session.get(Invoice, fatura["id"]), InvoiceStatus.ISSUED.value
        )


# ------------------------------------------------------------------ pela API


def _admin(client, make_tenant, auth_headers):
    """Um tenant cujo owner é admin de plataforma."""
    t = make_tenant()
    with unscoped_session(reason="test:promover") as session:
        from app.db.models.platform import User

        user = session.get(User, t["user_id"])
        user.is_platform_admin = True
    return t, auth_headers(t["email"], t["password"])


def test_o_painel_fecha_o_mes_de_todas_as_empresas(client, make_tenant, auth_headers):
    t, headers = _admin(client, make_tenant, auth_headers)
    outra = make_tenant()
    _contratar(t["tenant_id"], mensal=100_000)
    _contratar(outra["tenant_id"], mensal=300_000)
    _consumir(
        outra["tenant_id"], unidades=3, custo_micro=900, quando=datetime(2026, 8, 7, tzinfo=UTC)
    )

    r = client.post(
        "/api/v1/admin/invoices/close", headers=headers, json={"year": 2026, "month": 8}
    )
    assert r.status_code == 200, r.text
    por_empresa = {f["tenant_id"]: f for f in r.json()}
    assert por_empresa[str(t["tenant_id"])]["total_cents"] == 100_000
    assert por_empresa[str(outra["tenant_id"])]["total_cents"] == 300_000
    assert por_empresa[str(outra["tenant_id"])]["ai_units"] == 3
    # A margem aparece em dólar, arredondada, para leitura humana.
    assert por_empresa[str(outra["tenant_id"])]["ai_cost_usd"] == pytest.approx(0.0009)


def test_a_listagem_traz_o_nome_da_empresa(client, make_tenant, auth_headers):
    t, headers = _admin(client, make_tenant, auth_headers)
    client.post("/api/v1/admin/invoices/close", headers=headers, json={"year": 2026, "month": 8})
    r = client.get("/api/v1/admin/invoices?year=2026&month=8", headers=headers)
    assert r.status_code == 200, r.text
    assert all(f["tenant_name"] for f in r.json()), "painel sem nome é painel de uuid"


def test_emitir_e_pagar_pelo_painel(client, make_tenant, auth_headers):
    t, headers = _admin(client, make_tenant, auth_headers)
    _contratar(t["tenant_id"], mensal=100_000)
    fechadas = client.post(
        "/api/v1/admin/invoices/close",
        headers=headers,
        json={"year": 2026, "month": 8, "tenant_id": str(t["tenant_id"])},
    ).json()
    fatura_id = fechadas[0]["id"]

    emitida = client.patch(
        f"/api/v1/admin/invoices/{fatura_id}", headers=headers, json={"status": "issued"}
    )
    assert emitida.status_code == 200, emitida.text
    assert emitida.json()["issued_at"]

    paga = client.patch(
        f"/api/v1/admin/invoices/{fatura_id}",
        headers=headers,
        json={"status": "paid", "notes": "PIX recebido em 05/09"},
    )
    assert paga.status_code == 200, paga.text
    assert paga.json()["paid_at"]
    assert paga.json()["notes"] == "PIX recebido em 05/09"


def test_pagar_sem_emitir_e_recusado_pela_api(client, make_tenant, auth_headers):
    t, headers = _admin(client, make_tenant, auth_headers)
    fechadas = client.post(
        "/api/v1/admin/invoices/close",
        headers=headers,
        json={"year": 2026, "month": 8, "tenant_id": str(t["tenant_id"])},
    ).json()
    r = client.patch(
        f"/api/v1/admin/invoices/{fechadas[0]['id']}", headers=headers, json={"status": "paid"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invoice_transition_invalid"


def test_uma_empresa_emitida_nao_para_o_fechamento_das_outras(client, make_tenant, auth_headers):
    """Virada de mês é operação em lote: parar na primeira deixa metade sem fechar."""
    t, headers = _admin(client, make_tenant, auth_headers)
    outra = make_tenant()
    _contratar(outra["tenant_id"], mensal=200_000)

    primeiro = client.post(
        "/api/v1/admin/invoices/close", headers=headers, json={"year": 2026, "month": 8}
    ).json()
    da_primeira = next(f for f in primeiro if f["tenant_id"] == str(t["tenant_id"]))
    client.patch(
        f"/api/v1/admin/invoices/{da_primeira['id']}", headers=headers, json={"status": "issued"}
    )

    de_novo = client.post(
        "/api/v1/admin/invoices/close", headers=headers, json={"year": 2026, "month": 8}
    )
    assert de_novo.status_code == 200, de_novo.text
    estados = {f["tenant_id"]: f["status"] for f in de_novo.json()}
    assert estados[str(t["tenant_id"])] == "issued", "a emitida volta como está"
    assert estados[str(outra["tenant_id"])] == "draft", "a outra continua fechando"


def test_contrato_negativo_e_recusado(client, make_tenant, auth_headers):
    """Cobrança negativa é crédito, e crédito é outro documento."""
    t, headers = _admin(client, make_tenant, auth_headers)
    r = client.patch(
        f"/api/v1/admin/tenants/{t['tenant_id']}",
        headers=headers,
        json={"contract_monthly_cents": -100},
    )
    assert r.status_code == 422


def test_o_contrato_volta_na_listagem_do_painel(client, make_tenant, auth_headers):
    """Editar contrato no escuro é como se apaga um valor sem perceber."""
    t, headers = _admin(client, make_tenant, auth_headers)
    client.patch(
        f"/api/v1/admin/tenants/{t['tenant_id']}",
        headers=headers,
        json={"contract_monthly_cents": 199_900, "overage_cents_per_unit": 3},
    )
    empresas = client.get("/api/v1/admin/tenants", headers=headers).json()
    minha = next(e for e in empresas if e["id"] == str(t["tenant_id"]))
    assert minha["contract_monthly_cents"] == 199_900
    assert minha["overage_cents_per_unit"] == 3
    assert minha["contract_currency"] == "BRL"


def test_quem_nao_e_admin_de_plataforma_nao_ve_fatura(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    assert client.get("/api/v1/admin/invoices", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/invoices/close", headers=headers, json={"year": 2026, "month": 8}
        ).status_code
        == 403
    )


def test_fatura_de_uma_empresa_nao_aparece_para_outra(make_tenant):
    """Fatura é dado de cliente, e dos mais sensíveis para vazar.

    A tabela entra no RLS como as outras: a sessão escopada em A não enxerga a
    fatura de B, mesmo perguntando direto.
    """
    a = make_tenant()
    b = make_tenant()
    for t in (a, b):
        with unscoped_session(reason="test:fechar") as session:
            faturamento.fechar(session, t["tenant_id"], PERIODO)

    with tenant_session(a["tenant_id"]) as session:
        minhas = session.execute(select(Invoice)).scalars().all()
        assert len(minhas) == 1
        assert minhas[0].tenant_id == a["tenant_id"]


def test_o_fechamento_fica_na_auditoria_da_empresa(client, make_tenant, auth_headers):
    t, headers = _admin(client, make_tenant, auth_headers)
    client.post(
        "/api/v1/admin/invoices/close",
        headers=headers,
        json={"year": 2026, "month": 8, "tenant_id": str(t["tenant_id"])},
    )
    acoes = [e["action"] for e in client.get("/api/v1/tenants/me/audit", headers=headers).json()]
    assert "invoice.closed" in acoes
