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

`AgentExecutor` é um ponto de extensão, e os quatro agentes já são executores de
verdade: chamam o modelo com a chave **do tenant**, com saída estruturada e
validada, e gravam o que produziram (pesquisa, rascunho, veredito). O executor de
eco continua no código para desenvolvimento e teste, mas não é mais reserva
silenciosa — fingir que trabalhou é pior do que dizer que falta configurar.

### O que a plataforma garante em volta da chamada

Isolamento, cota, registro, consumo e auditoria valem para todos, e mais quatro
regras que existem porque cada chamada gasta dinheiro de verdade:

- **Teto de custo por execução** (`ai_max_cost_micro_usd`, US$ 0,50 por
  padrão), conferido **depois de cada turno** — inclusive entre as retomadas de
  turno pausado da busca na web. Estourou, a execução é recusada ali, sem
  comprar o turno seguinte.
- **Gasto de execução que falha não desaparece.** Token queimado antes da falha
  continua tendo custado: os tokens vão para o `agent_run` e o custo real vira
  evento de consumo com **zero unidade** — o cliente não paga cota por trabalho
  que não foi entregue, mas a margem do mês não mente.
- **Recusa de política não é tentada de novo.** Descadastro, cota estourada,
  teto de custo e isolamento de tenant (402/403/409) encerram o job na primeira
  vez. A fila insistir aqui não conserta nada e, quando a recusa vem depois da
  chamada ao modelo, a segunda tentativa paga a conta outra vez.
- **Execução idempotente por `job_id`.** Job que passa de quinze minutos volta
  para a fila enquanto ainda roda; sem isso, o segundo worker pesquisaria a
  mesma conta e pagaria de novo. Passado esse prazo sem conclusão, o run conta
  como **abandonado** — processo morto no meio não é entrega feita — e a
  tentativa seguinte refaz o trabalho em vez de herdar um run parado.
- **Toda exceção fecha o run.** Inclusive as que não são de domínio, que são
  justamente as mais prováveis: o SDK da Anthropic levantando corte de conexão,
  429 ou 500. Antes elas passavam por fora, o run ficava em `running` para
  sempre e a tentativa seguinte o devolvia como "já executado" — o job era
  marcado como concluído sem nada ter acontecido.

### As regras de parada de cada agente

Ficam no executor, e não só em quem despacha, porque o executor é o último ponto
antes de gastar token e produzir rascunho — e o disparo manual pela tela não
passa por cadência nenhuma:

| Agente          | Não roda quando                                                      |
|-----------------|----------------------------------------------------------------------|
| `outreach`      | contato descadastrado ou sem email, campanha pausada, teto diário **da campanha** atingido, prospect em estágio terminal (qualificado, reunião, desqualificado, bounce) ou lead que já respondeu |
| `conversation`  | conversa sem nenhuma mensagem do lead, ou última mensagem dele já respondida e enviada |
| `qualification` | campanha sem critérios, ou contato descadastrado — descadastro não se desfaz por veredito de agente |
| `research`      | alvo de outro tenant, alvo inexistente, contato sem empresa           |

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
