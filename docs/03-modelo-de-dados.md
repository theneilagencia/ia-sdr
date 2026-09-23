# Modelo de dados

PostgreSQL 16, um banco, Row Level Security. 26 tabelas — duas de identidade e
24 com `tenant_id` (a lista está em
`backend/app/db/models/__init__.py::TENANT_SCOPED_TABLES`).

## Identidade

| Tabela        | Papel                                                        |
|---------------|--------------------------------------------------------------|
| `tenants`     | A empresa cliente: plano, status de assinatura, limites, settings |
| `users`       | Identidade global — o mesmo email pode servir a vários tenants |
| `memberships` | Usuário × tenant × papel. É aqui que o acesso é decidido      |

`memberships` carrega `tenant_id` e tem política de RLS como qualquer tabela do
cliente (`0008_rls_memberships`): quem decide o acesso é justamente a tabela que
menos pode vazar. Está aqui pelo papel que cumpre, não por ser global.

`users` é global de propósito: um consultor que atende três clientes tem uma
senha, não três. O que é por tenant é o **vínculo**, não a identidade.

O **segundo fator** também vive em `users` (`0010_mfa`), pela mesma razão: quem
serve duas empresas protege uma conta. O segredo TOTP fica cifrado com a chave
Fernet, como toda credencial daqui, e ao lado dele ficam o último passo aceito
(é o que recusa reuso de código) e os códigos de recuperação em SHA-256.

## Por tenant

```
Plataforma      memberships · invitations · audit_logs · usage_events · jobs
                invoices
Comercial       companies · contacts · campaigns · prospects · research · scores
Engajamento     sequences · sequence_enrollments · conversations · messages
                qualifications · meetings
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

## Fechamento do mês

`invoices` é onde o consumo medido vira número a cobrar: uma linha por
`(tenant_id, período)` — a unicidade é do banco, para o mesmo mês não ser cobrado
duas vezes por dois cliques.

O contrato mora em `tenants`: `contract_monthly_cents`, `contract_currency` e
`overage_cents_per_unit`. Tudo em centavos inteiros, porque `float` em dinheiro
erra centavo, e centavo errado em fatura é conversa com o cliente. Zero significa
"ainda não precificado": o mês fecha, mostra o consumo e não cobra nada — a
plataforma não inventa o preço de quem a opera.

A fatura copia os números do contrato no fechamento em vez de apontar para ele:
fatura é o retrato de um mês, e mexer no preço em outubro não pode mudar o que
setembro cobrou.

```
draft ──▶ issued ──▶ paid
  └──────────┴─────────┴──▶ void
```

`draft` recalcula a cada fechamento — refazer o fechamento é seguro de propósito.
De `issued` em diante os números congelam, e pagar sem emitir é recusado: seria
cobrança quitada que o cliente nunca recebeu. `void` sai dos três, inclusive de
`paid`: quem baixou a fatura errada precisa de saída.

Cobrança real (gateway, boleto, cartão) continua fora, e não é o que faltava:
no Brasil quem emite nota é o contador ou um serviço de NFe, então gateway é
conveniência de recebimento. O que destravava faturar era o número — e ele existe.

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
