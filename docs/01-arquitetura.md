# Arquitetura

## O que estamos construindo

Não é um disparador de emails. É uma **plataforma multiempresa de agentes
comerciais de IA** — uma *AI Sales Workforce*. No MVP essa força de trabalho
tem um único papel, SDR, mas identidade, Company Brain, políticas, integrações,
memória operacional e governança já são por tenant.

```
                    AI SALES WORKFORCE
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
     TENANT A          TENANT B          TENANT C
     Empresa A         Empresa B         Empresa C
        │                 │                 │
    Campaigns         Campaigns         Campaigns
    Contacts          Contacts          Contacts
    Agents            Agents            Agents
    Knowledge         Knowledge         Knowledge
    Conversations     Conversations     Conversations
    Meetings          Meetings          Meetings
```

O mesmo motor atende todos. Nenhum dado, configuração, conhecimento ou
credencial de uma empresa alcança outra.

## Hierarquia

```
Platform
   │
   ├── Tenant
   │      ├── Users
   │      ├── Campaigns
   │      ├── Leads / Prospects
   │      ├── Contacts
   │      ├── Conversations
   │      ├── Meetings
   │      ├── Knowledge Base
   │      ├── AI Agents
   │      ├── Integrations
   │      └── Settings
   │
   └── Platform Admin
```

O **Tenant** é a empresa cliente (`Tenant: Apy Mine`, `tnt_001`). Tudo que
pertence ao cliente carrega `tenant_id` — a regra vale para as 19 tabelas
listadas em `backend/app/db/models/__init__.py::TENANT_SCOPED_TABLES`.

## Camadas

```
                         ┌───────────────────┐
                         │      WEB APP      │
                         │     Next.js       │   (a construir)
                         └─────────┬─────────┘
                                   │
                              Auth / JWT
                                   │
                                   ▼
                         ┌───────────────────┐
                         │    API LAYER      │   app/main.py
                         │     FastAPI       │   app/api/v1/*
                         └─────────┬─────────┘
                                   │
                         ┌─────────▼─────────┐
                         │ TENANT CONTEXT    │   app/api/deps.py
                         │                   │   app/tenancy/context.py
                         │ tenant_id         │
                         │ user_id           │
                         │ role/permissions  │
                         └─────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        Campaign Engine       AI Orchestrator      Integrations
        app/api/v1/           app/orchestrator/    app/api/v1/
        campaigns.py          runner.py            integrations.py
              │                    │                    │
              ▼                    ▼                    ▼
          PostgreSQL          Agent Workers        Email / CRM
           + RLS                   │
                       ┌───────────┼───────────┐
                       ▼           ▼           ▼
                    Research   Outreach   Qualification
```

## Decisões que valem registrar

**Um banco, RLS, não um banco por cliente.** Um banco por tenant multiplica
migrations, conexões e custo de operação por número de clientes — caro cedo
demais. PostgreSQL com Row Level Security dá isolamento verificável no próprio
banco, e a migração para banco dedicado (cliente enterprise que exija) continua
possível depois, porque `tenant_id` já está em toda linha.

**O `tenant_id` nunca vem do frontend.** Ele sai do token, é revalidado contra
`memberships` a cada requisição (`app/api/deps.py`) e vira a variável
`app.tenant_id` da transação. O banco faz o resto.

**A sessão sem escopo existe, mas é explícita.** `unscoped_session(reason=...)`
atravessa o RLS e exige um motivo escrito. Hoje ela aparece em três lugares:
autenticação, painel de plataforma e manutenção. É fácil auditar num code
review — `grep unscoped_session`.

**Todo trabalho assíncrono tem envelope.** `JobEnvelope` carrega `tenant_id`,
`job_id`, `user_id` e `campaign_id`. Um worker não tem como "herdar" o tenant
do job anterior, porque o contexto é derivado do envelope, não do processo.

**Contabilidade de execução em transação própria.** Se uma execução de agente
falha e o trabalho de domínio é desfeito, o registro de que ela aconteceu — e
quanto custou — permanece. Falha de agente não pode virar buraco no histórico
nem na fatura.

## Documentos relacionados

- [Multi-tenancy e isolamento](02-multi-tenancy.md)
- [Modelo de dados](03-modelo-de-dados.md)
- [AI Orchestrator e Company Brain](04-ai-orchestrator.md)
- [Roadmap](05-roadmap.md)
