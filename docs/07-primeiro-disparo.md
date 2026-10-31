# O primeiro disparo real

Tudo nesta plataforma está testado com cliente falso: mais de trezentos testes exercitam a
mecânica sem rede e sem gastar token. O que nenhum teste responde é a única
pergunta que decide se o produto serve: **o texto que os agentes escrevem
convence uma pessoa de verdade?**

Este documento é o caminho do primeiro disparo com chave real num alvo
controlado. Leva cerca de uma hora, a maior parte dela preenchendo o que os
agentes leem — e é aí que o resultado é decidido, não no modelo.

## Antes de começar

Três coisas, nesta ordem de importância:

1. **Uma chave da Anthropic** (console.anthropic.com → API Keys). O consumo cai
   na conta da própria empresa; nada é compartilhado entre empresas.
2. **Uma conta de email que você possa queimar.** Não use o domínio principal da
   empresa no primeiro teste. Se for Gmail ou Outlook com 2FA, gere uma **senha
   de app** — a senha normal não funciona em SMTP.
3. **Um endereço seu para receber**, diferente do que envia. É o alvo do primeiro
   disparo: você vai ler o email como o lead leria.

Reserve também **um prospect real** — uma empresa que você conhece bem o
suficiente para julgar se a pesquisa acertou. Julgar a pesquisa de uma conta que
você não conhece não diz nada.

## 1. Configurações (10 min)

Em **Configurações**, três cards:

- **Inteligência artificial** — cole a chave e use *Só testar* antes de salvar.
- **Email de envio** — escolha o provedor, preencha e teste. O teste faz login
  de verdade no SMTP e no IMAP; se falhar, a mensagem diz qual dos dois.
- **Volume de envio** — o padrão já é conservador (30/dia, aquecimento começando
  em 10 e subindo 5, só em horário comercial). Para o primeiro teste, baixe o
  limite diário para **5**: você quer ler cada mensagem, não produzir volume.

O card do **CRM (RAVI)** é opcional e pode ficar para depois — sem ele tudo
funciona, o lead só não aparece no CRM.

## 2. Cérebro (15 min — o passo que mais importa)

Em **Cérebro**, preencha pelo menos:

- **Posicionamento** — a frase que explica o que vocês fazem.
- **Produtos** — o que vendem e o que cada um resolve.
- **Objeções conhecidas** — o que os clientes respondem, e o que responder. O
  agente de conversa **escala para humano** o que não estiver aqui.
- **Tom de voz** e **nunca escrever** — é o que separa um email seu de um email
  de robô.
- **Limites da IA** — "nunca prometer" e "sempre passar para uma pessoa quando".

Se o primeiro rascunho sair genérico, é quase sempre aqui que está a causa. O
modelo não inventa posicionamento; ele repete o que encontrou.

## 3. Campanha (10 min)

Em **Campanhas**, crie uma e abra *O que os agentes leem*:

- **ICP** — característica e valor: setor, porte, país. É contra isto que a
  pesquisa mede a aderência e dá a nota.
- **Oferta** — o que vendemos nesta campanha, resultado esperado e prova.
- **Mensagem** — ângulo, gancho, o que pedir, o que não usar.
- **Critérios de qualificação** — critério e o que conta como atendido. Sem
  nenhum, o agente de qualificação **se recusa a julgar**. Se você usar os nomes
  *orçamento*, *autoridade*, *necessidade* e *prazo*, o RAVI recebe o BANT
  preenchido.

O cartão da campanha lista o que ainda falta para ela produzir. Ative quando
estiver satisfeito — em rascunho, a cadência não anda.

## 4. Base de conhecimento (5 min)

Em **Conhecimento**, cole o playbook e as perguntas frequentes. O agente de
conversa responde **somente** com o que estiver aqui; base vazia é um agente que
escala tudo, todo dia — e um agente assim é desligado na primeira semana.

## 5. A lista

Em **Prospects**, importe o CSV (ou use o alvo controlado). Precisa de uma coluna
com o nome da empresa e outra com o nome da pessoa; email, cargo, setor, país e
LinkedIn entram se estiverem lá. O relatório diz quantas linhas entraram, quantas
já estavam, quantas ficaram de fora com o número da linha, e **quantas vieram sem
email** — para essas a abordagem não sai.

## 6. O funil, um agente por vez

Em **Agentes**, ou direto no detalhe do prospect:

1. **Pesquisar a conta.** Vai para a fila (busca na web leva minutos). Quando
   terminar, abra o detalhe do prospect: leia os achados e a nota. **Pergunta a
   se fazer:** os fatos são verdade, e a fonte existe? Se a pesquisa estiver
   errada, pare aqui — a abordagem vai amplificar o erro.
2. **Escrever a abordagem.** O texto vai para **Revisão**. Leia com o olho de
   quem receberia:
   - cita algo específico da conta, ou é genérico?
   - promete algo que não está no playbook?
   - o pedido é pequeno o suficiente para um "sim" custar pouco?
3. **Aprovar e enviar.** Confira a caixa de destino, e olhe o email como um
   lead: assunto, remetente, primeira linha.
4. **Responda como o lead** — de dúvida simples a objeção difícil. Em
   **Revisão**, use *Buscar respostas* (ou espere o worker) e depois peça o
   rascunho ao agente de conversa na tela da conversa. Verifique se ele respondeu
   pela base de conhecimento e se escalou o que devia escalar.
5. **Qualificar.** No detalhe do prospect, o veredito vem critério a critério,
   com a evidência. Critério dado como atendido sem evidência é rebaixado pelo
   código — se aparecer muito "não atendido" com evidência vazia, o problema são
   os critérios, não o agente.
6. **Cadência.** Em **Cadências**, crie uma com dois ou três toques, ative,
   inscreva o prospect e use *Avançar cadências* para não esperar o relógio.
7. **RAVI**, se conectado: no detalhe do prospect, *Enviar agora*. Prospect sem
   nota não sobe.

## O que medir (não é opinião)

Anote, dos primeiros dez prospects:

| Número | Onde olhar | O que ele diz |
|---|---|---|
| Rascunhos aprovados **sem editar** | Revisão | se o agente escreve no seu tom |
| Rascunhos que citam a pesquisa | o texto | se a personalização é real |
| Escaladas para humano | conversas marcadas | se a base de conhecimento está rasa |
| Respostas recebidas | Conversas | o único número que o cliente vai olhar |
| Custo por prospect | Funil (consumo do mês) | se a conta fecha |

Um agente que produz texto bom e escala tudo tem base de conhecimento vazia. Um
que nunca escala e erra o conteúdo tem base errada. Os dois números juntos dizem
mais do que ler dez rascunhos.

## Os erros que aparecem primeiro

- **"Esta empresa ainda não tem uma chave da Anthropic configurada"** — a chave é
  por empresa; configure em Configurações.
- **Login recusado no SMTP** — com 2FA, senha normal não serve; gere senha de
  app.
- **"Sem pesquisa desta conta: dispare o agente de pesquisa antes de abordar"** —
  a ordem importa, e é proposital: abordagem sem pesquisa é email genérico.
- **"Campanha sem critérios de qualificação"** — preencha os critérios; o agente
  se recusa a julgar sem eles.
- **Cota estourada** — o painel da plataforma mostra o consumo e deixa ajustar o
  limite contratado.
- **Nada sai da fila** — o processo trabalhador precisa estar no ar. A tela de
  agentes avisa quando há trabalho parado há mais de cinco minutos.

## Depois do primeiro disparo

Guarde os dez primeiros rascunhos e as respostas. São eles que dizem o que
ajustar: quase sempre é **Cérebro, ICP ou base de conhecimento** — o que o agente
lê —, e não o modelo nem o prompt.
