"""Configuração da aplicação.

Todas as credenciais vivem no backend. Nada de chave de cliente no frontend.

**Segredo pode vir de arquivo**, e não só de variável de ambiente: para qualquer
campo, `<NOME>_FILE` aponta um caminho e o conteúdo do arquivo passa a valer.
Isso é o que torna a aplicação compatível com gerenciador de segredo externo sem
depender de fornecedor nenhum — Vault com agente, AWS Secrets Manager via ECS,
Kubernetes Secret montado, Docker Swarm secret e systemd credentials todos
entregam segredo do mesmo jeito: um arquivo no disco, com permissão apertada.

Por que isso é melhor do que variável de ambiente, em produção: o ambiente de um
processo aparece em `docker inspect`, no `/proc/<pid>/environ` de quem estiver na
máquina, e é herdado por todo subprocesso — inclusive pelo que sobe num crash
handler. Arquivo tem dono, modo e caminho, e o vazamento por descuido é menos
provável. O que ele não resolve, e nenhum vault resolve: a chave precisa estar
legível pela aplicação em algum momento.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: Sufixo da variável que aponta arquivo em vez de valor. `JWT_SECRET_FILE`
#: vale sobre `JWT_SECRET`: quem montou o segredo num arquivo quis usá-lo.
SUFIXO_ARQUIVO = "_FILE"


def _valores_de_arquivo(campos: set[str]) -> dict[str, str]:
    """Lê `<NOME>_FILE` do ambiente, para os campos que existem, e devolve `{NOME: conteúdo}`.

    Só os campos desta configuração, e não toda variável terminada em `_FILE`:
    a primeira versão varria o ambiente inteiro e morria porque **outra**
    ferramenta exportava um `..._FILE=1`. Uma aplicação que não sobe por causa de
    variável alheia é péssima vizinha, e o erro não teria nada a ver com a causa.

    Para os campos que são nossos, falha com estrondo quando o arquivo não existe
    ou está vazio. O silêncio aqui seria pior do que a exceção: a aplicação
    subiria com o valor padrão — `change-me-in-production` — e um segredo de
    desenvolvimento em produção não quebra nada na hora, só deixa qualquer pessoa
    assinar token válido para qualquer empresa.

    O caso simétrico, um `JWT_SECRT_FILE` com o nome digitado errado, passa em
    branco aqui de propósito: quem protege contra ele é a verificação de boot, que
    recusa produção com segredo de exemplo — e ela protege contra a variável
    esquecida também, que este módulo não teria como perceber.
    """
    achados: dict[str, str] = {}
    for chave, caminho in os.environ.items():
        if not chave.endswith(SUFIXO_ARQUIVO) or not caminho:
            continue
        nome = chave[: -len(SUFIXO_ARQUIVO)]
        if nome.lower() not in campos:
            continue
        arquivo = Path(caminho)
        if not arquivo.is_file():
            raise RuntimeError(
                f"{chave} aponta para {caminho}, que não existe ou não é arquivo. "
                "Sem isso a aplicação subiria com o valor padrão — que é o de "
                "desenvolvimento, publicado no repositório."
            )
        # `strip`: quem gera segredo com `echo` deixa um \n no fim, e um segredo
        # com quebra de linha invisível vira "a senha está errada e não sei por quê".
        conteudo = arquivo.read_text(encoding="utf-8").strip()
        if not conteudo:
            raise RuntimeError(f"{chave} aponta para {caminho}, que está vazio.")
        achados[nome] = conteudo
    return achados


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

    # Rate limiting: janela deslizante por cliente.
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60

    #: Vazio: o freio conta dentro do processo, o que basta para uma réplica.
    #: Preenchido (`redis://redis:6379/0`), o balde passa a ser um só para todas
    #: as réplicas — sem isso, duas réplicas fazem o teto anunciado valer o
    #: dobro, cada uma contando o seu. Se o Redis cair, o freio volta a ser por
    #: processo e a API avisa no log; não abre e não derruba.
    redis_url: str = ""

    #: Onde a API responde para o mundo. É a base do link de descadastro que
    #: vai em todo email: se estiver errada, o link não abre.
    public_base_url: str = "http://localhost:8000"

    #: Onde o **web app** responde. É a base do link de convite, que abre uma
    #: tela, não um endpoint. Em produção é o mesmo domínio da API — um domínio
    #: só, dividido por caminho pelo proxy —, mas em desenvolvimento são portas
    #: diferentes, e um link de convite apontando para a API daria 404.
    app_base_url: str = "http://localhost:3000"

    #: Chaves Fernet **anteriores**, separadas por vírgula. Existem para que
    #: rotacionar a chave não signifique perder tudo o que já está cifrado: a
    #: primeira chave cifra, e qualquer uma da lista decifra. Depois de rodar
    #: `scripts/rotacionar_chave.py`, esta variável volta a ficar vazia.
    #:
    #: Sem isto, a única resposta a "a chave vazou" era "perca toda credencial de
    #: email e chave de API que as empresas já salvaram" — o que na prática
    #: significa não rotacionar nunca.
    secrets_encryption_keys_previous: str = ""

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @property
    def previous_encryption_keys(self) -> list[str]:
        """As chaves antigas, já sem espaço nem entrada vazia."""
        return [k.strip() for k in self.secrets_encryption_keys_previous.split(",") if k.strip()]

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
    """As configurações, com os segredos de arquivo aplicados por cima.

    A leitura acontece aqui, e não numa fonte customizada do pydantic-settings,
    porque assim o caminho é um só e óbvio: quem for ler este arquivo vê a ordem
    de precedência sem precisar conhecer a API de fontes da biblioteca. E sem
    imprimir valor nenhum no log — só o nome do campo que veio de arquivo.
    """
    de_arquivo = _valores_de_arquivo(set(Settings.model_fields))
    if de_arquivo:
        logger.info("Configuração lida de arquivo para: %s", ", ".join(sorted(de_arquivo)))
    return Settings(**{nome.lower(): valor for nome, valor in de_arquivo.items()})


settings = get_settings()
