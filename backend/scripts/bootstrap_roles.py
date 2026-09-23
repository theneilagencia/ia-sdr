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
