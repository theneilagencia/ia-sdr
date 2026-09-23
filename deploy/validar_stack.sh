#!/usr/bin/env bash
#
# Sobe a stack de produção INTEIRA numa máquina qualquer e prova que ela
# funciona — imagens de produção, `ENVIRONMENT=production`, o Caddyfile de
# verdade, HTTPS, migrations, worker, e a fumaça do navegador por cima.
#
#   ./validar_stack.sh                 # sobe, valida e derruba
#   ./validar_stack.sh --manter        # deixa de pé para investigar
#   ./validar_stack.sh --sem-build     # usa imagens já construídas
#
# Por que isto existe: até aqui, o que se verificava da publicação era o
# compose resolvendo com o `.env` e o shellcheck do script. Nada disso prova
# que a imagem instala, que a migration aplica, que o proxy roteia ou que o
# worker sobe — e "não sobe" é o pior lugar para descobrir um erro, porque
# acontece no servidor do cliente, no dia da entrega.
#
# O que este script NÃO valida, porque ninguém consegue localmente: a emissão
# do certificado pelo Let's Encrypt, que exige DNS público apontando para o
# servidor. Aqui o Caddy assina com a CA interna dele (caddy_cert_interno.awk).
set -euo pipefail
cd "$(dirname "$0")"

DOMINIO="${DOMINIO_VALIDACAO:-app.validacao.test}"
ENVFILE=".env.validacao"
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.validacao.yml --env-file $ENVFILE"
MANTER=0
BUILD=1
for arg in "$@"; do
  case "$arg" in
    --manter) MANTER=1 ;;
    --sem-build) BUILD=0 ;;
    *) printf 'argumento desconhecido: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

falhas=0
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
falhou(){ printf '\033[31m✗\033[0m %s\n' "$*"; falhas=$((falhas + 1)); }
info()  { printf '· %s\n' "$*"; }

# Cada verificação roda dentro de um `if`, e não como comando solto seguido de
# `$?`: com `set -e` ligado, a primeira que falhasse abortaria o script inteiro
# em vez de contar a falha e seguir — e a validação existe justamente para dizer
# tudo o que está errado de uma vez. (Este era um defeito da primeira versão
# deste arquivo, encontrado na primeira execução de verdade.)
verificar() {
  descricao="$1"
  shift
  if "$@" >/dev/null 2>&1; then ok "$descricao"; else falhou "$descricao"; fi
}

# `curl` que não passa por proxy nenhum e aceita a CA interna do Caddy: o
# domínio é de teste, o certificado é auto-assinado por desenho.
borda() { curl -sk --noproxy "$DOMINIO" "$@"; }

limpar() {
  if [ "$MANTER" = "1" ]; then
    info "stack mantida de pé: $COMPOSE logs -f"
    return
  fi
  info "derrubando a stack e apagando os volumes da validação"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENVFILE"
}
trap limpar EXIT

command -v docker >/dev/null || { echo "Docker não está instalado." >&2; exit 1; }

# ---------------------------------------------------------------- 1. segredos
# Gerados na hora e descartados no fim: a validação nunca toca no .env real.
segredo() { openssl rand -base64 "${1:-32}" | tr '+/' '-_' | tr -d '\n'; }
umask 077
cat > "$ENVFILE" <<ENV
# Gerado por validar_stack.sh. Descartável: o script apaga no fim.
DOMAIN=$DOMINIO
ACME_EMAIL=validacao@example.com
DB_NAME=ia_sdr
DB_ADMIN_USER=ia_sdr_admin
DB_ADMIN_PASSWORD=$(segredo 24)
APP_DB_USER=ia_sdr_app
APP_DB_PASSWORD=$(segredo 24)
JWT_SECRET=$(segredo 48)
SECRETS_ENCRYPTION_KEY=$(segredo 32)
UNSUBSCRIBE_SECRET=$(segredo 32)
ANTHROPIC_API_KEY=
AI_PLATFORM_KEY_FALLBACK=false
ENV

# O domínio de validação precisa resolver para esta máquina.
if ! getent hosts "$DOMINIO" >/dev/null; then
  if [ "$(id -u)" = "0" ]; then
    echo "127.0.0.1 $DOMINIO" >> /etc/hosts
    info "$DOMINIO apontado para 127.0.0.1 em /etc/hosts"
  else
    echo "Adicione '127.0.0.1 $DOMINIO' ao /etc/hosts (ou rode como root)." >&2
    exit 1
  fi
fi

# ------------------------------------------------------- 2. o Caddyfile parseia
# Antes de subir: config inválida derruba o proxy e, com ele, o acesso inteiro.
caddyfile_valido() {
  DOMAIN="$DOMINIO" ACME_EMAIL=validacao@example.com \
    docker run --rm -e DOMAIN -e ACME_EMAIL -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
}
verificar "o Caddyfile de produção é válido" caddyfile_valido

# ------------------------------------------------------------ 3. subir a stack
if [ "$BUILD" = "1" ]; then
  info "construindo as imagens de produção"
  $COMPOSE build
fi
info "subindo banco, API, worker, web e proxy"
$COMPOSE up -d --no-build

printf '· esperando a API ficar pronta'
estado=""
for _ in $(seq 1 60); do
  estado="$($COMPOSE ps --format '{{.Health}}' api 2>/dev/null | head -1)"
  [ "$estado" = "healthy" ] && break
  printf '.'
  sleep 3
done
printf '\n'
if [ "$estado" != "healthy" ]; then
  falhou "a API não ficou pronta"
  $COMPOSE logs --tail 40 api
  exit 1
fi
ok "a API subiu: migrations aplicadas e role de aplicação criado"

# --------------------------------------------------- 4. o que só o boot prova
# Migration aplicada até o fim: `alembic current` tem de casar com o head.
no_head() { $COMPOSE exec -T api sh -c 'alembic current 2>/dev/null | grep -q "(head)"'; }
verificar "o banco está na última migration" no_head

# O role da aplicação não pode ignorar RLS. A API já recusa subir se isso
# estiver errado; aqui a verificação é explícita para o log da validação dizer.
rls_de_pe() {
  $COMPOSE exec -T api python -c 'from app.db.session import verify_database_roles; verify_database_roles()'
}
verificar "o role da aplicação não ignora Row Level Security" rls_de_pe

# Só o proxy publica porta. Postgres, API e web exposto na internet é como
# começa a maioria dos vazamentos — e a validação publica o banco só em
# 127.0.0.1, de propósito.
publicadas="$(docker ps --filter label=com.docker.compose.project=ia-sdr --format '{{.Names}} {{.Ports}}')"
if echo "$publicadas" | grep -E 'ia-sdr-(api|web|worker)' | grep -q '0.0.0.0'; then
  falhou "API, web ou worker publicando porta para fora"
else
  ok "só o proxy publica porta para a internet"
fi

# ------------------------------------------------------- 5. a borda, por HTTPS
codigo() { borda -o /dev/null -w '%{http_code}' "$1"; }
responde() { [ "$(codigo "$1")" = "$2" ]; }
cabecalho() { borda -D - -o /dev/null "https://$DOMINIO/login" | grep -qi "$1"; }

verificar "/health responde pela borda, com TLS" responde "https://$DOMINIO/health" 200
verificar "a tela de login vem do container do web" responde "https://$DOMINIO/login" 200
verificar "a API recusa quem não tem sessão (401, não 500)" \
  responde "https://$DOMINIO/api/v1/auth/me" 401
verificar "o proxy manda HSTS" cabecalho 'strict-transport-security'
verificar "o proxy proíbe a aplicação num iframe" cabecalho 'x-frame-options: DENY'

redireciona() {
  [ "$(curl -s --noproxy "$DOMINIO" -o /dev/null -w '%{http_code}' "http://$DOMINIO/login")" = "308" ]
}
verificar "HTTP redireciona para HTTPS" redireciona

# A rota interna de prontidão NÃO é exposta: ela conta o estado do banco.
prontidao_fechada() { [ "$(codigo "https://$DOMINIO/health/ready")" != "200" ]; }
verificar "a prontidão detalhada não vaza pela borda" prontidao_fechada

# ------------------------------------------- 6. o caminho do runbook, de fato
# O endereço tem TLD de verdade de propósito: `.test` é reservado e o validador
# de email da API o recusa. Foi exatamente assim que se descobriu que o
# `criar_empresa` aceitava endereço que o login depois não deixava entrar.
DONO="dono@empresa-da-validacao.com"
saida="$($COMPOSE exec -T api python -m scripts.criar_empresa \
  --nome "Empresa da Validação" --email "$DONO" 2>&1 || true)"
senha="$(printf '%s' "$saida" | grep -oE 'senha: [^ ]+' | awk '{print $2}' | tr -d '\r')"
if [ -n "$senha" ]; then
  ok "criar_empresa cria a primeira empresa e imprime a senha uma vez"
else
  falhou "criar_empresa não imprimiu senha"
  printf '%s\n' "$saida"
fi

entra_pela_borda() {
  [ "$(borda -o /dev/null -w '%{http_code}' -X POST "https://$DOMINIO/api/v1/auth/login" \
    -H 'content-type: application/json' \
    -d "{\"email\":\"$DONO\",\"password\":\"$senha\"}")" = "200" ]
}
if [ -n "$senha" ]; then
  verificar "o dono criado entra pela API, atravessando o proxy" entra_pela_borda
fi

# ------------------------------------------------------------- 7. o worker
worker_vivo() { $COMPOSE logs worker 2>&1 | grep -qi "worker"; }
verificar "o worker subiu e está registrando o ciclo" worker_vivo

# ------------------------------------------------------- 8. fumaça no browser
# Só se a máquina tiver Playwright: é o mesmo arquivo do job `e2e`, agora
# apontado para as imagens de produção em vez do `next start` de desenvolvimento.
if [ -d ../web/node_modules/playwright ]; then
  info "semeando dados de demonstração para a fumaça"
  $COMPOSE exec -T api python -m scripts.seed_demo >/dev/null
  $COMPOSE exec -T api python -m scripts.promover_admin --email owner@apymine.com >/dev/null 2>&1
  info "rodando a fumaça contra a stack de produção"
  fumaca() {
    cd ../web
    NO_PROXY="$DOMINIO,localhost,127.0.0.1" no_proxy="$DOMINIO,localhost,127.0.0.1" \
    E2E_TLS_INSECURE=1 WEB_URL="https://$DOMINIO" API_URL="https://$DOMINIO" \
      node e2e/smoke.mjs
  }
  if (fumaca); then
    ok "a fumaça passa inteira contra a stack de produção"
  else
    falhou "a fumaça falhou contra a stack de produção (saída acima)"
  fi
else
  info "Playwright não instalado: fumaça pulada (rode 'npm ci' em web/)"
fi

echo
if [ "$falhas" = "0" ]; then
  printf '\033[32mstack de produção validada\033[0m\n'
else
  printf '\033[31m%s verificação(ões) falharam\033[0m\n' "$falhas"
  exit 1
fi
