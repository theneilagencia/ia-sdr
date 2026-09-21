# AI Orchestrator e Company Brain

## Um orquestrador, não agentes espalhados

Agente solto pelo código é onde o isolamento vaza: alguém monta um prompt numa
rota, esquece o filtro, e o conhecimento de um cliente entra na conversa de
outro. Por isso existe um ponto único de entrada — `app/orchestrator/runner.py`.

```
envelope → contexto do tenant → cota → execução → consumo → auditoria
```

O disparo é sempre um envelope:

```json
{
  "job_id": "affbf2c384bd411d9fbcc286c02f2274",
  "tenant_id": "tnt_001",
  "campaign_id": "cmp_023",
  "agent": "research",
  "entity_type": "company",
  "entity_id": "company_889",
  "user_id": "usr_010"
}
```

E o contexto é montado nesta ordem, só com o que pertence àquele tenant:

```
Tenant
  ↓
Company Brain
  ↓
Campaign
  ↓
ICP
  ↓
Knowledge Base
  ↓
Agent Instructions
  ↓
Task
```

Cada objeto carregado é conferido contra o `tenant_id` do envelope
(`assert_same_tenant`). O contexto final gera um **digest** guardado no
`agent_run`: dá para auditar depois exatamente qual contexto produziu qual
saída, sem guardar o prompt inteiro.

Isso também é o que torna o custo controlável: o agente recebe um contexto
delimitado, não "tudo que o tenant tem".

## Agentes do MVP

| Agente          | Unidades | Para quê                                        |
|-----------------|----------|-------------------------------------------------|
| `research`      | 1        | Pesquisa a conta-alvo, com fonte                 |
| `outreach`      | 1        | Escreve a primeira abordagem                     |
| `conversation`  | 1        | Conduz a conversa a partir da base de conhecimento |
| `qualification` | 2        | Avalia contra os critérios da campanha           |

O motor é o mesmo para todos os tenants. O que muda é o contexto:

```
Tenant A   vende software para mineração
Tenant B   vende ERP para indústria
Tenant C   vende serviços financeiros
```

Mesmo agente, três negócios, zero sobreposição.

## Estado atual da execução

`AgentExecutor` é um ponto de extensão. No Sprint 1, o executor registrado não
chama modelo nenhum — devolve um eco determinístico com o digest do contexto.
O que está pronto e coberto por teste é **a fronteira em volta da chamada**:
isolamento, cota, registro, consumo e auditoria. Ligar o modelo no Sprint 3 é
registrar um executor:

```python
register_executor("research", meu_executor_de_verdade)
```

Nada mais no caminho muda.

## Company Brain

Um por tenant (`company_profiles`). É o que a IA sabe sobre o negócio do
cliente — e provavelmente o ativo mais valioso do produto:

```
TENANT
│
├── Company Profile      ├── FAQs
├── Products             ├── Objections
├── Services             ├── Competitors
├── ICP                  ├── Sales Playbook
├── Personas             ├── Brand Voice
├── Pricing              └── AI Policies
└── Cases
```

Nasce junto com o tenant, vazio, no momento do cadastro. É editável por
`GET`/`PUT /api/v1/company-brain` por quem tem `knowledge:write`.

`ai_policies` é o lugar para as regras de comportamento do cliente — o que a IA
pode prometer, quando precisa escalar para humano, o que nunca deve dizer.

## Fluxo do produto

```
CREATE ACCOUNT → CREATE COMPANY → DEFINE ICP → DEFINE OFFER → UPLOAD KNOWLEDGE
      → CONNECT EMAIL → CREATE CAMPAIGN → GENERATE PROSPECTS
      → AI RESEARCH → AI SCORE → AI OUTREACH → AI CONVERSATION
      → AI QUALIFICATION → MEETING
```

E o painel do cliente é esse funil:

```
   1.284 prospects → 842 researched → 611 contacted
        → 47 engaged → 19 qualified → 11 meetings
```

## Depois do SDR

A mesma arquitetura recebe os próximos papéis sem redesenho — multitenancy,
identidade, Company Brain, políticas, integrações, memória operacional e
governança já estão prontos:

```
AI SDR → AI Sales Development → AI Account Executive
       → AI Customer Success → AI Revenue Operations
```
