"""Consumo, limites de plano e criptografia de segredos."""

from __future__ import annotations

import pytest

from app.billing.plans import PLAN_LIMITS, limits_for
from app.core.crypto import decrypt_json, decrypt_secret, encrypt_json, encrypt_secret
from app.core.errors import LimitExceeded
from app.db.models.platform import Plan, Tenant
from app.db.session import tenant_session
from app.services import limits, usage


def test_unidades_por_operacao_seguem_a_tabela():
    assert usage.UNIT_COST[usage.UsageKind.RESEARCH] == 1
    assert usage.UNIT_COST[usage.UsageKind.AI_MESSAGE] == 1
    assert usage.UNIT_COST[usage.UsageKind.DEEP_RESEARCH] == 5
    assert usage.UNIT_COST[usage.UsageKind.QUALIFICATION] == 2
    assert usage.UNIT_COST[usage.UsageKind.VOICE_INTERACTION] == 10


def test_consumo_acumula_e_resume_por_tipo(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        usage.record_usage(
            session, tenant_id=t["tenant_id"], kind=usage.UsageKind.RESEARCH, quantity=3
        )
        usage.record_usage(
            session,
            tenant_id=t["tenant_id"],
            kind=usage.UsageKind.DEEP_RESEARCH,
            cost_micro_usd=184_000,
        )

    with tenant_session(t["tenant_id"]) as session:
        resumo = usage.usage_summary(session, t["tenant_id"])

    assert resumo["ai_units_used"] == 8  # 3 * 1 + 1 * 5
    assert resumo["by_kind"]["research"]["units"] == 3
    assert resumo["estimated_cost_usd"] == pytest.approx(0.184)


def test_cota_de_ia_bloqueia_antes_de_gastar(make_tenant):
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"ai_units_per_month": 5}
        usage.record_usage(
            session, tenant_id=t["tenant_id"], kind=usage.UsageKind.RESEARCH, quantity=4
        )

    with tenant_session(t["tenant_id"]) as session:
        usage.check_ai_budget(session, t["tenant_id"], 1)  # cabe
        with pytest.raises(LimitExceeded) as exc:
            usage.check_ai_budget(session, t["tenant_id"], 5)  # não cabe
    assert exc.value.details["limit"] == 5


def test_teto_em_dolar_trava_o_agente(make_tenant):
    """O outro freio, que mede outra coisa.

    Unidade é a moeda que o cliente compra; dólar é o que a Anthropic cobra. Sem
    o segundo, a única proteção contra gasto fora de padrão era o teto por
    execução — e mil execuções dentro do teto somam mil vezes o teto.
    """
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"ai_cost_usd_per_month": 10}
        usage.record_usage(
            session,
            tenant_id=t["tenant_id"],
            kind=usage.UsageKind.RESEARCH,
            cost_micro_usd=9_000_000,  # US$ 9 de US$ 10
        )

    with tenant_session(t["tenant_id"]) as session:
        usage.check_ai_budget(session, t["tenant_id"], 1)  # ainda tem folga

    with tenant_session(t["tenant_id"]) as session:
        usage.record_usage(
            session,
            tenant_id=t["tenant_id"],
            kind=usage.UsageKind.RESEARCH,
            cost_micro_usd=1_500_000,  # passou
        )

    with tenant_session(t["tenant_id"]) as session:
        with pytest.raises(LimitExceeded) as exc:
            usage.check_ai_budget(session, t["tenant_id"], 1)
    assert exc.value.details["kind"] == "cost"
    assert exc.value.details["limit_usd"] == 10
    assert exc.value.details["spent_usd"] == pytest.approx(10.5)


def test_gasto_de_execucao_que_falhou_conta_no_teto_em_dolar(make_tenant):
    """É por isto que o freio em dólar existe.

    Execução que falha depois de chamar o modelo gera evento com **zero
    unidade** e custo real. A cota em unidades nunca vê esse gasto: um agente
    que falha em série gastaria o mês inteiro sem consumir uma única unidade.
    """
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"ai_cost_usd_per_month": 2}
        for _ in range(3):
            usage.record_usage(
                session,
                tenant_id=t["tenant_id"],
                kind=usage.UsageKind.RESEARCH,
                quantity=0,  # falhou: não entrega, não cobra unidade
                cost_micro_usd=800_000,
            )

    with tenant_session(t["tenant_id"]) as session:
        resumo = usage.usage_summary(session, t["tenant_id"])
        # Nenhuma unidade consumida, US$ 2,40 gastos.
        assert resumo["ai_units_used"] == 0
        assert resumo["estimated_cost_usd"] == pytest.approx(2.4)
        assert resumo["estimated_cost_limit_usd"] == 2

        with pytest.raises(LimitExceeded) as exc:
            usage.check_ai_budget(session, t["tenant_id"], 1)
    assert exc.value.details["kind"] == "cost"


def test_sem_teto_contratado_o_dolar_nao_trava(make_tenant):
    """O default não inventa preço: quanto vale gastar é decisão de quem opera."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        usage.record_usage(
            session,
            tenant_id=t["tenant_id"],
            kind=usage.UsageKind.RESEARCH,
            cost_micro_usd=50_000_000,  # US$ 50
        )

    with tenant_session(t["tenant_id"]) as session:
        assert usage.effective_limits(session, t["tenant_id"])["ai_cost_usd_per_month"] == -1
        usage.check_ai_budget(session, t["tenant_id"], 1)  # não trava


def test_plano_starter_limita_uma_campanha(client, make_tenant, auth_headers):
    t = make_tenant(plan=Plan.STARTER.value)
    headers = auth_headers(t["email"], t["password"])

    primeira = client.post(
        "/api/v1/campaigns", headers=headers, json={"name": "Uma", "slug": "uma"}
    )
    assert primeira.status_code == 201

    segunda = client.post(
        "/api/v1/campaigns", headers=headers, json={"name": "Outra", "slug": "outra"}
    )
    assert segunda.status_code == 402
    assert segunda.json()["error"]["code"] == "limit_exceeded"


def test_override_contratual_vence_o_plano(make_tenant):
    t = make_tenant(plan=Plan.STARTER.value)
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"campaigns": 3}

    from app.db.models.sales import Campaign

    with tenant_session(t["tenant_id"]) as session:
        for i in range(3):
            limits.check_can_create_campaign(session, t["tenant_id"])
            session.add(
                Campaign(tenant_id=t["tenant_id"], name=f"C{i}", slug=f"c{i}")
            )
            session.flush()
        with pytest.raises(LimitExceeded):
            limits.check_can_create_campaign(session, t["tenant_id"])


def test_enterprise_e_ilimitado():
    efetivos = limits_for(Plan.ENTERPRISE)
    assert efetivos["campaigns"] == -1
    assert efetivos["ai_units_per_month"] == -1
    assert PLAN_LIMITS[Plan.GROWTH].campaigns == 10


def test_segredo_vai_e_volta_mas_nao_em_claro():
    """Os valores são longos de propósito.

    A asserção é "o segredo não aparece no texto cifrado", e o texto cifrado é
    base64 de bytes aleatórios: um segredo de três letras aparece nele por acaso
    de vez em quando — foi o que aconteceu com `"xyz"`, que falhou uma vez sem
    nada estar errado. Teste que falha por sorteio ensina a ignorar teste
    vermelho, que é o pior hábito que uma suíte pode criar.
    """
    cifrado = encrypt_secret("senha-do-cliente-que-nao-pode-aparecer")
    assert "senha-do-cliente-que-nao-pode-aparecer" not in cifrado
    assert decrypt_secret(cifrado) == "senha-do-cliente-que-nao-pode-aparecer"

    credenciais = {
        "client_id": "identificador-publico-do-cliente",
        "client_secret": "segredo-do-cliente-que-nao-pode-aparecer",
    }
    token = encrypt_json(credenciais)
    assert "segredo-do-cliente-que-nao-pode-aparecer" not in token
    assert decrypt_json(token) == credenciais


def test_cifragem_nao_e_deterministica():
    """Dois segredos iguais não produzem o mesmo texto cifrado."""
    assert encrypt_secret("igual") != encrypt_secret("igual")


def test_limite_de_pessoas_bloqueia_o_convite(client, make_tenant, auth_headers):
    """Starter dá duas pessoas: o owner e mais uma.

    E **convite pendente conta**: sem isso, um plano de duas pessoas aceitaria
    convites à vontade, e o limite só apareceria para quem tentasse aceitar por
    último — que não teve nada a ver com a decisão de convidar.
    """
    t = make_tenant(plan=Plan.STARTER.value)
    headers = auth_headers(t["email"], t["password"])

    primeira = client.post(
        "/api/v1/tenants/me/invitations",
        headers=headers,
        json={"email": "segunda@example.com", "role": "operator"},
    )
    assert primeira.status_code == 201, primeira.text

    # A segunda vaga está ocupada por um convite que ninguém aceitou ainda.
    terceira = client.post(
        "/api/v1/tenants/me/invitations",
        headers=headers,
        json={"email": "terceira@example.com", "role": "viewer"},
    )
    assert terceira.status_code == 402
    assert terceira.json()["error"]["code"] == "limit_exceeded"

    # Revogar o pendente devolve a vaga.
    convite_id = primeira.json()["id"]
    assert (
        client.delete(f"/api/v1/tenants/me/invitations/{convite_id}", headers=headers).status_code
        == 204
    )
    de_novo = client.post(
        "/api/v1/tenants/me/invitations",
        headers=headers,
        json={"email": "terceira@example.com", "role": "viewer"},
    )
    assert de_novo.status_code == 201, de_novo.text


def test_limite_de_contas_de_email_bloqueia_a_segunda(client, make_tenant, auth_headers):
    """Starter dá uma conta de envio.

    A tela de Configurações sempre edita **a** conta (o serviço faz upsert), então
    o único caminho capaz de passar do limite é a rota genérica de integrações — e
    é lá que a verificação está.
    """
    t = make_tenant(plan=Plan.STARTER.value)
    headers = auth_headers(t["email"], t["password"])
    corpo = {
        "provider": "smtp",
        "account_ref": "vendas@apymine.com",
        "display_name": "Vendas",
        "credentials": {"username": "vendas@apymine.com", "password": "x"},
        "config": {"host": "smtp.apymine.com", "port": 587},
    }

    primeira = client.post("/api/v1/integrations", headers=headers, json=corpo)
    assert primeira.status_code == 201, primeira.text

    segunda = client.post(
        "/api/v1/integrations",
        headers=headers,
        json={**corpo, "account_ref": "outro@apymine.com"},
    )
    assert segunda.status_code == 402
    assert segunda.json()["error"]["code"] == "limit_exceeded"


def test_limite_de_documentos_bloqueia_a_ingestao(client, make_tenant, auth_headers):
    """O teto da base de conhecimento vale nos dois caminhos de entrada."""
    t = make_tenant(plan=Plan.STARTER.value)
    with tenant_session(t["tenant_id"]) as session:
        session.get(Tenant, t["tenant_id"]).limit_overrides = {"knowledge_documents": 1}
    headers = auth_headers(t["email"], t["password"])

    primeiro = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        json={"title": "Playbook", "content": "O reembolso vale por trinta dias."},
    )
    assert primeiro.status_code == 201, primeiro.text

    segundo = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        json={"title": "FAQ", "content": "Perguntas frequentes."},
    )
    assert segundo.status_code == 402
    assert segundo.json()["error"]["code"] == "limit_exceeded"

    # O upload é a outra porta, e ela tem de recusar igual.
    subida = client.post(
        "/api/v1/knowledge/documents/upload",
        headers=headers,
        files={"file": ("mais.md", b"# Mais um", "text/markdown")},
    )
    assert subida.status_code == 402


def test_todo_limite_do_plano_tem_onde_ser_verificado():
    """Tripwire: limite declarado e não verificado é promessa não cumprida.

    Quem acrescentar um limite ao plano vai ver este teste falhar, e o conserto é
    escrever a verificação **e** o teste dela — não acrescentar a chave aqui.
    """
    from app.billing.plans import PLAN_LIMITS

    #: Onde cada limite do plano é imposto hoje. Mudou de lugar? Atualize aqui.
    ONDE = {
        "campaigns": "limits.check_can_create_campaign (POST /campaigns)",
        "users": "limits.check_can_add_user (POST /tenants/me/invitations)",
        "email_accounts": "limits.check_can_add_email_account (POST /integrations)",
        "knowledge_documents": "limits.check_can_add_document (POST /knowledge/documents*)",
        "prospects_per_month": "prospects._check_import_budget (POST /prospects/import*)",
        "ai_units_per_month": "usage.check_ai_budget (orchestrator.run_job)",
        "ai_cost_usd_per_month": "usage.check_ai_budget (orchestrator.run_job)",
    }

    declarados = set(PLAN_LIMITS[Plan.STARTER].as_dict()) - {"features"}
    sem_verificacao = sorted(declarados - set(ONDE))
    assert sem_verificacao == [], (
        f"limites declarados sem verificação conhecida: {sem_verificacao}. "
        "Escreva a imposição e o teste dela."
    )
    fantasmas = sorted(set(ONDE) - declarados)
    assert fantasmas == [], f"verificações para limites que não existem mais: {fantasmas}"
