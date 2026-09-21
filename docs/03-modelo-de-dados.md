# Modelo de dados

PostgreSQL 16, um banco, Row Level Security. 22 tabelas no Sprint 1 — três
globais e 19 com `tenant_id`.

## Globais

| Tabela        | Papel                                                        |
|---------------|--------------------------------------------------------------|
| `tenants`     | A empresa cliente: plano, status de assinatura, limites, settings |
| `users`       | Identidade global — o mesmo email pode servir a vários tenants |
| `memberships` | Usuário × tenant × papel. É aqui que o acesso é decidido      |

`users` é global de propósito: um consultor que atende três clientes tem uma
senha, não três. O que é por tenant é o **vínculo**, não a identidade.

## Por tenant

```
Plataforma      audit_logs · usage_events
Comercial       companies · contacts · campaigns · prospects · research · scores
Engajamento     sequences · conversations · messages · qualifications · meetings
Conhecimento    company_profiles · knowledge_documents · knowledge_chunks
IA              ai_agents · agent_runs
Integrações     integrations
```

Toda uma delas tem:

```
tenant_id  uuid  NOT NULL  REFERENCES tenants(id) ON DELETE CASCADE
```

mais índice em `tenant_id` e índice composto `(tenant_id, created_at)` — porque
na prática toda listagem é "os últimos N deste tenant".

A coluna vem do mixin `TenantScoped` (`app/db/base.py`), e a mesma lista de
tabelas alimenta a criação das políticas de RLS. A lista está num lugar só
(`TENANT_SCOPED_TABLES`), e um teste falha se uma tabela nova ficar de fora.

## Campanha como contexto

`campaigns` guarda ICP, geografia, personas, oferta, mensagem, critérios de
qualificação, canais e limites diários. É o que separa "campanha de CFO no
Canadá" de "campanha de COO no Brasil" para o mesmo tenant — os agentes leem o
contexto da campanha, não um ICP global.

Campos que ainda vão mudar muito (ICP, personas, oferta, playbook) são `JSONB`.
Schema rígido neles, agora, custaria mais migration do que entrega.

## Consumo

`usage_events` é a moeda interna:

| Operação           | Unidades |
|--------------------|----------|
| Research           | 1        |
| AI message         | 1        |
| Deep research      | 5        |
| Qualification      | 2        |
| Voice interaction  | 10       |

Custo real fica em `cost_micro_usd` (inteiro, milionésimos de dólar — sem
`float` em dinheiro). Com isso dá para responder, por tenant e por mês: quanto
usou, quanto custou, qual a margem.

## Planos

Limites ficam em código (`app/billing/plans.py`), com override contratual por
tenant em `tenants.limit_overrides`:

| Plano      | Campanhas | Prospects/mês | Usuários | Contas email | Unidades IA/mês |
|------------|-----------|---------------|----------|--------------|-----------------|
| Starter    | 1         | 1.000         | 2        | 1            | 5.000           |
| Growth     | 10        | 10.000        | 10       | 5            | 60.000          |
| Enterprise | ilimitado | ilimitado     | ilimitado| ilimitado    | ilimitado       |

Cobrança não entra no primeiro sprint; o **modelo** entra. Quando o gateway
chegar, não se refaz o modelo de dados.

## Migrations

```
0001_foundation        22 tabelas, índices e constraints
0002_rls               RLS: ENABLE + FORCE + policy tenant_isolation
0003_rls_role          tira o escape por variável de sessão; o bypass passa a
                       ser atributo do role administrativo
```

```bash
alembic upgrade head          # aplica
alembic downgrade -1          # volta uma
alembic revision --autogenerate -m "descrição"
```

Migrations rodam com o role **administrativo** (`DATABASE_ADMIN_URL`): a
aplicação não tem, e não deve ter, privilégio de DDL. Depois delas,
`python -m scripts.bootstrap_roles` garante o role de aplicação e seus
privilégios mínimos.

A URL do banco vem sempre de `app.core.config.settings` — dev, CI e produção
leem da mesma fonte.
