"""Sessões de banco com escopo de tenant.

Regra da casa: nenhuma query de dados de cliente roda sem `app.tenant_id`
definido na transação. O RLS do PostgreSQL depende disso e, sem a variável,
as políticas simplesmente não retornam linha alguma.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
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

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

TENANT_GUC = "app.tenant_id"
BYPASS_GUC = "app.bypass_rls"


def _set_local(session: Session, key: str, value: str) -> None:
    session.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value})


@contextmanager
def tenant_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    """Sessão restrita a um tenant. É o caminho normal da aplicação."""
    session = SessionFactory()
    try:
        _set_local(session, TENANT_GUC, str(tenant_id))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def unscoped_session(*, reason: str) -> Iterator[Session]:
    """Sessão sem filtro de tenant — atravessa o RLS.

    Usada só em três lugares: autenticação (antes de existir tenant ativo),
    painel de Platform Admin e migrations/manutenção. O `reason` é obrigatório
    para que o motivo apareça nos logs e no code review.
    """
    session = SessionFactory()
    try:
        _set_local(session, BYPASS_GUC, "on")
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
