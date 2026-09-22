#!/usr/bin/env bash
#
# Publica a aplicação num servidor com Docker.
#
#   ./publicar.sh app.suaempresa.com voce@suaempresa.com
#
# Na primeira vez gera o .env com senhas e chaves aleatórias; nas seguintes
# reaproveita o que já existe e só reconstrói as imagens. Rodar de novo é
# seguro: é assim que se atualiza a aplicação depois de um `git pull`.
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

erro() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
info() { printf '· %s\n' "$*"; }

command -v docker >/dev/null || erro "Docker não está instalado. Veja https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || erro "O plugin 'docker compose' não está disponível."

# Segredo aleatório em base64 urlsafe. 32 bytes assim geram exatamente o
# formato que o Fernet espera, então a mesma função serve para as duas chaves.
segredo() { openssl rand -base64 "${1:-32}" | tr '+/' '-_' | tr -d '\n'; }

if [ ! -f .env ]; then
  DOMINIO="${1:-}"
  EMAIL="${2:-}"
  [ -n "$DOMINIO" ] || erro "Uso: ./publicar.sh <dominio> <email-para-o-certificado>"
  [ -n "$EMAIL" ] || erro "Uso: ./publicar.sh <dominio> <email-para-o-certificado>"

  info "Gerando .env com senhas e chaves novas"
  umask 077
  cat > .env <<ENV
# Gerado por publicar.sh em $(date -u '+%Y-%m-%d %H:%M UTC'). NÃO versione.
# O SECRETS_ENCRYPTION_KEY é a única forma de decifrar as credenciais que as
# empresas salvarem. Guarde um backup deste arquivo fora do servidor.
DOMAIN=$DOMINIO
ACME_EMAIL=$EMAIL

DB_NAME=ia_sdr
DB_ADMIN_USER=ia_sdr_admin
DB_ADMIN_PASSWORD=$(segredo 24)
APP_DB_USER=ia_sdr_app
APP_DB_PASSWORD=$(segredo 24)

JWT_SECRET=$(segredo 48)
SECRETS_ENCRYPTION_KEY=$(segredo 32)
# Segredo próprio para os links de descadastro: eles não expiram, e rotacionar o
# JWT_SECRET depois de um vazamento não pode levar consigo todo descadastro já
# enviado.
UNSUBSCRIBE_SECRET=$(segredo 32)

ANTHROPIC_API_KEY=
AI_PLATFORM_KEY_FALLBACK=false
ENV
  chmod 600 .env
  ok ".env criado — faça um backup dele fora deste servidor"
else
  ok ".env já existe, reaproveitando as chaves"
  if [ -n "${1:-}" ]; then
    info "Domínio em uso é o do .env; para trocar, edite DOMAIN lá."
  fi
fi

# O .env é gerado, não versionado — o shellcheck não tem como segui-lo.
set -a
# shellcheck disable=SC1091
. ./.env
set +a
[ -n "${SECRETS_ENCRYPTION_KEY:-}" ] || erro ".env sem SECRETS_ENCRYPTION_KEY. Apague o .env e rode de novo."

info "Construindo as imagens (a primeira vez demora alguns minutos)"
$COMPOSE build

info "Subindo banco, API, worker, web e proxy"
$COMPOSE up -d

printf '· Esperando a API ficar pronta'
for _ in $(seq 1 60); do
  estado="$($COMPOSE ps --format '{{.Health}}' api 2>/dev/null | head -1)"
  [ "$estado" = "healthy" ] && break
  printf '.'
  sleep 3
done
printf '\n'

if [ "${estado:-}" != "healthy" ]; then
  printf '\033[31m✗ a API não ficou pronta. Os últimos logs:\033[0m\n' >&2
  $COMPOSE logs --tail 40 api >&2
  erro "Corrija o que o log aponta e rode ./publicar.sh de novo."
fi

ok "aplicação de pé em https://${DOMAIN}"
echo
echo "Falta uma coisa só: criar a primeira empresa e o usuário que entra nela."
echo
echo "  $COMPOSE exec api python -m scripts.criar_empresa \\"
echo "      --nome \"Sua Empresa\" --email voce@suaempresa.com"
echo
echo "A senha é impressa uma vez. Depois de entrar, vá em Configurações e"
echo "cadastre a chave da Anthropic e a conta de email desta empresa."
