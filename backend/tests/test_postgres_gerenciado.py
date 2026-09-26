"""Postgres gerenciado: o arranjo que Render, Neon, Supabase e RDS permitem.

Nenhum provedor gerenciado dá superusuário, e `CREATEROLE` **não** concede
`BYPASSRLS` — só um superusuário concede. Isso elimina o arranjo que a aplicação
usava até aqui, em que a parte administrativa (autenticação, painel da
plataforma, worker) atravessa o Row Level Security por **atributo** do role.

O que sobra é o caminho que o próprio PostgreSQL oferece: o **dono** da tabela
atravessa as políticas — a menos que a tabela esteja com `FORCE ROW LEVEL
SECURITY`, que sujeita até ele. Então em banco gerenciado o dono é o role
administrativo, as tabelas ficam sem FORCE, e a aplicação usa um role separado
que não é dono de nada.

O que estes testes precisam provar, em ordem de importância:

1. **O isolamento entre empresas continua valendo.** É a promessa central da
   plataforma, e a que ficaria plausível — e falsa — se alguém trocasse FORCE por
   nada sem verificar. FORCE nunca valeu para quem não é dono, e é isso que aqui
   se demonstra em vez de se afirmar.
2. **A parte administrativa funciona.** Sem isso, login e painel retornam vazio
   silenciosamente, que é a pior falha possível: parece bug de dado.
3. **Apontar a aplicação para o dono é recusado.** Era o que FORCE protegia; a
   proteção passa a ser esta verificação explícita.

O banco é montado do zero em cada execução deste arquivo, com um dono que tem
exatamente os privilégios de um Postgres gerenciado: `CREATEROLE`, sem
`SUPERUSER`, sem `BYPASSRLS`.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.db.session import InsecureDatabaseRole, verify_database_roles

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: O superusuário só é usado para montar o cenário — é o papel que, num provedor
#: gerenciado, a equipe do provedor cumpre antes de entregar o banco. No CI, o
#: usuário da imagem do Postgres é superusuário e serve; numa máquina onde ele
#: não é (o caso deste repositório em desenvolvimento), aponte
#: `TEST_SUPERUSER_URL` para um que seja.
SUPERUSUARIO = os.environ.get(
    "TEST_SUPERUSER_URL",
    os.environ.get(
        "TEST_DATABASE_ADMIN_URL", "postgresql+psycopg://ia_sdr:ia_sdr@localhost:5432/ia_sdr_test"
    ),
)

DONO = "dono_gerenciado_teste"
SENHA_DONO = "dono-de-teste"
APP = "app_gerenciado_teste"
SENHA_APP = "app-de-teste"
BANCO = "ia_sdr_gerenciado_teste"


def _url(usuario: str, senha: str, banco: str = BANCO) -> str:
    """URL com a senha **legível**.

    `str(URL)` do SQLAlchemy mascara a senha como `***` — proteção contra senha
    em log que, aqui, produzia "password authentication failed" sem explicar
    nada. `render_as_string(hide_password=False)` é o caminho explícito.
    """
    base = sa.engine.make_url(SUPERUSUARIO)
    return base.set(username=usuario, password=senha, database=banco).render_as_string(
        hide_password=False
    )


def _superusuario_disponivel() -> bool:
    """Montar o cenário exige superusuário; sem ele, os testes se pulam."""
    try:
        motor = sa.create_engine(
            sa.engine.make_url(SUPERUSUARIO).set(database="postgres"),
            isolation_level="AUTOCOMMIT",
        )
        with motor.connect() as conn:
            return bool(
                conn.execute(sa.text("SELECT current_setting('is_superuser') = 'on'")).scalar()
            )
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            motor.dispose()
        except Exception:  # noqa: BLE001, S110
            pass


@pytest.fixture(scope="module")
def banco_gerenciado():
    """Um banco cujo dono tem os privilégios de um Postgres gerenciado."""
    if not _superusuario_disponivel():
        pytest.skip("montar o cenário exige superusuário; aponte TEST_SUPERUSER_URL para um")

    motor = sa.create_engine(
        sa.engine.make_url(SUPERUSUARIO).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with motor.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{BANCO}"'))
        for role in (APP, DONO):
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        # Exatamente o que um provedor gerenciado entrega: dono do banco, pode
        # criar role, e não tem como se dar BYPASSRLS.
        conn.execute(
            sa.text(
                f"CREATE ROLE \"{DONO}\" LOGIN PASSWORD '{SENHA_DONO}' "
                "CREATEROLE NOSUPERUSER NOBYPASSRLS"
            )
        )
        conn.execute(sa.text(f'CREATE DATABASE "{BANCO}" OWNER "{DONO}"'))
    motor.dispose()

    yield

    motor = sa.create_engine(
        sa.engine.make_url(SUPERUSUARIO).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with motor.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{BANCO}"'))
        for role in (APP, DONO):
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
    motor.dispose()


@pytest.fixture(scope="module")
def migrado(banco_gerenciado, monkeypatch_modulo):
    """Migrations e bootstrap rodados **pelo dono**, como o provedor obriga."""
    from app.core.config import settings

    monkeypatch_modulo.setattr(settings, "database_admin_url", _url(DONO, SENHA_DONO))
    monkeypatch_modulo.setattr(settings, "database_url", _url(APP, SENHA_APP))

    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", _url(DONO, SENHA_DONO).replace("%", "%%"))
    command.upgrade(cfg, "head")

    from scripts.bootstrap_roles import main as bootstrap

    assert bootstrap() == 0, "o bootstrap precisa funcionar sem superusuário"

    app_engine = sa.create_engine(_url(APP, SENHA_APP), future=True)
    admin_engine = sa.create_engine(_url(DONO, SENHA_DONO), future=True)
    yield app_engine, admin_engine
    app_engine.dispose()
    admin_engine.dispose()


@pytest.fixture(scope="module")
def monkeypatch_modulo():
    """`monkeypatch` tem escopo de função; este cenário é de módulo."""
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_a_aplicacao_sobe_no_modo_posse(migrado):
    """Sem BYPASSRLS em lugar nenhum, e ainda assim de pé."""
    app_engine, admin_engine = migrado
    assert verify_database_roles(app_engine, admin_engine) == "posse"


def test_o_bootstrap_desliga_o_force(migrado):
    """Com FORCE ligado, o próprio dono é barrado: login e painel quebrariam.

    O erro seria `new row violates row-level security policy` na gravação e
    resultado vazio na leitura — sintoma que parece bug de dado, não de
    configuração.
    """
    _, admin_engine = migrado
    with admin_engine.connect() as conn:
        forcadas = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relforcerowsecurity"
            )
        ).scalar_one()
        com_rls = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
            )
        ).scalar_one()
    assert com_rls > 20, "as políticas de RLS continuam ligadas"
    assert forcadas == 0, "FORCE sai, porque sujeitaria o dono às próprias políticas"


def test_o_isolamento_entre_empresas_continua_valendo(migrado):
    """O teste que sustenta tudo: o role da aplicação não enxerga a empresa do lado.

    Se este passar, trocar FORCE por posse não custou isolamento nenhum — que é
    exatamente a afirmação que ninguém deveria aceitar sem prova.
    """
    app_engine, admin_engine = migrado
    empresas = []
    with admin_engine.begin() as conn:
        for nome in ("Alfa", "Beta"):
            sufixo = uuid.uuid4().hex[:6]
            tenant_id = conn.execute(
                sa.text(
                    "INSERT INTO tenants (id, name, slug, plan, subscription_status, settings, "
                    "limit_overrides, is_active, contract_monthly_cents, contract_currency, "
                    "overage_cents_per_unit, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :nome, :slug, 'starter', 'trial', '{}', '{}', "
                    "true, 0, 'BRL', 0, now(), now()) RETURNING id"
                ),
                {"nome": nome, "slug": f"{nome.lower()}-{sufixo}"},
            ).scalar_one()
            empresas.append(tenant_id)

    # Cada empresa grava uma conta-alvo pelo role da aplicação, com o escopo posto.
    for i, tenant_id in enumerate(empresas):
        with app_engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            conn.execute(
                sa.text(
                    "INSERT INTO companies (id, tenant_id, name, attributes, created_at, "
                    "updated_at) VALUES (gen_random_uuid(), :t, :nome, '{}', now(), now())"
                ),
                {"t": tenant_id, "nome": f"Conta da empresa {i}"},
            )

    # A aplicação, dentro de cada empresa, vê só a dela.
    for i, tenant_id in enumerate(empresas):
        with app_engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            nomes = [r[0] for r in conn.execute(sa.text("SELECT name FROM companies")).all()]
        assert nomes == [f"Conta da empresa {i}"], f"vazamento entre empresas: {nomes}"

    # E sem escopo posto não vê nada — o RLS fecha, não abre.
    with app_engine.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM companies")).scalar_one() == 0


def test_a_aplicacao_nao_grava_para_outra_empresa(migrado):
    """Mentir o `tenant_id` no INSERT é recusado pelo banco, não pelo código."""
    app_engine, admin_engine = migrado
    with admin_engine.connect() as conn:
        a, b = [r[0] for r in conn.execute(sa.text("SELECT id FROM tenants ORDER BY name")).all()][
            :2
        ]

    with pytest.raises(sa.exc.DBAPIError) as erro:
        with app_engine.begin() as conn:
            conn.execute(sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(a)})
            conn.execute(
                sa.text(
                    "INSERT INTO companies (id, tenant_id, name, attributes, created_at, "
                    "updated_at) VALUES (gen_random_uuid(), :outro, 'invasora', '{}', now(), now())"
                ),
                {"outro": b},
            )
    assert "row-level security" in str(erro.value)


def test_o_administrativo_enxerga_todas_as_empresas(migrado):
    """É o que autenticação, painel da plataforma e worker precisam.

    Sem isso, o login não acha o usuário e responde "email ou senha inválidos" —
    a mensagem mais enganosa possível para um problema de configuração de banco.
    """
    _, admin_engine = migrado
    with admin_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() >= 2
        assert conn.execute(sa.text("SELECT count(*) FROM companies")).scalar_one() >= 2


def test_apontar_a_aplicacao_para_o_dono_e_recusado(migrado):
    """A proteção que substitui o FORCE.

    Sem FORCE, o dono atravessa as políticas — então a aplicação conectando como
    dono deixaria de ter isolamento nenhum, silenciosamente. A verificação de
    boot recusa, e a mensagem diz o que fazer.
    """
    _, admin_engine = migrado
    with pytest.raises(InsecureDatabaseRole) as erro:
        verify_database_roles(admin_engine, admin_engine)
    assert "dono de" in str(erro.value)
    assert "DATABASE_URL" in str(erro.value)


def test_a_recusa_antiga_continua_para_quem_nao_e_dono_nem_tem_bypassrls(migrado):
    """Um role administrativo que não é dono e não tem BYPASSRLS não serve.

    É o caso de quem rodou as migrations com um usuário e apontou
    `DATABASE_ADMIN_URL` para outro: tudo parece configurado e a autenticação
    devolve vazio.
    """
    app_engine, _ = migrado
    with pytest.raises(InsecureDatabaseRole) as erro:
        # O role da aplicação no lugar do administrativo: não é dono de nada.
        verify_database_roles(app_engine, app_engine)
    assert "dono" in str(erro.value)
