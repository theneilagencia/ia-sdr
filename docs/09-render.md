# Publicar no Render

Este é o caminho para quem prefere não administrar servidor. O outro caminho, em
[06-publicar.md](06-publicar.md), é uma máquina Linux com Docker e um proxy —
mais barato e mais sob controle, ao custo de cuidar da máquina.

A troca, em uma frase: **no Render você paga cerca de cinco vezes mais e não
atualiza sistema operacional, não configura certificado e não monitora disco.**
Se isso vale o dinheiro é decisão sua; o que segue supõe que já foi decidido.

A configuração inteira está em [`render.yaml`](../render.yaml), na raiz do
repositório, com o porquê de cada decisão escrito ao lado dela. Vale ler antes.

## O que sobe

| Recurso | Papel | Público? |
|---|---|---|
| `ia-sdr-web` | as telas, e o único endereço que responde no seu domínio | sim |
| `ia-sdr-api` | a API, num serviço **privado**, sem porta para a internet | não |
| `ia-sdr-worker` | lê as caixas, despacha o aprovado, avança as cadências | não |
| `ia-sdr-db` | PostgreSQL gerenciado, sem endereço externo | não |

`/api/*` chega à API por um encaminhador do Next
(`web/app/api/[...caminho]/route.ts`), então é um domínio só —
exatamente como o Caddy faz no servidor próprio. Isso importa por três razões
concretas: o cookie de sessão fica same-site, o link de descadastro que vai em
todo email sai pelo mesmo endereço da aplicação, e a API não tem superfície
exposta para ninguém varrer.

## Antes de começar

1. **Um domínio.** Não é opcional na prática: email frio saindo de um
   `onrender.com` não tem SPF, DKIM nem DMARC seus, e vai para spam. Você vai
   colar esse endereço em três variáveis no passo 2.
2. **A chave de cifra, gerada e guardada.** É ela que decifra as credenciais de
   email e as chaves de API que as empresas salvarem. Gere:

   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   E guarde no seu gerenciador de senhas **antes** de colar no Render. Perdê-la é
   perder o acesso a tudo o que está cifrado, sem recuperação — por desenho.
   Trocá-la tem caminho (`scripts/rotacionar_chave.py`); perdê-la não tem.
3. **A região.** `oregon` no arquivo. Trocar depois exige banco novo: `region`,
   `user`, `databaseName` e `postgresMajorVersion` do Postgres **não mudam**
   depois de criados.

## 1. Criar o Blueprint

No painel do Render: **New → Blueprint**, escolha este repositório e a branch. Ele
lê o `render.yaml` e mostra os quatro recursos antes de criar nada.

## 2. Os sete campos que ele pergunta

São os valores que não podem estar no repositório nem ser inventados por um
padrão. O Render pergunta uma vez, na criação, e nunca sobrescreve.

| Variável | Onde | O que colar |
|---|---|---|
| `SECRETS_ENCRYPTION_KEY` | api | a chave Fernet que você gerou e guardou |
| `PUBLIC_BASE_URL` | api | `https://app.suaempresa.com` |
| `APP_BASE_URL` | api | `https://app.suaempresa.com` (o mesmo) |
| `CORS_ORIGINS` | api | `["https://app.suaempresa.com"]` — com colchetes e aspas |
| `SECRETS_ENCRYPTION_KEY` | worker | a **mesma** chave da api |
| `PUBLIC_BASE_URL` | worker | o mesmo endereço |
| `APP_BASE_URL` | worker | o mesmo endereço |

Sim, os mesmos três valores são pedidos duas vezes. A causa é uma limitação do
Render: grupo de variáveis não aceita segredo que o painel pergunta (`sync:
false`), e colocar um valor de exemplo no grupo faria cada atualização do
blueprint sobrescrever o segredo real. Repetir a pergunta é feio e correto.

Se você ainda não tem o domínio, cole o endereço `onrender.com` que o Render der
ao `ia-sdr-web` e volte aqui depois do passo 4 — a aplicação **recusa subir** com
endereço de desenvolvimento ou sem HTTPS, o que é melhor do que subir mandando
link quebrado.

O que ele **não** pergunta, porque gera sozinho: `JWT_SECRET`,
`UNSUBSCRIBE_SECRET` e a senha do role de aplicação do banco.

## 3. O primeiro deploy

A ordem é: banco, depois o pré-deploy da api, depois os três serviços.

O pré-deploy da api roda `alembic upgrade head` e `python -m
scripts.bootstrap_roles`. É onde o schema, as políticas de Row Level Security e o
**segundo role do banco** são criados. Ele é idempotente e roda em todo deploy.

Duas coisas para esperar:

- **O worker pode reiniciar algumas vezes na primeiríssima subida**, até as
  migrations terminarem. Ele recusa subir contra um banco sem as políticas de
  isolamento, em vez de subir e trabalhar sem elas.
- **Se o pré-deploy falhar dizendo que o usuário do banco não pode criar role**,
  esse provedor não serve para esta aplicação, e a mensagem explica por quê: sem
  um segundo role, a aplicação conectaria como dona das tabelas e o Row Level
  Security não isolaria empresa nenhuma. Não há contorno seguro.

  Dizer com antecedência se o usuário do Render tem esse privilégio exigiria uma
  conta e um banco criado — não é algo que a documentação de provedor costume
  afirmar. Então o código trata os dois desfechos, e o primeiro deploy responde
  em um minuto. Se quiser saber antes, crie só o banco, libere seu IP no painel e
  rode:

  ```
  psql "$URL_EXTERNA_DO_BANCO" -c \
    "select rolcreaterole from pg_roles where rolname = current_user"
  ```

  `t` é sinal verde. O comportamento nos dois casos está preso por teste em
  `backend/tests/test_postgres_gerenciado.py`, com um banco cujo dono tem
  exatamente os privilégios de um Postgres gerenciado.

Um deploy que falha no pré-deploy **não entra no ar** — a versão anterior
continua servindo. É o comportamento que se quer de uma migration que não
aplicou.

## 4. O domínio

Em `ia-sdr-web` → **Settings → Custom Domains**, adicione `app.suaempresa.com` e
crie no seu DNS o `CNAME` que o Render mostrar. O certificado é emitido e
renovado por ele.

Depois que o domínio responder, volte às três variáveis do passo 2 (nos **dois**
serviços) e troque o endereço `onrender.com` pelo seu. Os dois serviços
reiniciam.

## 5. A primeira empresa

Nenhuma empresa nasce junto com a instalação: quem cria é você, por linha de
comando. No painel, em `ia-sdr-api` → **Shell**:

```
python -m scripts.criar_empresa --nome "Sua Empresa" --email dono@suaempresa.com
python -m scripts.promover_admin --email dono@suaempresa.com
python -m scripts.pronto_para_ir_ao_ar
```

O primeiro imprime a senha inicial — anote, ela não é mostrada de novo. O segundo
concede a marca de platform admin, sem a qual o painel da plataforma e o
fechamento do mês ficam inacessíveis. O terceiro é o pré-voo descrito em
[08-go-live.md](08-go-live.md): ele diz, empresa por empresa, o que ainda falta
para operar.

## O custo

Preço de nuvem muda; confirme na página de preços antes de decidir. A
**composição** é o que não muda:

| Recurso | Plano |
|---|---|
| `ia-sdr-web` | uma instância paga |
| `ia-sdr-api` | uma instância paga |
| `ia-sdr-worker` | uma instância paga |
| `ia-sdr-db` | Postgres pago, menor tamanho |

São **quatro recursos pagos**, e na ordem de grandeza de meados de 2026 isso dá
algo perto de **US$ 25 a 30 por mês** — contra os US$ 5 a 10 de uma máquina única
rodando os mesmos quatro processos em containers. O consumo da Anthropic é
separado, em qualquer um dos dois caminhos, e é cobrado por empresa dentro da
aplicação.

Onde economizar, se o orçamento aperta: os três serviços podem ficar no plano
mais baixo que o Render oferecer para cada tipo. O que **não** dá para cortar é o
worker — sem ele a aplicação abre, mostra tudo e não faz nada acontecer.

## O plano grátis não serve

Não é aviso genérico; são dois impedimentos concretos:

1. **Não existe worker no plano grátis.** Sem worker, nenhuma resposta é lida,
   nenhum email aprovado é despachado e nenhuma cadência avança. A aplicação fica
   bonita e parada.
2. **O Postgres grátis expira.** Ele tem prazo de validade (um mês, na política
   vigente), e quando expira os dados vão embora. Não é banco para guardar
   histórico de cliente.

Some a isso que serviço grátis hiberna por inatividade, e o primeiro acesso do
dia demora. Para experimentar a aplicação, o caminho mais honesto é local
(`docker compose up` em desenvolvimento), não um Render grátis que mente sobre o
que a plataforma faz.

## Redis, no dia em que houver duas instâncias

O freio de requisições conta dentro de cada processo. Com uma instância da api,
isso é exato. Com duas, o teto anunciado passa a valer o dobro — cada uma conta o
seu balde. Quando esse dia chegar, adicione ao `render.yaml`:

```yaml
  - type: keyvalue
    name: ia-sdr-redis
    region: oregon
    plan: starter
    maxmemoryPolicy: noeviction
    ipAllowList: []
```

E em `ia-sdr-api`, mais uma variável:

```yaml
      - key: REDIS_URL
        fromService:
          name: ia-sdr-redis
          type: keyvalue
          property: connectionString
```

Se o Redis cair, o freio volta a contar por processo e a API avisa no log: não
abre e não derruba.

## O que o Render não faz por você

O pré-voo (`pronto_para_ir_ao_ar`) cobre o que é verificável de dentro. O que
sobra é o mesmo de [08-go-live.md](08-go-live.md), com uma diferença e uma
adição:

- **O backup.** O Render faz backup do Postgres nos planos pagos, o que é melhor
  do que um `pg_dump` que você lembra de rodar. Continua valendo a regra: um
  backup que nunca foi restaurado não é backup. Restaure num banco novo uma vez,
  antes de precisar.
- **A chave de cifra não está no backup do banco.** O dump guarda o texto
  cifrado; a chave está numa variável de ambiente do Render. Se você perder a
  conta do Render, o backup não devolve credencial de email nem chave de API de
  nenhuma empresa. É por isso que ela vai para o seu gerenciador de senhas no
  passo 2, e não é gerada pelo painel.
- **SPF, DKIM e DMARC** do domínio de envio, que se configuram no DNS.
- **O primeiro disparo de verdade**, com alguém lendo o que a IA escreveu antes
  de aprovar: [07-primeiro-disparo.md](07-primeiro-disparo.md).

## Limites conhecidos deste caminho

Nenhum destes é bug; são consequências de escolhas, listadas para não serem
descobertas no pior momento.

- **O banco não tem endereço externo** (`ipAllowList: []`). Para rodar `psql` da
  sua máquina, libere seu IP no painel e feche depois. O caminho normal é o Shell
  da api, que já está dentro da rede privada.
- **`postgresMajorVersion`, `region`, `user`, `databaseName` e o `name` do banco
  não mudam** depois de criados. Mudar exige banco novo e migração à mão.
- **Ambiente de pré-visualização está desligado** (`previews.generation: "off"`).
  Ligar cria uma cópia paga da stack inteira a cada pull request aberto.
- **O deploy só acontece com o CI verde** (`autoDeployTrigger: checksPass`). Se o
  CI estiver quebrado por motivo alheio, nenhum deploy sai — o que é o
  comportamento desejado, e é bom saber que é ele.
