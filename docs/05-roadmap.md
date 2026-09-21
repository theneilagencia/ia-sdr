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

## Sprint 2 — Sales Intelligence

- Importação e enriquecimento de prospects (CSV e provedores)
- Research Agent com execução real e fontes citadas
- Scoring contra o ICP da campanha, com justificativa
- Editor do Company Brain no web app
- Limite de prospects/mês ligado ao `usage_events`

## Sprint 3 — AI SDR

- Executor real dos agentes (`register_executor`) com chamada de modelo
- Fila e workers consumindo `JobEnvelope` (o payload já é o contrato)
- Ingestão da Knowledge Base: chunking, embeddings, busca (migrar
  `knowledge_chunks.embedding` para `pgvector`)
- Integração de email (OAuth Gmail/Outlook e SMTP), envio e recebimento
- Sequências e limites diários por campanha

## Sprint 4 — Conversion

- Qualificação automática contra os critérios da campanha
- Calendário e agendamento de reuniões
- Integração de CRM (HubSpot, Salesforce, Pipedrive)
- Dashboard do funil e de consumo
- Billing: assinatura, cobrança por uso e faturas

## Transversal (quando a operação exigir)

- Web app em Next.js — hoje só existe a API
- Secrets manager externo
- Rate limiting distribuído (Redis)
- Política de retenção e exportação de dados por tenant
- SSO / MFA para contratos enterprise
