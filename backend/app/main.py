"""AI Sales Workforce — API.

O MVP tem um único papel (SDR), mas a arquitetura é de plataforma de agentes
multiempresa: identidade, Company Brain, políticas, integrações e governança
já são por tenant.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_exception_handlers
from app.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.api.v1 import (
    admin,
    agents,
    auth,
    campaigns,
    companies,
    company_brain,
    contacts,
    conversations,
    health,
    integrations,
    knowledge,
    messages,
    prospects,
    public,
    sequences,
    tenants,
)
from app.api.v1 import (
    settings as settings_router,
)
from app.core.config import settings
from app.core.startup import verify_production_secrets
from app.db.session import verify_database_roles
from app.orchestrator.executors import register_default_executors

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Antes de aceitar a primeira requisição: o role da aplicação consegue
    # mesmo ser filtrado pelo RLS? Se não, é melhor não subir do que servir
    # dados de todos os tenants para todo mundo.
    verify_database_roles()
    # E os segredos são de verdade, ou sobraram do .env.example?
    verify_production_secrets()
    register_default_executors()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Sales Workforce API",
        version="0.1.0",
        description="Plataforma multiempresa de agentes comerciais de IA",
        lifespan=lifespan,
    )

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)

    app.include_router(health.router)

    v1 = APIRouter(prefix="/api/v1")
    for module in (
        auth,
        tenants,
        campaigns,
        companies,
        company_brain,
        contacts,
        conversations,
        prospects,
        sequences,
        messages,
        settings_router,
        public,
        agents,
        integrations,
        knowledge,
        admin,
    ):
        v1.include_router(module.router)
    app.include_router(v1)

    return app


app = create_app()
