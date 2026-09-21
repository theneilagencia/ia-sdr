# AI Sales Workforce

Plataforma multiempresa de agentes comerciais de IA. No MVP a força de trabalho
tem um papel — **SDR** — mas a arquitetura é de plataforma de agentes: cada
empresa cliente entra em um tenant isolado, com seus próprios dados, seu
conhecimento, suas credenciais e suas políticas. O mesmo motor atende todos.

```
   1.284 prospects → 842 researched → 611 contacted
        → 47 engaged → 19 qualified → 11 meetings
```

## Estado atual

**Sprint 1 (Foundation) implementado e testado**: autenticação, tenants,
RBAC, PostgreSQL com Row Level Security, contexto de tenant, Company Brain,
orquestrador de agentes, medição de consumo, limites de plano, auditoria,
criptografia de credenciais e painel de plataforma. 21 endpoints, 22 tabelas,
45 testes contra PostgreSQL de verdade.

A execução dos agentes tem a fronteira pronta e testada (isolamento, cota,
registro, consumo), com a chamada ao modelo como ponto de extensão — é o
Sprint 3. Não há web app ainda: só a API.

Veja [`docs/05-roadmap.md`](docs/05-roadmap.md) para o que vem a seguir.

## Rodando

Com Docker:

```bash
cp backend/.env.example backend/.env
docker compose up --build
# API em http://localhost:8000/docs
```

Sem Docker (precisa de um PostgreSQL 14+ acessível):

```bash
cd backend
uv venv .venv && uv pip install -e ".[dev]"
cp .env.example .env            # ajuste DATABASE_URL
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_demo    # dois tenants de exemplo
.venv/bin/uvicorn app.main:app --reload
```

Atalhos no `Makefile`: `make install`, `make migrate`, `make seed`, `make run`,
`make test`, `make lint`.

### Se o `docker compose up` falhar

**`Bind for 0.0.0.0:5432 failed: port is already allocated`** — você já tem um
Postgres ocupando a porta. O compose publica o banco em **5433** por padrão
justamente por isso; se a 5433 também estiver ocupada, escolha outra:

    DB_PORT=5434 docker compose up --build

A API não depende dessa porta — ela fala com o banco pela rede interna do
compose. A porta só existe para você abrir um cliente SQL a partir do host.
`API_PORT` funciona do mesmo jeito para a API.

**`failed to resolve host 'db'`** — sobrou container de uma subida que falhou
no meio, sem rede. Limpe e suba de novo:

    docker compose down --remove-orphans
    docker compose up --build

**`Cannot connect to the Docker daemon`** — o daemon não está rodando
(`colima start`, ou abra o Docker Desktop).

## Testes

```bash
make test        # ou: cd backend && .venv/bin/pytest
```

Os testes rodam contra PostgreSQL de verdade, porque metade do que eles
verificam — Row Level Security — não existe em outro banco. Aponte
`TEST_DATABASE_URL` para onde quiser; o banco é criado e migrado sozinho.

O que está coberto:

- `test_rls.py` — isolamento no banco: leitura, escrita, update e delete
  cruzados; sessão sem escopo; política presente em todas as tabelas
- `test_api_isolation.py` — isolamento pela API, token forjado, credencial que
  não vaza na resposta, auditoria
- `test_rbac.py` — matriz de papéis e enforcement na rota
- `test_orchestrator.py` — contexto de IA só com dado do próprio tenant,
  envelope, cota e registro de execução
- `test_usage_and_limits.py` — consumo, limites de plano e criptografia

## Estrutura

```
backend/
  app/
    api/            rotas HTTP, dependências, middleware
    billing/        planos e limites
    core/           config, segurança, criptografia, erros
    db/             modelos e sessões com escopo de tenant
    orchestrator/   envelope, contexto, agentes, runner
    rbac/           papéis e permissões
    services/       auditoria, consumo, limites
    tenancy/        contexto de tenant
  alembic/          migrations (inclui as políticas de RLS)
  scripts/          seed de demonstração
  tests/
docs/               arquitetura e decisões
```

## Documentação

- [Arquitetura](docs/01-arquitetura.md)
- [Multi-tenancy: as quatro camadas](docs/02-multi-tenancy.md)
- [Modelo de dados](docs/03-modelo-de-dados.md)
- [AI Orchestrator e Company Brain](docs/04-ai-orchestrator.md)
- [Roadmap](docs/05-roadmap.md)

## Duas regras que não se negociam

1. **Credencial de cliente não vai para o frontend.** Fica no backend, cifrada,
   e nunca é serializada em resposta de API.
2. **Dado de cliente não sai do tenant.** O banco recusa por RLS, e o
   orquestrador confere de novo antes de montar qualquer contexto de IA.
