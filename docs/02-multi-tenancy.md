# Multi-tenancy: as quatro camadas

Multi-tenancy não é só banco. São quatro fronteiras, e cada uma tem um lugar
no código.

## 1. Dados

Empresa A não acessa dados da Empresa B.

A garantia é do PostgreSQL, não da disciplina de quem escreve a query. A
migration `0002_row_level_security` liga RLS em todas as tabelas com dados de
cliente:

```sql
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON campaigns
USING      (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
```

Quatro detalhes importam:

- **`FORCE`** faz a política valer inclusive para o dono da tabela — e é a única
  das quatro que depende de onde o banco roda. Em servidor próprio ela fica
  ligada, e nem quem rodou as migrations escapa. Em **Postgres gerenciado**
  (Render, Neon, Supabase, RDS, Cloud SQL) ela sai, por um motivo que não é
  escolha: nenhum provedor dá superusuário, `CREATEROLE` **não** concede
  `BYPASSRLS` — só um superusuário concede —, e sem um dos dois a parte
  administrativa (autenticação, painel, worker) não teria como enxergar mais de
  uma empresa. O caminho que sobra é o do próprio PostgreSQL: o dono da tabela
  atravessa as políticas. Então lá o dono é o role administrativo, `FORCE` sai, e
  **o isolamento do role da aplicação não muda em nada** — `FORCE` nunca valeu
  para quem não é dono. Quem decide é o `bootstrap_roles`, conforme os
  privilégios que aquele banco permite, e o modo em vigor (`atributo` ou `posse`)
  aparece no log da subida. A rede de proteção que `FORCE` dava — apontar a
  aplicação para o dono por engano — passa a ser uma verificação explícita: a
  aplicação recusa subir se o role dela for dono de qualquer tabela com
  `tenant_id`. Há sete testes contra um banco montado com os privilégios exatos
  de um provedor gerenciado, incluindo o que prova que uma empresa não enxerga a
  outra nesse modo.
- **`WITH CHECK`** impede *gravar* linha com `tenant_id` alheio, não só lê-la.
  Sem isso, dá para escrever no tenant do vizinho.
- **Esquecer o escopo não vaza**: sessão sem `app.tenant_id` não lê nada, em
  vez de ler tudo.
- **Não há escape por variável de sessão.** A primeira versão da política
  aceitava `app.bypass_rls = 'on'`, o que era conveniente e errado: qualquer
  SQL executado na conexão da aplicação podia ligá-la. Hoje o privilégio de
  atravessar o isolamento é atributo do role, verificado pelo banco.

### Os dois roles

| Role             | Atributos                 | Quem usa                                          |
|------------------|---------------------------|---------------------------------------------------|
| `ia_sdr_app`     | NOSUPERUSER, NOBYPASSRLS  | A aplicação, em toda query de dado de cliente      |
| `ia_sdr`         | dono das tabelas, BYPASSRLS | Migrations, bootstrap, autenticação, Platform Admin |

`python -m scripts.bootstrap_roles` cria o role de aplicação e concede a ele
`SELECT/INSERT/UPDATE/DELETE` — nada de DDL, nada de `TRUNCATE`. É idempotente
e roda em toda subida.

**Por que isso não é detalhe de deploy.** A imagem oficial do PostgreSQL cria o
`POSTGRES_USER` como **superusuário**, e superusuário ignora RLS inteiro —
`FORCE` inclusive. Uma aplicação apontada para esse role sobe normalmente,
responde tudo com `200`, e entrega os dados de todos os tenants para qualquer
um. Não há mensagem de erro em lugar nenhum. Por isso existem duas defesas
contra essa configuração:

- `verify_database_roles()` roda no startup e recusa subir se o role da
  aplicação tiver `SUPERUSER` ou `BYPASSRLS` — ou se o administrativo não
  tiver o bypass de que precisa;
- `tests/test_rls.py::test_conexao_da_aplicacao_nao_pode_ignorar_rls` falha a
  suíte inteira nessa configuração.

Quem define a variável é `app/db/session.py`:

```python
with tenant_session(tenant_id) as session:   # SET LOCAL app.tenant_id
    session.execute(select(Campaign))        # já filtrado pelo banco
```

`backend/tests/test_rls.py` verifica cada uma dessas propriedades — inclusive
`UPDATE` e `DELETE` cruzados, que retornam 0 linhas afetadas, e o privilégio do
role com que a suíte está conectada.

### A lista de tabelas é conferida contra os modelos

A lista de tabelas que recebem RLS (`TENANT_SCOPED_TABLES`) é mantida à mão, e
por isso a suíte confere as duas direções: toda tabela da lista tem política, **e
nenhuma tabela mapeada com `tenant_id` está fora dela**.

A segunda direção foi acrescentada depois de encontrar o caso que ela pega:
`memberships` carregava `tenant_id` desde a primeira migration e nunca teve
política. Não vazava por sorte — todos os caminhos que leem memberships de
várias empresas (registro, login, troca de tenant, `/auth/me`, resolução de
permissão) usam a sessão sem escopo de propósito. Mas `limits.check_can_add_user`
conta os membros pela sessão **com escopo**, protegido apenas por um
`WHERE tenant_id = ...` escrito à mão — exatamente aquilo que este capítulo diz
que o RLS existe para não depender. A migration `0008_rls_memberships` fecha o
laço.

## 2. IA

A IA da Empresa A não pode usar documentos, prompts, produtos ou histórico da
Empresa B.

O `ContextBuilder` (`app/orchestrator/context_builder.py`) monta o contexto
sempre a partir do `tenant_id` do envelope e, além de depender do RLS, confere
o `tenant_id` de cada objeto carregado (`assert_same_tenant`). Se algo de outro
tenant aparecer, a execução para com `cross_tenant_access` — é a segunda
barreira, para o caso de alguém abrir uma sessão sem escopo por engano.

Conhecimento também tem escopo *dentro* do tenant: um `knowledge_document` sem
`campaign_ids` vale para o tenant inteiro; com lista, só para aquelas campanhas.

## 3. Credenciais

Cada tenant tem suas contas de email, calendário, CRM, API keys e domínio.

- Ficam em `integrations`, cifradas com Fernet (`app/core/crypto.py`).
- **Nunca** são serializadas em resposta de API — `IntegrationResponse` não tem
  campo de credencial, de propósito, e há teste garantindo isso.
- **Nunca** vão para o frontend. Chave de cliente no browser é chave vazada.
- Em produção, a chave de cifragem vem de um secrets manager, não do `.env`.

## 4. Operação

Jobs assíncronos carregam `tenant_id`, `job_id`, `user_id` e `campaign_id`.

```python
JobEnvelope(tenant_id=..., agent=AgentKind.RESEARCH, campaign_id=..., ...)
```

O contexto de execução é **derivado do envelope** (`envelope.context()`), nunca
herdado do processo. É isso que impede o problema clássico: o worker pega um
job da Empresa A e executa usando configuração da Empresa B.

## Papéis (RBAC)

| Papel      | O que faz                                              |
|------------|--------------------------------------------------------|
| `owner`    | Tudo, inclusive billing                                |
| `admin`    | Configuração, usuários, integrações, auditoria         |
| `operator` | Campanhas, prospects, conversas, dispara agentes       |
| `viewer`   | Somente leitura                                        |

A matriz está em `app/rbac/roles.py` e é cumulativa (cada papel contém o
anterior). O **Platform Admin** é ortogonal a isso: é uma flag no usuário
(`users.is_platform_admin`), com rotas separadas em `/api/v1/admin/*`, porque
enxergar a plataforma inteira é outro tipo de acesso — não um papel maior
dentro de um tenant.

Quando fizer sentido, a escala cresce sem refazer nada:
`Platform Admin → Tenant Owner → Admin → Manager → SDR → Sales → Viewer`.

## Checklist de segurança do MVP

- [x] Autenticação (JWT, bcrypt)
- [x] RBAC por papel, verificado na rota
- [x] Isolamento por tenant no banco (RLS com `FORCE` e `WITH CHECK`)
- [x] Segundo cinto de segurança na montagem de contexto de IA
- [x] Criptografia de credenciais em repouso
- [x] Audit log por tenant
- [x] Rate limiting
- [x] Registro de toda execução de agente (entrada, saída, custo, digest do contexto)
- [x] Controle de acesso à Knowledge Base (por tenant e por campanha)
- [x] Role de banco sem `SUPERUSER`/`BYPASSRLS` para a aplicação, verificado
      no startup
- [ ] Secrets manager externo (hoje: variável de ambiente)
- [ ] Política de retenção de dados
- [ ] SSO / MFA
