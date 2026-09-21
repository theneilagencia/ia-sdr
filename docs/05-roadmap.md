# Roadmap

## Sprint 1 — Foundation ✅

| Item                                   | Onde                                      |
|----------------------------------------|-------------------------------------------|
| Auth (JWT, bcrypt, troca de tenant)    | `app/api/v1/auth.py`, `app/core/security.py` |
| Tenant + Users + Memberships           | `app/db/models/platform.py`               |
| RBAC (owner/admin/operator/viewer)     | `app/rbac/roles.py`                       |
| PostgreSQL + RLS                       | `alembic/versions/0002_row_level_security.py` |
| Role de aplicação sem bypass de RLS    | `scripts/bootstrap_roles.py`, `app/db/session.py` |
| Contexto de tenant por requisição/job  | `app/tenancy/context.py`, `app/api/deps.py` |
| Platform Admin                         | `app/api/v1/admin.py`, `scripts/promover_admin.py` |
| Audit log                              | `app/services/audit.py`                   |
| Planos, limites e medição de consumo   | `app/billing/plans.py`, `app/services/usage.py` |
| Credenciais cifradas                   | `app/core/crypto.py`                      |
| Rate limiting                          | `app/api/middleware.py`                   |
| Company Brain                          | `app/api/v1/company_brain.py`             |
| AI Orchestrator (fronteira e registro) | `app/orchestrator/`                       |

Modelo de dados completo dos quatro sprints já criado, para não refazer
migration a cada entrega.

## Sprint 2 — Sales Intelligence ✅

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
  prospects, campanhas e configurações. Token em cookie httpOnly, chamadas à
  API feitas pelo servidor — nada de credencial no browser
- **Configuração por empresa, para leigos**: chave da Anthropic e conta de
  email (Gmail, Outlook ou SMTP próprio), ambas testadas antes de salvar,
  cifradas e nunca devolvidas; limites de volume com aquecimento de domínio

- **Import por CSV** (`app/services/csv_import.py`): o arquivo que a pessoa já
  tem, com os apelidos de coluna em português e inglês, ponto e vírgula do
  Excel em português, Latin-1 e linha inválida devolvida com o número da linha

A fazer:

- Enriquecimento de prospects por provedores externos (exige fornecedor)
- Editor do Company Brain no web app (a API existe)

## Sprint 3 — AI SDR ✅

Feito:

- **Envio de verdade** (`app/services/email_sender.py`): o rascunho aprovado sai
  pela conta configurada da empresa, com limite diário, aquecimento de domínio e
  horário comercial aplicados antes de cada mensagem
- **Descadastro** (`app/services/unsubscribe.py`): link assinado em todo email,
  no corpo e no cabeçalho `List-Unsubscribe`, com página pública de um clique
- **Fila de envio na tela**: cota do dia com o motivo, envio individual ou em
  lote, e uma seção para o que falhou, com o erro em português e "tentar de novo"

- **Recebimento** (`app/services/email_receiver.py`): lê a caixa da empresa por
  IMAP e liga cada resposta à conversa certa — pelo `In-Reply-To`, e pelo
  endereço do remetente quando o cabeçalho não vem. Sem pareamento, ignora:
  inventar a conversa seria pior do que perder a mensagem

- **Fila e worker** (`app/services/jobs.py`, `app/workers/runner.py`):
  PostgreSQL como fila, com `FOR UPDATE SKIP LOCKED`, deduplicação, backoff e
  retomada do que ficou preso. O worker lê a caixa de cada empresa, despacha o
  que está aprovado e aciona o Conversation Agent quando chega resposta — o
  ciclo roda sem ninguém olhando

- **Base de conhecimento inteira** (`app/services/knowledge.py`,
  `app/api/v1/knowledge.py`): colar texto, subir arquivo, listar, apagar e
  buscar. A recuperação é por relevância — busca textual nativa do PostgreSQL,
  não vetorial, porque a Anthropic não tem API de embeddings e vetor exigiria
  um segundo fornecedor. A coluna `embedding` segue no modelo para o dia em que
  isso se justificar

- **Cadência multi-passo** (`app/services/sequences.py`,
  `app/api/v1/sequences.py`): estado por prospect, o passo vencido vira job do
  Outreach Agent com a instrução daquele toque, e as regras de parada valem
  mais que a cadência — resposta, descadastro, reunião, qualificação e os
  estágios terminais param; campanha pausada adia

- **Bounce** (`app/services/email_receiver.py`): aviso de não entrega nunca é
  contado como resposta. Permanente (5.x.x) tira o prospect do funil e para a
  cadência; temporário (4.x.x) só registra

- **Conversas e contatos** (`app/api/v1/conversations.py`, `contacts.py`): a
  caixa de entrada da operação e o cadastro que só o import escrevia

- **Troca de senha e gestão de membros**: a troca encerra as sessões abertas;
  o último owner não é rebaixado nem removido

- **Exportação por tenant** (`app/services/export.py`): o cliente leva os dados
  embora, sem nenhum segredo junto

## Sprint 4 — Conversion

Tudo o que falta aqui depende de uma conta em serviço de terceiro, e por isso
não foi construído às cegas: código de integração que nunca falou com a API de
verdade é código que ainda não existe.

- Integração de calendário — Google ou Microsoft (o agendamento manual já
  existe e é o mesmo caminho)
- **Integração com o RAVI** ✅ (`app/services/ravi.py`). Esta plataforma não tem
  CRM próprio e não vai ter: o lead vive no RAVI, e o que o AI SDR faz é
  pesquisar, pontuar, abordar e qualificar — empurrando isso para lá. Duas bases
  com o mesmo lead divergem em uma semana.

  Sentido: **AI SDR → RAVI**. `POST /leads` do serviço de chat, autenticado por
  `Authorization` + `x-tenant-id`, que do lado do RAVI faz upsert por email ou
  telefone — é essa propriedade que permite o envio viver na fila com backoff.
  A conexão é por empresa, testada antes de salvar, cifrada e nunca devolvida à
  tela; o token aparece como `…1234`.

  Três coisas para alinhar com quem cuida do RAVI:

  1. **`source` não tem valor para prospecção outbound.** Os aceitos são
     `widget`, `whatsapp`, `email`, `form` e `manual`; vai `manual`, que é o
     menos errado. Um valor próprio separaria o lead que chegou pelo chat do
     lead que este motor foi buscar.
  2. **`stage` é texto no Lead do chat e `stage_id` no funil do dashboard.** O
     que se configura aqui é enviado como texto, sem tradução.
  3. **O contrato foi lido no repositório público** (`theneilagencia/ravi`). A
     fonte da verdade é `ApyMine/ravi`, privado, que a sessão não conseguiu
     abrir. Se divergir, o que quebra é o teste de conexão e o envio — em voz
     alta, com o corpo da resposta do RAVI no erro.

- Billing: assinatura, cobrança por uso e faturas — exige gateway (Stripe)
- Dashboard do funil e de consumo: a API existe (`/prospects/funnel`,
  `/tenants/me/usage`); falta a tela

## Transversal (quando a operação exigir)

- **Telas que faltam no web app**: Company Brain, base de conhecimento,
  conversas, importação de lista, cadências, membros, CRM, detalhe do prospect e
  Platform Admin. Toda a API por trás delas existe — hoje 22 das 97 operações
  da API têm tela
- Secrets manager externo (hoje a chave Fernet vive no `.env` do servidor)
- Rate limiting distribuído (Redis) — hoje é por processo, o que basta para uma
  réplica só
- Política de **retenção** automática por tenant (a exportação já existe)
- Busca vetorial na base de conhecimento — exige fornecedor de embeddings
- SSO / MFA para contratos enterprise
