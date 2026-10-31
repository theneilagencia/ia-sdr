"""Sessões de banco com escopo de tenant.

Duas conexões, e a diferença entre elas é o que sustenta o isolamento:

* `engine` — role da aplicação, **sem** superusuário e **sem** BYPASSRLS. Toda
  query de dado de cliente passa por aqui, dentro de uma transação que define
  `app.tenant_id`. O PostgreSQL filtra o resto.
* `admin_engine` — role administrativo, com BYPASSRLS. Usado só por migrations,
  bootstrap e pelos três pontos que precisam enxergar mais de um tenant:
  autenticação, painel de plataforma e manutenção.

Por que não uma variável de sessão para "desligar" o RLS: qualquer SQL
executado na conexão conseguiria ligá-la. O privilégio de atravessar o
isolamento é atributo do role, verificado pelo banco, e não um valor que a
aplicação escolhe em tempo de execução.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import TENANT_SCOPED_TABLES

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.db_echo,
    future=True,
)

#: Pool pequeno: o caminho administrativo é exceção, não regra.
admin_engine = create_engine(
    settings.effective_admin_url,
    pool_size=2,
    max_overflow=3,
    pool_pre_ping=True,
    echo=settings.db_echo,
    future=True,
)

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
AdminSessionFactory = sessionmaker(
    bind=admin_engine, autoflush=False, expire_on_commit=False, future=True
)

TENANT_GUC = "app.tenant_id"


class InsecureDatabaseRole(RuntimeError):
    """A configuração do banco anularia o isolamento entre tenants."""


def _role_privileges(target_engine) -> tuple[str, bool, bool]:
    with target_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    return row[0], bool(row[1]), bool(row[2])


def _posse_das_tabelas(target_engine) -> tuple[int, int, int]:
    """(quantas tabelas com tenant_id existem, quantas o role atual possui, quantas estão FORCE).

    Serve para reconhecer o segundo jeito de um role atravessar o RLS, que é o
    único disponível em Postgres gerenciado: **ser dono da tabela**. O dono
    atravessa as políticas por definição do PostgreSQL — a menos que a tabela
    esteja com `FORCE ROW LEVEL SECURITY`, que sujeita até ele.
    """
    with target_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE pg_get_userbyid(c.relowner) = current_user),
                       count(*) FILTER (WHERE c.relforcerowsecurity)
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public'
                   AND c.relkind = 'r'
                   AND c.relname = ANY(:tabelas)
                """
            ),
            {"tabelas": list(TENANT_SCOPED_TABLES)},
        ).one()
    return int(row[0]), int(row[1]), int(row[2])


def verify_database_roles(app_engine=None, admin: object = None) -> str:
    """Recusa subir com uma configuração que desliga o RLS sem avisar.

    Este é o alarme que faltava: a imagem oficial do PostgreSQL cria o
    `POSTGRES_USER` como superusuário, e superusuário ignora RLS por completo —
    inclusive `FORCE ROW LEVEL SECURITY`. Uma aplicação apontada para esse role
    funciona perfeitamente e serve dados de todos os tenants para todo mundo.

    Há **dois** arranjos válidos, e a diferença não é de gosto: é o que separa
    poder usar ou não um Postgres gerenciado (Render, Neon, Supabase, RDS, Cloud
    SQL).

    * **Por atributo** (servidor próprio): o role administrativo tem SUPERUSER ou
      BYPASSRLS e as tabelas ficam com `FORCE ROW LEVEL SECURITY`. É o mais
      apertado: nem o dono da tabela escapa das políticas, então apontar a
      aplicação para o role errado não vaza nada.
    * **Por posse** (Postgres gerenciado): nenhum provedor gerenciado dá
      superusuário, e `CREATEROLE` **não** concede BYPASSRLS — só um superusuário
      concede. O que sobra é o caminho que o PostgreSQL oferece de origem: o dono
      da tabela atravessa as políticas. Então o role administrativo é o dono, as
      tabelas ficam sem FORCE, e a aplicação usa um role separado que não é dono
      de nada. O isolamento do role da aplicação é **idêntico** nos dois casos —
      FORCE nunca valeu para quem não é dono —, e o que se perde é a rede de
      proteção contra apontar a aplicação para o dono. Ela é substituída pela
      verificação explícita aqui embaixo.

    Devolve qual dos dois arranjos está em vigor, para o log da subida dizer.
    """
    app_engine = app_engine if app_engine is not None else engine
    admin_engine_ = admin if admin is not None else admin_engine

    app_role, app_super, app_bypass = _role_privileges(app_engine)
    if app_super or app_bypass:
        raise InsecureDatabaseRole(
            f"O role de aplicação '{app_role}' tem "
            f"{'SUPERUSER' if app_super else 'BYPASSRLS'} e ignoraria o Row Level "
            "Security: os tenants enxergariam os dados uns dos outros. Rode "
            "`python -m scripts.bootstrap_roles` e aponte DATABASE_URL para o "
            "role de aplicação, deixando o role administrativo em "
            "DATABASE_ADMIN_URL."
        )

    total, do_app, forcadas = _posse_das_tabelas(app_engine)
    if total and do_app:
        # Sem FORCE, o dono atravessa as políticas. Com a aplicação conectando
        # como dono, o isolamento entre empresas simplesmente não existiria — e
        # nada na tela diria isso.
        raise InsecureDatabaseRole(
            f"O role de aplicação '{app_role}' é dono de {do_app} tabela(s) com "
            "tenant_id, e dono de tabela atravessa as políticas de Row Level "
            "Security. Aponte DATABASE_URL para o role criado pelo "
            "`bootstrap_roles` (que não é dono de nada) e deixe o dono do banco "
            "em DATABASE_ADMIN_URL."
        )

    admin_role, admin_super, admin_bypass = _role_privileges(admin_engine_)
    if admin_super or admin_bypass:
        return "atributo"

    _, do_admin, _ = _posse_das_tabelas(admin_engine_)
    if total and do_admin == total and forcadas == 0:
        return "posse"

    if total and do_admin == total and forcadas:
        raise InsecureDatabaseRole(
            f"O role administrativo '{admin_role}' é dono das tabelas mas {forcadas} "
            "delas estão com FORCE ROW LEVEL SECURITY, que sujeita até o dono às "
            "políticas: autenticação, painel de plataforma e worker retornariam "
            "vazio ou falhariam ao gravar. Rode `python -m scripts.bootstrap_roles`, "
            "que ajusta isso conforme os privilégios que este banco permite."
        )

    raise InsecureDatabaseRole(
        f"O role administrativo '{admin_role}' não tem BYPASSRLS nem é dono das "
        f"tabelas ({do_admin} de {total}). Autenticação e painel de plataforma "
        "precisam enxergar mais de um tenant e retornariam vazio silenciosamente. "
        "Num servidor próprio: `ALTER ROLE "
        f'"{admin_role}" BYPASSRLS;` como superusuário. Em Postgres gerenciado: '
        "aponte DATABASE_ADMIN_URL para o usuário que o provedor criou, que é o "
        "dono do banco, e rode as migrations com ele."
    )


@contextmanager
def tenant_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    """Sessão restrita a um tenant. É o caminho normal da aplicação.

    O escopo é reaplicado no início de **cada** transação da sessão, não só na
    primeira. `SET LOCAL` vale até o fim da transação: um `commit()` no meio do
    trabalho abriria a transação seguinte sem `app.tenant_id` e, a partir dali,
    as consultas não retornariam nada — uma falha silenciosa e difícil de ler,
    porque o RLS fecha em vez de abrir. Com o listener, commitar no meio é
    seguro.
    """
    session = SessionFactory()

    @event.listens_for(session, "after_begin")
    def _apply_tenant_scope(session_, transaction, connection) -> None:  # noqa: ARG001
        connection.exec_driver_sql("SELECT set_config(%s, %s, true)", (TENANT_GUC, str(tenant_id)))

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        event.remove(session, "after_begin", _apply_tenant_scope)
        session.close()


@contextmanager
def unscoped_session(*, reason: str) -> Iterator[Session]:
    """Sessão administrativa, que atravessa o RLS.

    Usada só em três lugares: autenticação (antes de existir tenant ativo),
    painel de Platform Admin e manutenção. O `reason` é obrigatório para que o
    motivo apareça nos logs e no code review — `grep unscoped_session` mostra
    toda a superfície de uma vez.
    """
    session = AdminSessionFactory()
    try:
        session.info["rls_bypass_reason"] = reason
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_tenant_of(session: Session) -> uuid.UUID | None:
    value = session.execute(text("SELECT current_setting(:k, true)"), {"k": TENANT_GUC}).scalar()
    return uuid.UUID(value) if value else None
