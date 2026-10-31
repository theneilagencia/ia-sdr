"""A URL do banco como o provedor a entrega, e o role de aplicação por cima.

Postgres gerenciado impõe duas coisas que nenhum servidor próprio impõe:

1. **O esquema é o do libpq** (`postgres://` ou `postgresql://`). É a string que
   o painel mostra e que o arquivo de infraestrutura injeta sozinho na variável
   de ambiente — não há ninguém no meio para editar. O SQLAlchemy recusa
   `postgres://` desde a 1.4.
2. **Existe um usuário só**, e a referência que a infraestrutura consegue passar
   é a URL inteira, sem como recombinar host, porta e banco com outro usuário.
   Esta aplicação precisa de dois roles — é o que faz o Row Level Security valer
   — e o segundo entra por `APP_DB_USER`/`APP_DB_PASSWORD`.

Nenhum destes testes toca o banco: é tratamento de string, e erro aqui aparece
como falha de conexão no primeiro deploy, longe da causa.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings
from scripts.bootstrap_roles import _conninfo

#: O formato que o Render entrega numa conexão interna, e o `sslmode` que a
#: externa carrega. Os dois casos importam: quem publica cola um ou o outro.
INTERNA = "postgres://ia_sdr_admin:s3nha@dpg-abc123-a/ia_sdr"
EXTERNA = (
    "postgresql://ia_sdr_admin:s3nha@dpg-abc123-a.oregon-postgres.render.com:5432/"
    "ia_sdr?sslmode=require"
)


def test_o_esquema_do_provedor_ganha_driver():
    """`postgres://` e `postgresql://` viram `postgresql+psycopg://`."""
    assert Settings(database_url=INTERNA).database_url.startswith("postgresql+psycopg://")
    assert Settings(database_url=EXTERNA).database_url.startswith("postgresql+psycopg://")


def test_o_que_vem_depois_da_interrogacao_sobrevive():
    """`sslmode=require` é obrigatório na conexão externa de todo provedor.

    Perder esse parâmetro no caminho dá "server does not support SSL" — erro que
    não menciona nem a variável nem quem a reescreveu.
    """
    assert Settings(database_url=EXTERNA).database_url.endswith("?sslmode=require")


def test_uma_url_que_ja_traz_driver_passa_intacta():
    """Quem foi explícito quis aquilo — inclusive um driver que não é o nosso."""
    url = "postgresql+asyncpg://u:p@host/db"
    assert Settings(database_url=url).database_url == url


def test_a_url_administrativa_tambem_e_normalizada():
    """Ela é a que roda migrations e bootstrap; sem normalizar, nada sobe."""
    s = Settings(database_url=INTERNA, database_admin_url=EXTERNA)
    assert s.database_admin_url.startswith("postgresql+psycopg://")
    assert s.effective_admin_url == s.database_admin_url


def test_o_role_de_aplicacao_entra_por_cima_do_endereco_do_provedor():
    """Host, porta e banco do provedor; usuário e senha do role sem privilégio."""
    s = Settings(
        database_url=EXTERNA,
        database_admin_url=EXTERNA,
        app_db_user="ia_sdr_app",
        app_db_password="outra-senha",
    )
    app = make_url(s.database_url)
    admin = make_url(s.effective_admin_url)

    assert app.username == "ia_sdr_app"
    assert app.password == "outra-senha"
    assert (app.host, app.port, app.database) == (admin.host, admin.port, admin.database)
    assert app.query == admin.query, "o sslmode vale para as duas conexões"
    # O ponto todo: são dois roles diferentes. Iguais, o bootstrap recusa.
    assert app.username != admin.username


def test_senha_com_caractere_especial_sobrevive_a_ida_e_volta():
    """Senha gerada por provedor tem `/`, `+` e `=`; sem escapar, a URL quebra.

    E quebra do pior jeito: `senha/com@arroba` faz o parser ler outro host, e o
    erro fala de um servidor que ninguém configurou.
    """
    senha = "a/b+c=d:e@f"
    s = Settings(database_url=INTERNA, app_db_user="ia_sdr_app", app_db_password=senha)
    assert make_url(s.database_url).password == senha


def test_meia_configuracao_e_recusada():
    """Só `APP_DB_USER` significaria conectar com a senha do administrativo.

    O que não daria erro: daria acesso. A aplicação subiria com o role que
    atravessa o Row Level Security, e o isolamento entre empresas passaria a
    depender de ninguém ter esquecido uma variável.
    """
    for parcial in ({"app_db_user": "ia_sdr_app"}, {"app_db_password": "x"}):
        with pytest.raises(ValidationError) as erro:
            Settings(database_url=INTERNA, **parcial)
        assert "andam juntos" in str(erro.value)


def test_sem_as_variaveis_do_provedor_nada_muda():
    """Servidor próprio escreve a DATABASE_URL inteira, e ela chega intacta."""
    url = "postgresql+psycopg://ia_sdr_app:app@db:5432/ia_sdr"
    assert Settings(database_url=url).database_url == url


def test_o_conninfo_do_bootstrap_preserva_o_sslmode():
    """O bootstrap conecta com psycopg puro, que não entende `+psycopg`.

    A primeira versão montava a conexão a partir de host/porta/usuário/banco e
    deixava o `sslmode` de fora — em provedor gerenciado, a única coisa que não
    pode faltar.
    """
    conninfo = _conninfo(make_url(EXTERNA.replace("postgresql://", "postgresql+psycopg://")))
    assert conninfo.startswith("postgresql://")
    assert "sslmode=require" in conninfo
