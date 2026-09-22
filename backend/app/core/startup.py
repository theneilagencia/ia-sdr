"""Verificações que acontecem antes da primeira requisição.

O padrão aqui é o mesmo de `verify_database_roles`: uma configuração que
quebraria o isolamento ou a segurança não deve virar um bug silencioso em
produção — deve impedir o processo de subir.

Por que isso não é paranoia: os defaults do repositório são de
desenvolvimento, e desenvolvimento é onde todo mundo testa. Um deploy que
herda `JWT_SECRET=change-me-in-production` aceita token assinado por qualquer
pessoa que tenha lido este código — o que, num repositório, é qualquer pessoa.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)


class InsecureConfiguration(RuntimeError):
    """A configuração subiria em produção com segredo de exemplo."""


#: Valores que existem no repositório e no `.env.example`. Qualquer um deles em
#: produção significa que o deploy nunca recebeu segredo próprio.
SEGREDOS_DE_EXEMPLO = {
    "change-me-in-production",
    "dev-only-key-not-for-production",
    "ci-secret",
    "ci-encryption-key",
    "",
}

#: 32 bytes de entropia. Abaixo disso a assinatura do JWT fica ao alcance de
#: força bruta com hardware comum.
MIN_SEGREDO = 32


def _e_chave_fernet(valor: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(valor.encode())) == 32
    except Exception:
        return False


def _problemas_de_url(nome: str, valor: str, *, para_que: str, por_que_tls: str) -> list[str]:
    """As duas checagens que todo endereço posto dentro de link precisa passar.

    Separadas em função porque são duas variáveis com o mesmo desenho, e um
    `if` copiado é onde o segundo endereço vira o que ninguém verificou.
    """
    url = urlparse(valor)
    if url.hostname in {"localhost", "127.0.0.1", "0.0.0.0", None}:
        return [f"{nome} aponta para {valor}, que não existe fora do servidor. {para_que}"]
    if url.scheme != "https":
        return [f"{nome} usa {url.scheme}://. {por_que_tls}"]
    return []


def verify_production_secrets() -> None:
    """Recusa subir em produção com segredo de desenvolvimento.

    Fora de produção não faz nada: o atrito de gerar chave para rodar teste
    local é justamente o que faz as pessoas commitarem `.env`.
    """
    if not settings.is_production:
        return

    problemas: list[str] = []

    if settings.jwt_secret in SEGREDOS_DE_EXEMPLO:
        problemas.append(
            "JWT_SECRET está com o valor de exemplo do repositório: qualquer "
            "pessoa consegue assinar um token válido para qualquer empresa. "
            "Gere um novo com `openssl rand -base64 48`."
        )
    elif len(settings.jwt_secret) < MIN_SEGREDO:
        problemas.append(
            f"JWT_SECRET tem {len(settings.jwt_secret)} caracteres; o mínimo é "
            f"{MIN_SEGREDO}. Gere um novo com `openssl rand -base64 48`."
        )

    if settings.secrets_encryption_key in SEGREDOS_DE_EXEMPLO:
        problemas.append(
            "SECRETS_ENCRYPTION_KEY está com o valor de exemplo: as credenciais "
            "de email e as chaves de API das empresas ficariam cifradas com uma "
            'chave publicada. Gere uma nova com `python -c "from '
            'cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.'
        )
    elif not _e_chave_fernet(settings.secrets_encryption_key):
        problemas.append(
            "SECRETS_ENCRYPTION_KEY não é uma chave Fernet válida (32 bytes em "
            "base64 urlsafe). Sem isso, salvar a primeira credencial falha em "
            "tempo de execução, não aqui."
        )

    problemas += _problemas_de_url(
        "PUBLIC_BASE_URL",
        settings.public_base_url,
        para_que="É a base do link de descadastro que vai em todo email: errada, o "
        "link não abre — e email frio sem saída queima o domínio da empresa.",
        por_que_tls="O link de descadastro chega por email para gente de fora; sem "
        "TLS ele vai em claro.",
    )
    # O convite é o segundo link que a plataforma manda para fora, e o mais
    # sensível dos dois: ele é uma credencial de uso único. Apontando para
    # localhost, nenhum convite abre; em `http://`, o token viaja em claro.
    problemas += _problemas_de_url(
        "APP_BASE_URL",
        settings.app_base_url,
        para_que="É a base do link de convite, que abre a tela de aceite: errada, "
        "ninguém consegue entrar na empresa que convidou.",
        por_que_tls="O link de convite carrega o token de aceite; sem TLS ele vai "
        "em claro para quem estiver no caminho.",
    )

    if problemas:
        raise InsecureConfiguration(
            "Configuração insegura para produção:\n- " + "\n- ".join(problemas)
        )

    locais = [o for o in settings.cors_origins if "localhost" in o or "127.0.0.1" in o]
    if locais:
        # Não é fatal: com token em Authorization, o CORS não é a fronteira. Mas
        # origem de desenvolvimento liberada em produção é sinal de .env copiado
        # sem revisar, e o resto do arquivo merece uma olhada.
        logger.warning(
            "CORS_ORIGINS em produção inclui origem local (%s) — provavelmente "
            "sobrou do ambiente de desenvolvimento.",
            ", ".join(locais),
        )
