# Roadmap

## Sprint 1 — Foundation ✅ (este commit)

| Item                                   | Onde                                      |
|----------------------------------------|-------------------------------------------|
| Auth (JWT, bcrypt, troca de tenant)    | `app/api/v1/auth.py`, `app/core/security.py` |
| Tenant + Users + Memberships           | `app/db/models/platform.py`               |
| RBAC (owner/admin/operator/viewer)     | `app/rbac/roles.py`                       |
| PostgreSQL + RLS                       | `alembic/versions/0002_row_level_security.py` |
| Role de aplicação sem bypass de RLS    | `scripts/bootstrap_roles.py`, `app/db/session.py` |
| Contexto de tenant por requisição/job  | `app/tenancy/context.py`, `app/api/deps.py` |
| Platform Admin                         | `app/api/v1/admin.py`                     |
| Audit log                              | `app/services/audit.py`                   |
| Planos, limites e medição de consumo   | `app/billing/plans.py`, `app/services/usage.py` |
| Credenciais cifradas                   | `app/core/crypto.py`                      |
| Rate limiting                          | `app/api/middleware.py`                   |
| Company Brain                          | `app/api/v1/company_brain.py`             |
| AI Orchestrator (fronteira e registro) | `app/orchestrator/`                       |

Modelo de dados completo dos quatro sprints já criado, para não refazer
migration a cada entrega.

## Sprint 2 — Sales Intelligence (em andamento)

Feito:

- **Research Agent com execução real** (`app/orchestrator/executors/research.py`):
  saída estruturada e validada, busca na web como ferramenta de servidor,
  retomada de turno pausado, teto de custo por execução e persistência em
  `research`
- Custo por token, por modelo (`app/ai/pricing.py`), gravado em cada
  `usage_event` — unidades são o que se cobra, micro-dólares são o que se paga
- **Contas-alvo e prospects**: cadastro, import em lote com deduplicação por
  domínio e email, cota mensal do plano aplicada na entrada
- **Scoring contra o ICP** (`app/services/scoring.py`), derivado da pesquisa em
  vez de uma segunda chamada de modelo, com o cálculo aberto para auditoria
- **Funil** (`GET /api/v1/prospects/funnel`): os números da tela inicial,
  cumulativos por estágio
- **Outreach Agent** (`app/orchestrator/executors/outreach.py`): primeira
  abordagem ancorada na pesquisa, gravada como **rascunho**. Recusa escrever
  sem pesquisa da conta, respeita descadastro, campanha pausada e teto diário
- **Conversation Agent** (`app/orchestrator/executors/conversation.py`):
  responde a partir da base de conhecimento do tenant e escala para humano
  quando a resposta não está lá, quando o assunto é preço/jurídico/prazo ou
  quando o modelo recusa. Descadastro é obedecido no ato
- **Qualification Agent** (`app/orchestrator/executors/qualification.py`):
  avalia critério a critério contra a campanha. Critério cumprido sem
  evidência é rebaixado pelo código, e confiança baixa não vira "qualificado"
- **Reunião** (`POST /api/v1/prospects/{id}/meetings`): fecha o funil
- **Fila de revisão**: rascunho só vira mensagem enviável depois que uma
  pessoa aprova; a recusa guarda o motivo
- **Web app** (`web/`, Next.js): funil, fila de revisão com aprovar/recusar,
  prospects e campanhas. Token em cookie httpOnly, chamadas à API feitas pelo
  servidor — nada de credencial no browser

A fazer:

- Enriquecimento de prospects por provedores externos
- Upload de CSV (hoje o import é JSON em lote)
- Editor do Company Brain no web app

## Sprint 3 — AI SDR

- **Envio de verdade**: integração de email (OAuth Gmail/Outlook e SMTP) e o
  passo que transforma rascunho em mensagem enviada — hoje o Outreach Agent
  para no rascunho, de propósito
- Fila e workers consumindo `JobEnvelope` (o payload já é o contrato)
- Ingestão da Knowledge Base: chunking, embeddings, busca (migrar
  `knowledge_chunks.embedding` para `pgvector`)
- Sequências (cadência multi-passo); o teto diário por campanha já existe

## Sprint 4 — Conversion

- Integração de calendário (o agendamento manual já existe e é o mesmo caminho)
- Integração de CRM (HubSpot, Salesforce, Pipedrive)
- Dashboard do funil e de consumo
- Billing: assinatura, cobrança por uso e faturas

## Transversal (quando a operação exigir)

- Telas que faltam no web app: Company Brain, conversas, importação de lista
- Teste de ponta a ponta do web app no CI (hoje validado manualmente com browser)
- Secrets manager externo
- Rate limiting distribuído (Redis)
- Política de retenção e exportação de dados por tenant
- SSO / MFA para contratos enterprise
