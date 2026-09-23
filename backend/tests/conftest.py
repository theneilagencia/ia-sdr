"""Fixtures dos testes.

Os testes rodam contra um PostgreSQL de verdade, porque metade do que se está
verificando aqui — Row Level Security — não existe em outro banco.
"""

from __future__ import annotations

import os
import uuid

import pytest

# Os testes usam a mesma separação de roles da produção: o engine da
# aplicação não pode ignorar RLS, e o administrativo existe para migrations,
# bootstrap e limpeza entre testes. Testar com um role privilegiado esconderia
# exatamente o tipo de falha que esta suíte precisa pegar.
TEST_ADMIN_DB_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL", "postgresql+psycopg://ia_sdr:ia_sdr@localhost:5432/ia_sdr_test"
)
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ia_sdr_app:ia_sdr_app@localhost:5432/ia_sdr_test"
)
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["DATABASE_ADMIN_URL"] = TEST_ADMIN_DB_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["SECRETS_ENCRYPTION_KEY"] = "test-encryption-key"
os.environ["RATE_LIMIT_REQUESTS"] = "10000"

import sqlalchemy as sa  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from alembic import command  # noqa: E402
from app.db.models import TENANT_SCOPED_TABLES  # noqa: E402
from app.db.models.engagement import (  # noqa: E402
    Conversation,
    Message,
    MessageStatus,
)
from app.db.models.sales import Campaign, Company, Contact, Prospect  # noqa: E402
from app.db.session import (  # noqa: E402
    AdminSessionFactory,
    admin_engine,
    engine,
    tenant_session,
    unscoped_session,
    verify_database_roles,
)
from app.main import app  # noqa: E402
from app.services import email_accounts, email_sender  # noqa: E402
from scripts.bootstrap_roles import main as bootstrap_roles  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_database() -> None:
    url = sa.engine.make_url(TEST_ADMIN_DB_URL)
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
    assert bootstrap_roles() == 0, "bootstrap do role de aplicação falhou"
    verify_database_roles()
    yield
    engine.dispose()
    admin_engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database):
    yield
    tables = ", ".join(("tenants", "users", "memberships", *TENANT_SCOPED_TABLES))
    # Limpeza é operação administrativa: o role da aplicação nem tem TRUNCATE.
    with AdminSessionFactory() as session:
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
def membro(client, auth_headers):
    """Põe alguém numa empresa pelo caminho de verdade: convite e aceite.

    Criar membro com senha escolhida por outra pessoa não existe mais — era o
    fluxo que revelava, na resposta, se aquele email já tinha conta na
    plataforma. Os testes que só precisavam de "um operador nesta empresa"
    passam por aqui, e de graça exercitam o fluxo novo inteiro.
    """

    def _membro(
        tenant: dict,
        *,
        role: str = "operator",
        email: str | None = None,
        password: str = "senha-forte-12345",
        full_name: str = "",
    ) -> dict:
        email = email or f"{role}-{uuid.uuid4().hex[:8]}@example.com"
        headers = auth_headers(tenant["email"], tenant["password"])
        convite = client.post(
            "/api/v1/tenants/me/invitations",
            headers=headers,
            json={"email": email, "role": role},
        )
        assert convite.status_code == 201, convite.text
        token = convite.json()["accept_url"].rsplit("/", 1)[-1]

        aceite = client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": token, "password": password, "full_name": full_name},
        )
        assert aceite.status_code == 200, aceite.text

        # O `user_id` sai da listagem porque a resposta do aceite é um token de
        # sessão, não um cadastro: quem acabou de entrar recebe o que precisa
        # para entrar, e nada sobre a estrutura interna da empresa.
        membros = client.get("/api/v1/tenants/me/members", headers=headers).json()
        return {
            "user_id": next(m["user_id"] for m in membros if m["email"] == email),
            "email": email,
            "password": password,
            "role": role,
            "access_token": aceite.json()["access_token"],
        }

    return _membro


@pytest.fixture
def db_for():
    """Sessão já escopada em um tenant, como a aplicação usa."""
    return tenant_session

# ---------------------------------------------------- envio de email de verdade
#
# Estas duas vivem aqui, e não no arquivo de teste de envio, porque o convite de
# calendário usa o mesmo cenário: empresa com conta de email configurada e um
# prospect com contato que tem endereço. Duplicar a montagem faria os dois
# arquivos divergirem no dia em que um campo novo entrasse.


@pytest.fixture
def enviados(monkeypatch):
    """Captura o que sairia pelo SMTP, sem sair."""
    capturados = []
    monkeypatch.setattr(
        email_sender, "_transport", lambda credenciais, mensagem: capturados.append(mensagem)
    )
    return capturados


@pytest.fixture
def pronto_para_enviar(make_tenant):
    """Empresa com conta de email configurada e um rascunho já aprovado."""
    t = make_tenant()
    with tenant_session(t["tenant_id"]) as session:
        email_accounts.store(
            session,
            t["tenant_id"],
            provider="gmail",
            from_email="vendas@apymine.com",
            from_name="Vendas Apy Mine",
            username=None,
            password="senha-de-app",
            host=None,
            port=None,
            created_by=t["user_id"],
        )
        campanha = Campaign(tenant_id=t["tenant_id"], name="Mining Canada", slug="mc")
        empresa = Company(tenant_id=t["tenant_id"], name="Northern Ore")
        session.add_all([campanha, empresa])
        session.flush()
        contato = Contact(
            tenant_id=t["tenant_id"],
            company_id=empresa.id,
            full_name="Alice",
            email="alice@northernore.ca",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=t["tenant_id"],
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="scored",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=t["tenant_id"],
            prospect_id=prospect.id,
            campaign_id=campanha.id,
            subject="Turnos em Sudbury",
        )
        session.add(conversa)
        session.flush()
        mensagem = Message(
            tenant_id=t["tenant_id"],
            conversation_id=conversa.id,
            direction="outbound",
            status=MessageStatus.QUEUED.value,
            subject="Turnos em Sudbury",
            body="Alice, vi as vagas em Sudbury.",
        )
        session.add(mensagem)
        session.flush()
        return {
            **t,
            "message_id": mensagem.id,
            "prospect_id": prospect.id,
            "contact_id": contato.id,
        }
