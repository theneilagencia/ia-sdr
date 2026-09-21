"""Configuração da aplicação.

Todas as credenciais vivem no backend. Nada de chave de cliente no frontend.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    environment: str = "development"
    debug: bool = False

    # Banco — duas conexões, de propósito.
    #
    # database_url: o role da APLICAÇÃO. Precisa ser NOSUPERUSER e NOBYPASSRLS,
    #   senão o Row Level Security é simplesmente ignorado e o isolamento entre
    #   tenants deixa de existir. A aplicação recusa subir se esse role tiver
    #   privilégio demais (app/db/session.py::verify_database_roles).
    #
    # database_admin_url: o role ADMINISTRATIVO (dono das tabelas, com
    #   BYPASSRLS). Serve para migrations, bootstrap e para os três pontos que
    #   precisam atravessar o RLS: autenticação, painel de plataforma e
    #   manutenção.
    database_url: str = "postgresql+psycopg://ia_sdr_app:ia_sdr_app@localhost:5432/ia_sdr"
    database_admin_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12

    # Segredos de integração (Fernet key, base64 urlsafe de 32 bytes).
    # Em produção, vem de um secrets manager.
    secrets_encryption_key: str = "dev-only-key-not-for-production"

    # Rate limiting (janela deslizante em memória; use Redis em produção)
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @property
    def effective_admin_url(self) -> str:
        """Sem URL administrativa configurada, cai na de aplicação.

        Em desenvolvimento isso é conveniente; a checagem de privilégio no
        startup avisa se a configuração ficar inconsistente.
        """
        return self.database_admin_url or self.database_url

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
