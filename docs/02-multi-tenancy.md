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
USING (
    current_setting('app.bypass_rls', true) = 'on'
    OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
)
WITH CHECK ( ... mesma condição ... );
```

Três detalhes importam:

- **`FORCE`** faz a política valer inclusive para o dono da tabela — que é o
  papel que a aplicação usa no MVP. Sem isso, o RLS seria decorativo.
- **`WITH CHECK`** impede *gravar* linha com `tenant_id` alheio, não só lê-la.
  Sem isso, dá para escrever no tenant do vizinho.
- **Esquecer o escopo não vaza**: sessão sem `app.tenant_id` não lê nada, em
  vez de ler tudo.

Quem define a variável é `app/db/session.py`:

```python
with tenant_session(tenant_id) as session:   # SET LOCAL app.tenant_id
    session.execute(select(Campaign))        # já filtrado pelo banco
```

`backend/tests/test_rls.py` verifica cada uma dessas propriedades — inclusive
`UPDATE` e `DELETE` cruzados, que retornam 0 linhas afetadas.

**Próximo passo em produção:** a aplicação conectar com um role sem
`BYPASSRLS`. O código já está pronto para isso, porque só três pontos usam
sessão sem escopo.

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
- [ ] Secrets manager externo (hoje: variável de ambiente)
- [ ] Role de banco sem `BYPASSRLS` para a aplicação
- [ ] Política de retenção de dados
- [ ] SSO / MFA
