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

    #: Assina os links de descadastro. **Não rotacione**: o link não expira de
    #: propósito — descadastro de dois anos atrás continua valendo — e trocar
    #: este segredo mata todo link já enviado, transformando uma obrigação legal
    #: numa página de erro. Vazio herda o `jwt_secret`, que é como as instalações
    #: existentes já assinavam; a variável existe para que rotacionar o segredo
    #: dos tokens de sessão (o que se faz depois de um vazamento) não leve os
    #: links de descadastro junto.
    unsubscribe_secret: str | None = None

    # IA. A chave é da plataforma e vive só no backend. Sem ela, os agentes
    # continuam registrando execução e consumo, mas com o executor de eco —
    # nenhuma chamada de modelo acontece.
    anthropic_api_key: str | None = None
    #: Deixar um tenant sem chave usar a chave da plataforma significa pagar
    #: a conta dele. Desligado por padrão: a empresa configura a própria.
    ai_platform_key_fallback: bool = False
    ai_model_default: str = "claude-opus-5"
    ai_effort: str = "high"  # low | medium | high | xhigh | max
    ai_max_output_tokens: int = 8000
    ai_web_search: bool = True
    ai_max_web_searches: int = 8
    ai_timeout_seconds: float = 600.0
    ai_max_retries: int = 2
    #: Teto de segurança por execução, em micro-dólares. Uma pesquisa que
    #: custar mais que isso é registrada como falha, não cobrada em silêncio.
    ai_max_cost_micro_usd: int = 500_000  # US$ 0,50

    # Rate limiting (janela deslizante em memória; use Redis em produção)
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60

    #: Onde a API responde para o mundo. É a base do link de descadastro que
    #: vai em todo email: se estiver errada, o link não abre.
    public_base_url: str = "http://localhost:8000"

    #: Onde o **web app** responde. É a base do link de convite, que abre uma
    #: tela, não um endpoint. Em produção é o mesmo domínio da API — um domínio
    #: só, dividido por caminho pelo proxy —, mas em desenvolvimento são portas
    #: diferentes, e um link de convite apontando para a API daria 404.
    app_base_url: str = "http://localhost:3000"

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
