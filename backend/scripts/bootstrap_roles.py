"""Cria o role de aplicação e concede a ele o mínimo necessário.

Roda como o role administrativo, depois das migrations, e é idempotente —
pode rodar em toda subida.

O nome e a senha do role saem da própria `DATABASE_URL` da aplicação: uma
fonte de verdade só, em vez de variáveis que podem divergir.

    python -m scripts.bootstrap_roles
"""

from __future__ import annotations

import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import settings

#: A aplicação precisa ler e escrever dados. Só isso: nada de DDL, nada de
#: TRUNCATE, nada de criar role.
TABLE_PRIVILEGES = sql.SQL("SELECT, INSERT, UPDATE, DELETE")


def _tem_bypassrls(conn) -> bool:
    return bool(
        conn.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()[0]
    )


def _e_dono(conn, tabela: str) -> bool:
    """Se o role atual é dono daquela tabela do schema `public`.

    Com o filtro de schema e de tipo, e não só `WHERE relname = %s`: `pg_class`
    guarda índices, sequências e catálogos, e `relname` não é único entre
    schemas — a primeira versão disto respondia sobre a linha errada e acusava
    uma tabela "de outro dono" que não existia.
    """
    return bool(
        conn.execute(
            "SELECT pg_get_userbyid(c.relowner) = current_user "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = %s",
            (tabela,),
        ).fetchone()[0]
    )


def _connect(url) -> psycopg.Connection:
    return psycopg.connect(
        host=url.host or "localhost",
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname=url.database,
        autocommit=True,
    )


def main() -> int:
    app_url = make_url(settings.database_url)
    admin_url = make_url(settings.effective_admin_url)

    role = app_url.username
    password = app_url.password
    database = app_url.database

    if not role or not password:
        print("DATABASE_URL precisa conter usuário e senha do role de aplicação")
        return 1
    if role == admin_url.username:
        print(
            f"DATABASE_URL e DATABASE_ADMIN_URL usam o mesmo role ('{role}'). "
            "O role de aplicação precisa ser separado e sem BYPASSRLS."
        )
        return 1

    role_id = sql.Identifier(role)

    with _connect(admin_url) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(role_id))
            print(f"✓ role '{role}' criado")
        else:
            print(f"· role '{role}' já existe")

        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(role_id, sql.Literal(password))
        )

        # NOSUPERUSER/NOBYPASSRLS é o ponto todo: sem isso o RLS não vale nada.
        # Só um superusuário pode mexer nesses atributos; um administrador com
        # apenas CREATEROLE cria o role já sem eles, que é o padrão. Nos dois
        # casos a verificação no fim é a palavra final.
        admin_is_superuser = conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]
        if admin_is_superuser:
            conn.execute(
                sql.SQL("ALTER ROLE {} WITH NOSUPERUSER NOBYPASSRLS NOCREATEROLE").format(role_id)
            )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), role_id)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role_id))
        conn.execute(
            sql.SQL("GRANT {} ON ALL TABLES IN SCHEMA public TO {}").format(
                TABLE_PRIVILEGES, role_id
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role_id)
        )
        # Tabelas que as próximas migrations criarem já nascem acessíveis.
        conn.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {} ON TABLES TO {}").format(
                TABLE_PRIVILEGES, role_id
            )
        )
        conn.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(role_id)
        )
        print(f"✓ privilégios concedidos a '{role}' em {database}")

        # --------------------------------------------------- FORCE ou não FORCE
        #
        # `FORCE ROW LEVEL SECURITY` sujeita **o dono da tabela** às políticas.
        # Em servidor próprio isso é a rede de proteção que se quer: nem o dono
        # escapa, então apontar a aplicação para o role errado não vaza nada.
        #
        # Em Postgres gerenciado (Render, Neon, Supabase, RDS, Cloud SQL) não há
        # superusuário e `CREATEROLE` não concede BYPASSRLS — só superusuário
        # concede. O único caminho que sobra para a parte administrativa
        # (autenticação, painel da plataforma, worker) atravessar o RLS é ser
        # dona das tabelas. Com FORCE ligado, ela é barrada: um INSERT do próprio
        # dono devolve "new row violates row-level security policy". Então, nesse
        # caso, FORCE sai — e o isolamento do role da aplicação não muda em nada,
        # porque FORCE nunca valeu para quem não é dono.
        tabelas = [
            linha[0]
            for linha in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
            ).fetchall()
        ]
        if admin_is_superuser or _tem_bypassrls(conn):
            for tabela in tabelas:
                conn.execute(
                    sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(
                        sql.Identifier(tabela)
                    )
                )
            print(f"✓ FORCE ROW LEVEL SECURITY em {len(tabelas)} tabela(s): nem o dono escapa")
        else:
            nao_proprias = [t for t in tabelas if not _e_dono(conn, t)]
            if nao_proprias:
                print(
                    f"✗ o role administrativo não tem BYPASSRLS nem é dono de "
                    f"{len(nao_proprias)} tabela(s) com RLS. Rode as migrations com o "
                    "usuário que o provedor criou (o dono do banco) e tente de novo."
                )
                return 1
            for tabela in tabelas:
                conn.execute(
                    sql.SQL("ALTER TABLE {} NO FORCE ROW LEVEL SECURITY").format(
                        sql.Identifier(tabela)
                    )
                )
            print(
                f"· Postgres gerenciado: FORCE desligado em {len(tabelas)} tabela(s). "
                "A parte administrativa atravessa o RLS por ser dona das tabelas — "
                "é o único caminho sem superusuário. O role da aplicação continua "
                "isolado do mesmo jeito: FORCE nunca valeu para quem não é dono."
            )

        rolsuper, rolbypassrls = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if rolsuper or rolbypassrls:
            atributo = "SUPERUSER" if rolsuper else "BYPASSRLS"
            print(
                f"✗ '{role}' tem {atributo} e ignoraria o Row Level Security. "
                f'Como superusuário: ALTER ROLE "{role}" NOSUPERUSER NOBYPASSRLS;'
            )
            return 1

    print("✓ isolamento por RLS garantido para o role de aplicação")
    return 0


if __name__ == "__main__":
    sys.exit(main())
