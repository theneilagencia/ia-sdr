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

- **Convite de calendário ✅ — por `.ics`, sem OAuth de ninguém.** A integração com
  Google Calendar ou Microsoft Graph estava aqui como "depende de terceiro", e o
  que ela entrega de fato — o compromisso no calendário do lead — não dependia:
  um `VEVENT` (RFC 5545, `METHOD:REQUEST`) anexado ao email que já sai pela conta
  da própria empresa faz Gmail, Outlook e Apple Mail mostrarem "Aceitar" de forma
  nativa. Sem aplicativo verificado, sem tela de consentimento por empresa, e sem
  cliente ficando de fora por não autorizar.

  O convite é opcional e desligado por padrão: quem registra reunião combinada
  por telefone não quer disparar convite que o outro lado não espera. `SEQUENCE`
  sobe a cada reenvio, senão o calendário do lead ignora o arquivo por já conhecer
  o UID — o email chegaria e a agenda não mudaria. E `invite_sent_at` existe para
  a tela distinguir **reunião combinada de reunião anotada**, que é a diferença
  que aparece no dia.

  Os freios de prospecção não se aplicam, e isso é decisão: teto diário e
  aquecimento protegem reputação contra volume frio, e este email é resposta a um
  acordo; horário comercial seguraria até as 9h um convite para uma reunião das
  10h. O que **não** tem exceção transacional é o descadastro: quem pediu para não
  receber não recebe nem convite.

  O que o `.ics` não dá, e continua dependendo de OAuth: **ler** a agenda do
  vendedor para oferecer só horários de fato livres. Marcar reunião segue sendo
  trabalho de quem opera
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

- **Fechamento mensal ✅ — faturar por contrato, sem gateway.** Este item dizia
  "exige gateway (Stripe)", e estava errado sobre o que faltava. No Brasil quem
  emite nota fiscal é o contador ou um serviço de NFe; gateway é conveniência de
  recebimento. O que impedia faturar era não haver **número**: a plataforma media
  o consumo de cada empresa e não sabia dizer quanto aquele mês custou.

  Agora sabe. O contrato fica em `tenants` (mensalidade, moeda, preço da unidade
  de IA acima da cota), o fechamento gera uma fatura por empresa ativa com o
  consumo do período medido por trás, e `POST /admin/invoices/close` é idempotente
  de propósito: refazer o fechamento recalcula rascunho e **não** toca no que já
  foi emitido. O estado anda `draft → issued → paid`, e `void` é alcançável dos
  três — quem baixou a fatura errada precisa de saída pela tela, não por `curl`;
  pagar sem emitir, ao contrário, é recusado: seria cobrança quitada que o cliente
  nunca recebeu.

  Três decisões que o teste fixa. **Preço não tem default**: empresa sem
  precificação fecha mostrando o consumo e cobrando zero — um valor mensal
  inventado num campo de dinheiro vira cobrança de verdade. **A fatura copia os
  números**, não aponta para o contrato: mexer no preço em outubro não pode mudar
  o que setembro cobrou. E **dinheiro é inteiro em centavos** do começo ao fim,
  inclusive no formulário, que recusa `1.999,90` em vez de adivinhar se o ponto é
  milhar ou decimal — em pt-BR e en-US isso são mil vezes de diferença.

  Ao lado do total cobrado, o painel mostra o custo real da Anthropic no período:
  é a leitura que responde se o contrato daquela empresa fecha em dinheiro. O que
  continua fora é receber automaticamente (boleto, cartão, assinatura recorrente)
  e emitir a nota — ambos com fornecedor, ambos decisão comercial de quem opera
- Dashboard do funil e de consumo ✅: é a tela inicial do web app — estágios
  cumulativos, aderência ao ICP por banda e o consumo do mês contra o limite do
  plano

## Transversal (quando a operação exigir)

- **Telas do web app ✅ — nenhuma capacidade da API ficou sem tela.** Funil e
  consumo, revisão e envio, conversas com a thread, prospects com import e
  detalhe, contas-alvo, contatos, campanhas, disparo **e configuração** de
  agente, cadências, Company Brain, base de conhecimento, equipe, auditoria,
  configurações (IA, email, volume, CRM e exportação) e o painel da plataforma

  As últimas quatro fecharam lacunas em que a API existia e a tela não — o que
  significa capacidade que só quem sabia usar `curl` tinha:

  * **Contas-alvo**, com correção. Domínio trocado é o erro mais caro de uma
    planilha: o agente pesquisa a empresa errada, escreve com os fatos dela, e
    ninguém percebe até o lead responder confuso. Não há apagar, de propósito —
    a conta está amarrada a prospect, pesquisa e conversa
  * **Contatos**, com busca, o filtro de quem está **sem email** (invisível no
    funil, porque o agente de abordagem se recusa a escrever para eles, com
    razão) e o registro de **descadastro pedido por fora** — telefone, WhatsApp,
    resposta a uma pessoa. É o item da lista com consequência legal, e era o que
    exigia ir ao banco
  * **Configuração por agente**: modelo, instruções próprias e teto de resposta.
    Isto existia no banco desde o primeiro sprint e era lido pelo orquestrador em
    toda execução — só não tinha porta de entrada: trocar o modelo de um cliente
    exigia `INSERT`. É a alavanca de custo mais direta da plataforma (pesquisa em
    Opus, abordagem em Haiku é escolha legítima), então a API recusa modelo fora
    da tabela de preços, que é o que permite medir margem
  * **Auditoria** e **exportação**: as duas já gravavam e devolviam tudo, sem
    nada que as mostrasse. "Dá para ir embora" que exige linha de comando, para
    quem avalia entrar, é o mesmo que não ser verdade

  No caminho apareceu um defeito de verdade: o contexto devolvia `2000` como teto
  de resposta quando a empresa não tinha configuração, e o executor faz
  `... or settings.ai_max_output_tokens` — então o 2000 sempre ganhava e o ajuste
  de 8000 nunca valeu para ninguém. Pior: a mensagem de "resposta truncada"
  mandava aumentar exatamente esse número sem efeito

  Na cadência, a ordem da tela é a ordem real da operação: escrever os toques,
  ativar, inscrever. Ela **nasce desativada** — quem acabou de escrever ainda vai
  reler, e meia cadência ativa dispara mesmo assim — e a inscrição fica barrada
  com explicação enquanto ela estiver desativada, porque a API recusa entrada em
  cadência que não anda e um botão que sempre dá erro é pior do que nenhum botão.
  O "avançar agora" diz que o toque **foi para a fila**, não que o rascunho está
  pronto: quem escreve é o agente de abordagem, depois, e prometer o rascunho
  mandaria a pessoa olhar uma fila vazia

  A caixa de entrada põe quem escreveu e não foi respondido no topo, porque é o
  único item da lista com prazo. Na conversa, o humano assume com os mesmos
  freios do agente: a resposta escrita à mão **nasce rascunho** e passa pela fila
  de Revisão, já que limite diário, aquecimento, horário comercial e descadastro
  estão todos depois da aprovação. Passar para uma pessoa é o outro lado do
  escalonamento que o agente já fazia — ele marcava que precisava de humano, e
  agora se diz qual

  O detalhe do prospect mostra a nota com o porquê e o veredito **critério a
  critério, com a evidência**: "qualificado, confiança 80" sem evidência é um
  palpite com aparência de número. Dali se marca reunião, registra resposta
  recebida por fora do email, envia para o RAVI e registra descadastro — que vale
  no ato e não tem desfazer na tela, de propósito

  A tela de agentes traduz o que ninguém deveria precisar saber: o envelope pede
  a entidade **daquele** agente, e ela muda — a pesquisa é sobre a conta, a
  abordagem e a qualificação são sobre o prospect, a conversa é sobre a conversa.
  Quem opera escolhe uma pessoa numa lista com nome e empresa. A pesquisa vai
  para a fila porque busca na web leva minutos; os outros três rodam na
  requisição, e o resultado diz onde o trabalho aparece ("está em Revisão"), não
  o nome interno do agente

  A tela de campanha serve os campos que **algum agente lê** — objetivo,
  geografia, ICP, personas, oferta, mensagem e critérios de qualificação — e
  mostra, por campanha, o que falta para ela produzir: sem critérios o agente de
  qualificação se recusa a julgar, e é melhor ler isso na lista do que descobrir
  no disparo. `channels` e `daily_limits` ficam fora: o volume de envio é
  configuração da empresa, e está em Configurações

  Do Company Brain, a tela serve os **sete campos que algum agente lê** —
  posicionamento, produtos, casos, objeções, tom de voz, playbook e políticas de
  IA. Os outros oito (razão social e site à parte) existem no modelo e nenhum
  agente os consome: `services`, `personas`, `pricing`, `faqs` e `competitors`
  ficam fora da tela de propósito, porque campo que nada consome ensina a pessoa
  que preencher importa quando não importa. `pricing` e `faqs` são o caso mais
  interessante: o agente de conversa **escala preço para humano por desenho**,
  então preenchê-los mudaria o comportamento dele, não a tela

  O painel da plataforma mostra plano, assinatura, consumo do mês e falhas de
  agente, e deixa mexer em plano, status e limites contratados. O que ele **não**
  mostra é tão deliberado quanto o que mostra: nome de campanha, lead, conversa e
  base de conhecimento do cliente não aparecem ali. Ver a conta de um cliente e
  ler as conversas dele são coisas diferentes, e a marca de platform admin só
  concede a primeira — há teste de backend para isso desde o item 1

- **Revisão adversarial dos quatro executores ✅.** Sete defeitos, todos na
  linha entre o modelo e o dinheiro: gasto de execução que falha desaparecia da
  contabilidade (run com zero token, nenhum evento de consumo — e a fila
  tentando mais duas vezes, cada tentativa invisível do mesmo jeito); o teto de
  custo só era conferido depois do último turno, então uma pesquisa com busca na
  web pagava seis turnos para descobrir que o primeiro já havia estourado; o
  teto existia só na pesquisa, e agora é da plataforma; o limite diário da
  campanha era contado no tenant inteiro, então uma campanha comia a cota da
  outra; o Outreach escrevia abordagem fria para quem já havia respondido ou já
  tinha saído do funil (a cadência checava, o executor não, e o disparo manual
  não passa pela cadência); o Conversation respondia a si mesmo quando não havia
  mensagem do lead, ou mandava uma segunda réplica para a mesma frase; e o
  Qualification podia **desfazer um descadastro**, devolvendo ao funil como
  oportunidade quem acabou de pedir para não ser mais procurado.

  O oitavo é o mais silencioso e vale por si: o runner só tratava exceção de
  domínio, e a mais provável em produção não é — é o SDK da Anthropic levantando
  corte de conexão, 429 ou 500. O run ficava em `running` para sempre e, por ser
  um run não-falho com o mesmo `job_id`, a tentativa seguinte o devolvia como "já
  executado" e o job era marcado como concluído. O trabalho sumia e a tela dizia
  que estava tudo bem

- **Convite com aceite ✅.** O fluxo antigo fazia duas coisas erradas de uma vez.
  A senha inicial de uma pessoa era digitada por outra — e passava pelo navegador,
  pelo histórico e pelo canal por onde fosse entregue. E a resposta, para explicar
  que a senha seria ignorada quando o email já tinha conta, dizia exatamente isso:
  enumeração da base de usuários entregue de graça a quem convida.

  Agora `POST /tenants/me/invitations` devolve **uma vez** um link
  (`/convite/<token>`, 32 bytes de aleatório, SHA-256 no banco, validade de 7
  dias, uso único), e quem entra escolhe a senha ao aceitar. Email novo: a senha
  digitada nasce com a conta. Email que já tem conta: a senha tem de ser a dela —
  o convite anexa o vínculo, não abre a conta de ninguém. Token inválido,
  expirado, já usado e senha errada devolvem **a mesma frase**, porque distinguir
  devolveria a enumeração pela porta do aceite.

  Convite pendente ocupa vaga do plano (senão o limite só apareceria para quem
  aceitasse por último), aparece na tela de Equipe e é revogável. Reconvidar o
  mesmo email substitui o pendente em vez de somar outro link válido — e a
  verificação de limite sabe disso, senão corrigir o papel de um convite seria
  recusado pelo convite que a correção ia apagar. `POST /tenants/me/members`
  deixou de existir
- **Segundo fator por TOTP ✅ — sem fornecedor nenhum.** Estava na lista como
  "SSO/MFA, depende de terceiro", e só a primeira metade dependia. TOTP
  (RFC 6238) funciona com qualquer aplicativo autenticador e cabe na biblioteca
  padrão: `hmac`, `base64`, vinte linhas em `app/core/totp.py` — verificadas
  contra os seis vetores publicados na especificação, que é o que sustenta a
  decisão de não trazer dependência para o servidor que guarda a senha de email e
  a chave de IA de cada cliente.

  Por que antes de SSO: uma senha vazada abre as duas credenciais que cada
  empresa configurou aqui. E por que não SMS: depende de operadora, custa por
  mensagem e cai com troca de chip — fator fraco é pior que ausência, porque
  parece proteção.

  O que o fluxo recusa, com teste para cada um: o mesmo código duas vezes (o
  passo aceito fica guardado); cinco chutes (e o contador roda em transação
  própria, senão a recusa desfaria o incremento junto); trocar o segredo com a
  sessão aberta (token roubado não aponta o fator para outro celular); desligar
  sem senha **e** código; e o **convite como porta de fuga** — aceitar convite
  emite sessão, então passou a exigir o mesmo fator, senão bastava convidar o
  email de alguém e entrar com a senha vazada.

  Três coisas apareceram no caminho: a segunda etapa do login mandava email e
  senha **vazios**, porque o React limpa formulário depois de Server Action (a
  conta com fator ligado não tinha caminho de entrada pela tela); a confirmação
  de desligamento era renderizada dentro do galho que desaparecia ao desligar; e
  `logout()` existia como ação **sem botão em lugar nenhum** — exigir código para
  entrar não protege um computador compartilhado se não há como sair
- **As recusas do web app ✅ (quinta rodada de revisão adversarial).** A camada
  que nunca tinha sido revisada, e a que o leigo toca. Cinco achados:

  1. **Auditoria derrubava a tela para dois dos quatro papéis.** `AUDIT_READ` é de
     admin para cima; o menu oferecia o link a todo mundo, e a resposta 403 virava
     `ApiError` sem ninguém pegar — o que o Next mostra como "A server error
     occurred", em inglês, com um número de erro. Agora o link só aparece para
     quem pode, e a tela explica a recusa para quem chegar por link salvo ou tiver
     o papel rebaixado com a aba aberta
  2. **`/exportar` respondia 500 com o corpo vazio** para quem não administra: o
     botão já não aparecia, mas a URL é alcançável, e o navegador baixava um
     arquivo de erro sem dizer o que houve. Agora devolve o 403 com a frase
  3. **Id inexistente na URL derrubava a tela.** Endereço com uuid no meio é coisa
     que se cola pela metade, se guarda depois de o registro sair e se repassa
     velho. Agora dá página não encontrada, em português, com saída
  4. **Quem tinha duas empresas não conseguia chegar na segunda.**
     `POST /auth/switch-tenant` existe desde o primeiro sprint e nunca teve tela;
     o login escolhe sempre o vínculo mais antigo. O convite com aceite tornou o
     caso comum — a mesma conta servindo duas empresas é o caminho normal agora.
     A barra ganhou o seletor, e a troca invalida o cache de layout antes de
     redirecionar: sem isso, a navegação seguinte poderia servir do cache o que
     foi renderizado com o token da empresa anterior
  5. **A rota da troca não tinha teste nenhum** — nem tela, o que explica. Agora
     tem três, e os dois que importam são as recusas: `tenant_id` vem do cliente,
     então sem a verificação de vínculo esta seria a porta mais curta para o dado
     de outra empresa

  Duas redes novas por baixo de tudo: `app/not-found.tsx` e `app/error.tsx`, em
  português e com saída, porque o padrão do Next é uma tela em inglês com um
  número e nada mais
- **Formatação uniforme.** O CI roda `ruff check`, não `ruff format --check`, e
  42 dos 124 arquivos do backend divergem do formato canônico. Rodar `ruff format`
  e passar a checá-lo no CI é mecânico — ficou fora deste PR de propósito, porque
  um diff de 32 arquivos no meio de uma revisão atrapalha quem revisa
- **Freio em dólar, não só em unidade ✅.** A cota do plano conta unidades — a
  moeda interna, que o cliente compra — e execução que falha não consome unidade
  nenhuma, de propósito: o cliente não paga cota por trabalho que não foi
  entregue. Só que o dólar foi gasto. Agora existe `ai_cost_usd_per_month`,
  verificado junto com a cota de unidades antes de rodar qualquer agente, editável
  por contrato no painel e visível nas duas telas (o consumo do mês mostra o teto
  ao lado do gasto; o painel mostra "US$ X de US$ Y" por empresa).

  A regra é "já passou, não começa outra", e não "cabe mais uma": o custo de uma
  execução só existe depois dela. O excesso possível é de uma execução, e essa
  está limitada pelo teto por execução (US$ 0,50). **Nasce ilimitado nos três
  planos** — quanto vale a pena gastar com cada cliente é decisão comercial de
  quem opera, não número para um default inventar
- **Duas unidades de consumo declaradas e não construídas.**
  `deep_research` (5 unidades) e `voice_interaction` (10 unidades) existem na
  tabela de consumo, e `voice` aparece nos recursos do plano Enterprise — mas
  não há código atrás de nenhuma das duas. Hoje são promessa no modelo de dados,
  não funcionalidade; ficam aqui para que ninguém as venda antes de existirem
- **Rotação da chave de cifra ✅.** O risco real nunca foi *onde* a chave Fernet
  mora: era não haver caminho para trocá-la. Se ela vazasse, a única resposta era
  "perca toda credencial de email e chave de API já salva" — o que na prática
  significa nunca trocar, e é assim que um incidente vira permanente.

  Agora a chave é uma lista (`MultiFernet`): a primeira cifra, qualquer uma
  decifra. `scripts/rotacionar_chave.py` recifra o que está guardado sem o
  conteúdo passar pelo nosso código, `--verificar` sai com erro se algum segredo
  não abrir — é o que autoriza apagar a chave antiga —, e a API avisa no log
  enquanto a rotação está pela metade. A sequência está em `docs/06-publicar.md`.

  As colunas cifradas ficam **declaradas** em `app/core/segredos_guardados.py`,
  com tripwire: uma rotação que esquece uma coluna não falha, ela apaga em
  silêncio a única cópia daquele segredo. O teste procura no código toda escrita
  cifrada e falha nomeando a coluna que ficou fora — e `mfa_secret` é a prova de
  que uma lista por convenção de nome (`*_encrypted`) não serviria
- Secrets manager externo (hoje a chave Fernet vive no `.env` do servidor; com a
  rotação no lugar, o vault deixou de ser o que separa um vazamento de uma perda)
- **Rate limiting distribuído ✅ — o balde deixou de ser por processo.** O freio
  contava dentro de cada processo, e a conta do processo não é a conta da
  aplicação: com duas réplicas atrás do proxy, o teto anunciado de 300 por minuto
  passava a deixar passar 600, sem nada na configuração dizendo isso. Num dia de
  incidente, é a diferença entre conter e não conter.

  `app/core/limitador.py` tem dois backends com a mesma interface, e `REDIS_URL`
  escolhe. O de Redis faz a janela deslizante num sorted set com **um** script
  Lua: atômico, porque sem isso duas réplicas que leem "299 usados" no mesmo
  milissegundo deixam as duas passarem — o bug que o backend compartilhado existe
  para impedir. As chaves expiram sozinhas (nada de varredura) e a contagem
  sobrevive a um reinício da API.

  Quando o Redis cai, o freio **não** desaparece: volta ao balde em memória, por
  réplica, com uma linha no log por episódio. Abrir tudo convidaria o abuso
  justamente no dia em que um Redis morre; recusar tudo derrubaria a aplicação
  por causa de um serviço auxiliar. O Redis entra por `--profile escala`, fora do
  caminho de quem roda um servidor só — um container a mais para manter é custo
  real de quem opera.

  Doze testes contra um Redis de verdade, não contra dublê: o que se verifica é
  atomicidade, e um dublê em Python concordaria com a implementação errada.
  Inclusive o teste do contraste — duas instâncias do backend de memória deixando
  passar o dobro, que é o defeito documentado.
- Política de **retenção** automática por tenant (a exportação já existe)
- Busca vetorial na base de conhecimento — exige fornecedor de embeddings
- **SSO** para contratos enterprise — depende do provedor de identidade **do
  cliente** (Okta, Entra, Workspace), não de uma conta nossa. Desenvolvível
  contra um Keycloak local quando um contrato pedir
