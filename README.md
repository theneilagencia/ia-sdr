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
criptografia de credenciais e painel de plataforma. 65 endpoints, 23 tabelas,
164 testes contra PostgreSQL de verdade.

**Sprint 2 em andamento**: o Research Agent chama o modelo de verdade — saída
estruturada e validada, busca na web, retomada de turno pausado, teto de custo
por execução e custo real por token em cada evento de consumo. Em volta dele,
o funil já funciona: contas-alvo, import de prospects com deduplicação e cota,
pontuação contra o ICP com o cálculo aberto, e os números da tela inicial em
`GET /api/v1/prospects/funnel`. O **Outreach Agent** escreve a primeira
abordagem ancorada na pesquisa — e para no rascunho: disparar email escrito
por IA sem ninguém ter lido o primeiro é como se queima um domínio. O
**Conversation Agent** responde quando o lead responde — e escala para humano
quando a resposta não está na base, quando o assunto é preço ou jurídico, ou
quando ele próprio recusa. E o **Qualification Agent** avalia o lead critério
a critério contra a campanha — com dois freios no código, não no prompt:
critério cumprido sem evidência é rebaixado, e confiança baixa não vira
"qualificado". Os quatro agentes do MVP estão ligados.

O **web app** (`web/`, Next.js) já mostra o funil, a fila de revisão com
aprovar e recusar, os prospects e as campanhas. O token fica num cookie
httpOnly e todas as chamadas à API saem do servidor do Next: nenhuma
credencial chega ao browser.

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
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.bootstrap_roles
.venv/bin/python -m scripts.seed_demo
.venv/bin/uvicorn app.main:app --reload
```

Cada empresa configura a própria chave da Anthropic e a própria conta de email
na tela de **Configurações** — escrita para quem não sabe o que é SMTP nem API
key. As duas são testadas antes de salvar, ficam cifradas no banco e nunca
voltam para a tela: a chave aparece como `…1234`, a senha não aparece.

Isso significa que o consumo de IA cai na conta da Anthropic de cada cliente.
Se você preferir operar com uma chave da plataforma e revender tokens, ligue
`AI_PLATFORM_KEY_FALLBACK=true` no `backend/.env` — o código suporta os dois
modelos sem mudança.

O `.env` tem **duas** URLs de banco, e a diferença entre elas é o que sustenta
o isolamento: `DATABASE_ADMIN_URL` (dono das tabelas, com `BYPASSRLS`, usado
por migrations e autenticação) e `DATABASE_URL` (role da aplicação, sem
privilégio nenhum de ignorar RLS). O `bootstrap_roles` cria o segundo a partir
do primeiro. A API recusa subir se essa separação não estiver de pé.

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

**A API não sobe e reclama de `SUPERUSER`/`BYPASSRLS`** — é proposital.
`DATABASE_URL` está apontando para um role que ignora Row Level Security, o que
faria os tenants enxergarem os dados uns dos outros. Rode
`python -m scripts.bootstrap_roles` e aponte `DATABASE_URL` para o role de
aplicação, deixando o administrativo em `DATABASE_ADMIN_URL`.

## Testes

```bash
make test        # backend: cd backend && .venv/bin/pytest
cd web && npm run e2e   # com API e web no ar
```

Os testes rodam contra PostgreSQL de verdade, porque metade do que eles
verificam — Row Level Security — não existe em outro banco. Aponte
`TEST_DATABASE_URL` para onde quiser; o banco é criado e migrado sozinho.

O que está coberto:

- `test_rls.py` — isolamento no banco: leitura, escrita, update e delete
  cruzados; sessão sem escopo; política presente em todas as tabelas; e o
  privilégio do role com que a aplicação conecta, que é o que decide se tudo
  isso vale alguma coisa
- `test_api_isolation.py` — isolamento pela API, token forjado, credencial que
  não vaza na resposta, auditoria
- `test_rbac.py` — matriz de papéis e enforcement na rota
- `test_orchestrator.py` — contexto de IA só com dado do próprio tenant,
  envelope, cota e registro de execução
- `test_usage_and_limits.py` — consumo, limites de plano e criptografia
- `test_research_agent.py` — o agente real com cliente falso: retomada de turno
  pausado, teto de custo, persistência, custo por token e recusa de alvo de
  outro tenant
- `test_prospects_and_scoring.py` — import com deduplicação e cota, pontuação,
  e o funil que não empurra ninguém para trás
- `test_outreach_agent.py` — o rascunho que nasce rascunho, e os quatro
  motivos para não escrever: sem pesquisa, descadastro, campanha pausada,
  teto diário
- `test_conversation_agent.py` — escalonamento para humano, descadastro
  obedecido no ato e recusa do modelo tratada como sinal, não como erro
- `test_qualification_agent.py` — os freios contra falso positivo: evidência
  obrigatória, piso de confiança, e campanha sem critérios que não qualifica
- `test_review_queue.py` — o portão humano: aprovar, recusar com motivo, e
  quem não pode aprovar
- `web/e2e/smoke.mjs` — browser de verdade: login, funil e aprovação. Pega o
  que build e typecheck não pegam, como Server Action que compila e falha ao
  executar

## Estrutura

```
web/                  Next.js: funil, revisão e envio, prospects, configurações
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
    workers/        o worker que faz o ciclo rodar sem ninguém olhando
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
2. **Dado de cliente não sai do tenant.** O banco recusa por RLS — com um role
   que não tem como ignorá-lo — e o orquestrador confere de novo antes de
   montar qualquer contexto de IA.
