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

**Dois roles de banco, não um.** A aplicação conecta com um role
NOSUPERUSER/NOBYPASSRLS — ele *não consegue* sair do tenant, mesmo que o código
erre. O role administrativo, com BYPASSRLS, fica reservado a migrations,
autenticação e painel de plataforma, via `unscoped_session(reason=...)`, que
exige um motivo escrito. É fácil auditar num code review:
`grep unscoped_session` mostra toda a superfície.

**O privilégio de atravessar o isolamento é atributo do role, não variável de
sessão.** A primeira versão aceitava `app.bypass_rls = 'on'` na política — e
qualquer SQL rodando na conexão da aplicação conseguiria ligar essa variável.
Quem decide agora é o banco, pelo atributo do role.

**A API recusa subir com um role privilegiado.** `verify_database_roles()` roda
antes da primeira requisição. É a checagem que faltava: a imagem oficial do
PostgreSQL cria o `POSTGRES_USER` como superusuário, e superusuário ignora RLS
por completo — inclusive `FORCE`. Uma aplicação apontada para esse role
funciona sem erro nenhum e serve dados de todos os tenants para todo mundo.

**Todo trabalho assíncrono tem envelope.** `JobEnvelope` carrega `tenant_id`,
`job_id`, `user_id` e `campaign_id`. Um worker não tem como "herdar" o tenant
do job anterior, porque o contexto é derivado do envelope, não do processo.

**Contabilidade de execução em transação própria.** Se uma execução de agente
falha e o trabalho de domínio é desfeito, o registro de que ela aconteceu — e
quanto custou — permanece. Falha de agente não pode virar buraco no histórico
nem na fatura.

**Entrega repetida não executa duas vezes.** O `job_id` do envelope é estável e
sobrevive ao requeue, então uma execução já feita é devolvida, não refeita. Isso
importa porque a fila devolve à fila o job que passa de quinze minutos em
execução — e ela não tem como distinguir "o worker morreu" de "a pesquisa ainda
está buscando na web". Sem idempotência, o segundo worker pagaria a mesma conta e
deixaria dois rascunhos quase iguais esperando aprovação.

## Documentos relacionados

- [Multi-tenancy e isolamento](02-multi-tenancy.md)
- [Modelo de dados](03-modelo-de-dados.md)
- [AI Orchestrator e Company Brain](04-ai-orchestrator.md)
- [Roadmap](05-roadmap.md)
