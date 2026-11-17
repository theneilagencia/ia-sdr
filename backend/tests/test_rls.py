"""O teste que justifica a escolha de PostgreSQL + RLS.

Se algum destes quebrar, dado de um cliente está visível para outro.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.db.models.sales import Campaign
from app.db.session import SessionFactory, tenant_session, unscoped_session


def _campaign(tenant_id: uuid.UUID, name: str) -> Campaign:
    return Campaign(tenant_id=tenant_id, name=name, slug=name.lower().replace(" ", "-"))


def test_tenant_ve_apenas_as_proprias_campanhas(make_tenant):
    a = make_tenant("Empresa A")
    b = make_tenant("Empresa B")

    with tenant_session(a["tenant_id"]) as session:
        session.add(_campaign(a["tenant_id"], "Mining Canada"))
    with tenant_session(b["tenant_id"]) as session:
        session.add(_campaign(b["tenant_id"], "ERP Brasil"))

    with tenant_session(a["tenant_id"]) as session:
        nomes = [c.name for c in session.execute(sa.select(Campaign)).scalars()]
    assert nomes == ["Mining Canada"]

    with tenant_session(b["tenant_id"]) as session:
        nomes = [c.name for c in session.execute(sa.select(Campaign)).scalars()]
    assert nomes == ["ERP Brasil"]


def test_query_sem_filtro_de_tenant_ainda_assim_e_filtrada(make_tenant):
    """Mesmo um SELECT cru, sem WHERE tenant_id, só enxerga o tenant da sessão."""
    a = make_tenant()
    b = make_tenant()
    for t in (a, b):
        with tenant_session(t["tenant_id"]) as session:
            session.add(_campaign(t["tenant_id"], f"camp-{t['slug']}"))

    with tenant_session(a["tenant_id"]) as session:
        rows = session.execute(sa.text("SELECT tenant_id FROM campaigns")).all()
    assert [r[0] for r in rows] == [a["tenant_id"]]


def test_nao_da_para_ler_campanha_de_outro_tenant_por_id(make_tenant):
    a = make_tenant()
    b = make_tenant()
    with tenant_session(b["tenant_id"]) as session:
        campaign = _campaign(b["tenant_id"], "Segredo")
        session.add(campaign)
        session.flush()
        campaign_id = campaign.id

    with tenant_session(a["tenant_id"]) as session:
        assert session.get(Campaign, campaign_id) is None


def test_nao_da_para_gravar_linha_com_tenant_de_outro(make_tenant):
    """WITH CHECK: escrever para fora do próprio tenant é erro, não silêncio."""
    a = make_tenant()
    b = make_tenant()
    with pytest.raises(sa.exc.ProgrammingError) as exc:
        with tenant_session(a["tenant_id"]) as session:
            session.add(_campaign(b["tenant_id"], "Invasora"))
            session.flush()
    assert "row-level security" in str(exc.value).lower()


def test_update_nao_atravessa_a_fronteira(make_tenant):
    a = make_tenant()
    b = make_tenant()
    with tenant_session(b["tenant_id"]) as session:
        session.add(_campaign(b["tenant_id"], "Intocada"))

    with tenant_session(a["tenant_id"]) as session:
        result = session.execute(sa.text("UPDATE campaigns SET name = 'hackeada'"))
        assert result.rowcount == 0

    with tenant_session(b["tenant_id"]) as session:
        nomes = [c.name for c in session.execute(sa.select(Campaign)).scalars()]
    assert nomes == ["Intocada"]


def test_delete_nao_atravessa_a_fronteira(make_tenant):
    a = make_tenant()
    b = make_tenant()
    with tenant_session(b["tenant_id"]) as session:
        session.add(_campaign(b["tenant_id"], "Preservada"))

    with tenant_session(a["tenant_id"]) as session:
        assert session.execute(sa.text("DELETE FROM campaigns")).rowcount == 0

    with tenant_session(b["tenant_id"]) as session:
        assert session.execute(sa.select(sa.func.count(Campaign.id))).scalar_one() == 1


def test_sessao_sem_tenant_nao_le_nada(make_tenant):
    """Esquecer de abrir a sessão com escopo não vaza dado: simplesmente não retorna nada."""
    a = make_tenant()
    with tenant_session(a["tenant_id"]) as session:
        session.add(_campaign(a["tenant_id"], "Qualquer"))

    with SessionFactory() as session:  # sem set_config, sem bypass
        assert session.execute(sa.select(sa.func.count(Campaign.id))).scalar_one() == 0


def test_escopo_sobrevive_a_commit_no_meio_da_sessao(make_tenant):
    """`SET LOCAL` morre com a transação — o escopo precisa ser reaplicado.

    Sem isso, commitar no meio de um trabalho abre a transação seguinte sem
    `app.tenant_id`, e dali em diante as consultas voltam vazias. É uma falha
    silenciosa das piores: o RLS fecha em vez de abrir, então parece que o
    dado sumiu.
    """
    a = make_tenant()
    with tenant_session(a["tenant_id"]) as session:
        session.add(_campaign(a["tenant_id"], "Antes do commit"))
        session.commit()

        assert session.execute(sa.select(sa.func.count(Campaign.id))).scalar_one() == 1
        session.add(_campaign(a["tenant_id"], "Depois do commit"))
        session.flush()
        assert session.execute(sa.select(sa.func.count(Campaign.id))).scalar_one() == 2


def test_conexao_da_aplicacao_nao_pode_ignorar_rls():
    """O teste que faltava.

    Um role com SUPERUSER ou BYPASSRLS ignora Row Level Security por completo —
    inclusive FORCE — e a aplicação continua funcionando, servindo dados de
    todos os tenants para todo mundo, sem erro nenhum. A imagem oficial do
    PostgreSQL cria o POSTGRES_USER exatamente assim.
    """
    with SessionFactory() as session:
        rolname, rolsuper, rolbypassrls = session.execute(
            sa.text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    assert not rolsuper, f"role de aplicação '{rolname}' é superusuário e ignora o RLS"
    assert not rolbypassrls, f"role de aplicação '{rolname}' tem BYPASSRLS"


def test_variavel_de_sessao_nao_abre_bypass(make_tenant):
    """Atravessar o isolamento é atributo do role, não valor que se define em SQL."""
    a = make_tenant()
    with tenant_session(a["tenant_id"]) as session:
        session.add(_campaign(a["tenant_id"], "Reservada"))

    with SessionFactory() as session:
        session.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', true)"))
        assert session.execute(sa.select(sa.func.count(Campaign.id))).scalar_one() == 0


def test_startup_recusa_role_privilegiado(monkeypatch):
    """A aplicação não sobe com uma configuração que anula o isolamento."""
    from app.db import session as session_module

    monkeypatch.setattr(
        session_module,
        "_role_privileges",
        lambda target: ("postgres", True, True),
    )
    with pytest.raises(session_module.InsecureDatabaseRole) as exc:
        session_module.verify_database_roles()
    assert "SUPERUSER" in str(exc.value)


def test_bypass_explicito_enxerga_tudo(make_tenant):
    """A saída de emergência existe, é explícita, separada e exige justificativa."""
    a = make_tenant()
    b = make_tenant()
    for t in (a, b):
        with tenant_session(t["tenant_id"]) as session:
            session.add(_campaign(t["tenant_id"], f"c-{t['slug']}"))

    with unscoped_session(reason="test:platform-admin") as session:
        assert session.execute(sa.select(sa.func.count(Campaign.id))).scalar_one() == 2


def test_todas_as_tabelas_de_cliente_tem_politica():
    from app.db.models import TENANT_SCOPED_TABLES

    with unscoped_session(reason="test:inspect-policies") as session:
        rows = (
            session.execute(
                sa.text("SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")
            )
            .scalars()
            .all()
        )
        forced = (
            session.execute(
                sa.text("SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity")
            )
            .scalars()
            .all()
        )

    assert set(TENANT_SCOPED_TABLES) <= set(rows)
    assert set(TENANT_SCOPED_TABLES) <= set(forced)


def test_nenhuma_tabela_com_tenant_id_fica_fora_do_rls():
    """A lista de tabelas com RLS é mantida à mão — então a suíte a confere.

    O teste anterior provava um lado: toda tabela **da lista** tem política.
    Faltava o outro, que é o que quebra na prática: um modelo novo com
    `tenant_id` que ninguém lembrou de acrescentar à lista nasce sem política,
    sem erro e sem teste vermelho. Foi assim que `memberships` passou seis
    migrations carregando `tenant_id` e nenhuma proteção.
    """
    from app.db.models import TENANT_SCOPED_TABLES, Base

    com_tenant = {t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.columns}
    fora = sorted(com_tenant - set(TENANT_SCOPED_TABLES))
    assert fora == [], (
        "tabelas com tenant_id fora do RLS: "
        f"{fora}. Acrescente à lista e escreva a migration da política."
    )
    # E o contrário: nome na lista que não existe mais ou não tem tenant_id
    # deixaria a verificação do outro teste passando por vazio.
    sobrando = sorted(set(TENANT_SCOPED_TABLES) - com_tenant)
    assert sobrando == [], f"nomes na lista que não são tabelas com tenant_id: {sobrando}"
