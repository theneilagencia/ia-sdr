"""Fixtures dos testes.

Os testes rodam contra um PostgreSQL de verdade, porque metade do que se está
verificando aqui — Row Level Security — não existe em outro banco.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ia_sdr:ia_sdr@localhost:5432/ia_sdr_test"
)
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["SECRETS_ENCRYPTION_KEY"] = "test-encryption-key"
os.environ["RATE_LIMIT_REQUESTS"] = "10000"

import sqlalchemy as sa  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from alembic import command  # noqa: E402
from app.db.models import TENANT_SCOPED_TABLES  # noqa: E402
from app.db.session import SessionFactory, engine, tenant_session, unscoped_session  # noqa: E402
from app.main import app  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_database() -> None:
    url = sa.engine.make_url(TEST_DB_URL)
    admin_url = url.set(database="postgres")
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    _ensure_database()
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database):
    yield
    tables = ", ".join(("tenants", "users", "memberships", *TENANT_SCOPED_TABLES))
    with SessionFactory() as session:
        session.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def make_tenant():
    """Cria tenant + owner e devolve os dados prontos para usar."""

    def _make(name: str | None = None, *, plan: str = "starter") -> dict:
        suffix = uuid.uuid4().hex[:8]
        name = name or f"Empresa {suffix}"
        from app.core.security import hash_password
        from app.db.models.platform import Membership, Tenant, User

        with unscoped_session(reason="test:setup") as session:
            tenant = Tenant(name=name, slug=f"tenant-{suffix}", plan=plan)
            user = User(
                email=f"owner-{suffix}@example.com",
                password_hash=hash_password("senha-de-teste-123"),
                full_name="Owner",
            )
            session.add_all([tenant, user])
            session.flush()
            session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="owner"))
            session.flush()
            return {
                "tenant_id": tenant.id,
                "slug": tenant.slug,
                "user_id": user.id,
                "email": user.email,
                "password": "senha-de-teste-123",
            }

    return _make


@pytest.fixture
def auth_headers(client):
    def _headers(email: str, password: str) -> dict:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _headers


@pytest.fixture
def db_for():
    """Sessão já escopada em um tenant, como a aplicação usa."""
    return tenant_session
