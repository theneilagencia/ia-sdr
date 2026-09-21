"""AI Sales Workforce — API.

O MVP tem um único papel (SDR), mas a arquitetura é de plataforma de agentes
multiempresa: identidade, Company Brain, políticas, integrações e governança
já são por tenant.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_exception_handlers
from app.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.api.v1 import (
    admin,
    agents,
    auth,
    campaigns,
    company_brain,
    health,
    integrations,
    tenants,
)
from app.core.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Sales Workforce API",
        version="0.1.0",
        description="Plataforma multiempresa de agentes comerciais de IA",
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
    for module in (auth, tenants, campaigns, company_brain, agents, integrations, admin):
        v1.include_router(module.router)
    app.include_router(v1)

    return app


app = create_app()
