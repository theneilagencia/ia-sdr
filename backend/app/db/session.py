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


def verify_database_roles() -> None:
    """Recusa subir com uma configuração que desliga o RLS sem avisar.

    Este é o alarme que faltava: a imagem oficial do PostgreSQL cria o
    `POSTGRES_USER` como superusuário, e superusuário ignora RLS por completo —
    inclusive `FORCE ROW LEVEL SECURITY`. Uma aplicação apontada para esse role
    funciona perfeitamente e serve dados de todos os tenants para todo mundo.
    """
    app_role, app_super, app_bypass = _role_privileges(engine)
    if app_super or app_bypass:
        raise InsecureDatabaseRole(
            f"O role de aplicação '{app_role}' tem "
            f"{'SUPERUSER' if app_super else 'BYPASSRLS'} e ignoraria o Row Level "
            "Security: os tenants enxergariam os dados uns dos outros. Rode "
            "`python -m scripts.bootstrap_roles` e aponte DATABASE_URL para o "
            "role de aplicação, deixando o role administrativo em "
            "DATABASE_ADMIN_URL."
        )

    admin_role, admin_super, admin_bypass = _role_privileges(admin_engine)
    if not (admin_super or admin_bypass):
        raise InsecureDatabaseRole(
            f"O role administrativo '{admin_role}' não tem BYPASSRLS. "
            "Autenticação e painel de plataforma precisam enxergar mais de um "
            "tenant e retornariam vazio silenciosamente. Rode "
            "`ALTER ROLE {admin} BYPASSRLS;` como superusuário.".format(admin=admin_role)
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
