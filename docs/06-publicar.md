# Publicar a aplicação

O que segue é o caminho de um servidor vazio até a aplicação no ar, com HTTPS,
respondendo num domínio seu. São cinco passos e nenhum deles exige saber
programar.

A stack sobe com Docker: banco, API, worker, web e um proxy que cuida do
certificado. Roda em qualquer máquina Linux com Docker — Hetzner, DigitalOcean,
Contabo, AWS, ou um servidor na sua sala.

## 1. Um servidor

Peça a menor máquina que aguente: **2 vCPU e 4 GB de RAM** é o piso confortável
(o build do frontend é o que pede memória). Ubuntu 24.04 serve.

Instale o Docker:

```
curl -fsSL https://get.docker.com | sh
```

## 2. Aponte o domínio

No painel do seu DNS, crie um registro **A** apontando o subdomínio para o IP do
servidor:

```
app.suaempresa.com  →  203.0.113.10
```

Faça isso **antes** do passo 4: o certificado é emitido na primeira subida, e
para isso o Let's Encrypt precisa resolver o nome. Propagação costuma levar
minutos; `dig app.suaempresa.com` mostra quando terminou.

## 3. Traga o código

```
git clone https://github.com/theneilagencia/ia-sdr.git
cd ia-sdr
```

## 4. Publique

```
cd deploy
./publicar.sh app.suaempresa.com voce@suaempresa.com
```

O script gera todas as senhas e chaves, sobe os cinco serviços e espera a API
ficar pronta. A primeira vez demora alguns minutos — é o build das imagens.

Se algo falhar, ele mostra o log da API e para. Rodar de novo é seguro.

### Guarde o `deploy/.env`

O script grava ali as senhas do banco e três chaves:

- **`SECRETS_ENCRYPTION_KEY`** decifra as credenciais de email e as chaves de API
  que as empresas salvarem. **Perder esse arquivo é perder o acesso a tudo isso** —
  não tem recuperação, por desenho. Trocá-la, porém, tem caminho: ver *Trocar a
  chave de cifra* em Manutenção.
- **`JWT_SECRET`** assina os tokens de sessão. Rotacioná-lo derruba todas as
  sessões abertas, o que é exatamente o que se quer depois de um vazamento.
- **`UNSUBSCRIBE_SECRET`** assina os links de descadastro, que **não expiram**:
  descadastro de dois anos atrás continua valendo. **Não rotacione este.** Se o
  trocar, todo link já enviado passa a devolver "link inválido" — e honrar o
  pedido de quem não quer mais receber email é obrigação legal, não cortesia. Ele
  é separado do `JWT_SECRET` justamente para que a rotação de um não leve o outro.

Copie o arquivo para um gerenciador de senhas antes de seguir.

## 5. Crie a primeira empresa

```
docker compose -f docker-compose.prod.yml exec api \
  python -m scripts.criar_empresa --nome "Sua Empresa" --email voce@suaempresa.com
```

A senha aparece uma vez na tela. Entre em `https://app.suaempresa.com` com ela.

Cada cliente novo é outra empresa: rode o mesmo comando com outro nome e outro
email. Os dados de uma nunca aparecem para a outra — isso é garantido pelo banco,
não pelo código da aplicação.

## Depois de entrar

Duas configurações por empresa, ambas em **Configurações**, ambas testadas antes
de salvar:

- **Chave da Anthropic.** É o que faz os agentes pensarem. Sem ela, a empresa usa
  a aplicação mas os agentes não trabalham. Cada empresa paga o próprio consumo.
- **Conta de email.** Gmail, Outlook ou um SMTP próprio. É deste endereço que as
  abordagens saem e é nele que as respostas chegam.

Com as duas no lugar, o funil gira: importe uma lista, os agentes pesquisam,
pontuam e escrevem, você aprova, o email sai.

## Manutenção

**Atualizar** para a última versão:

```
git pull
cd deploy && ./publicar.sh
```

Sem apagar nada: o `.env` e o volume do banco são preservados, as migrations
aplicam sozinhas na subida.

**Backup** — o que importa são duas coisas:

```
# o banco
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U ia_sdr_admin ia_sdr | gzip > ia-sdr-$(date +%F).sql.gz

# e o deploy/.env, que você já copiou no passo 4
```

Um dump sem o `.env` não restaura as credenciais das empresas: o dump guarda o
texto cifrado, a chave está no `.env`.

**Tirar os segredos do `.env`** (gerenciador externo), se a sua operação exigir.
Qualquer campo aceita vir de **arquivo**: `JWT_SECRET_FILE=/run/secrets/jwt` vale
sobre `JWT_SECRET`. É assim que Vault com agente, AWS Secrets Manager via ECS,
Kubernetes Secret, Docker secret e systemd credentials entregam segredo — um
arquivo no disco, com dono e permissão —, então isto serve a todos eles sem SDK de
fornecedor nenhum dentro da aplicação.

```
# no deploy/.env
JWT_SECRET=
JWT_SECRET_FILE=/run/secrets/jwt
SECRETS_ENCRYPTION_KEY=
SECRETS_ENCRYPTION_KEY_FILE=/run/secrets/fernet
```

E monte o diretório nos serviços `api` e `worker`
(`- /caminho/dos/segredos:/run/secrets:ro`). Arquivo que não existe ou está vazio
**derruba a subida** de propósito: cair no valor padrão seria subir com o segredo
de desenvolvimento que está publicado no repositório — e isso não quebra nada na
hora, só deixa qualquer pessoa assinar token válido para qualquer empresa. No log
aparece o **nome** do campo que veio de arquivo, nunca o valor.

**Trocar a chave de cifra** (rotação). A resposta a "a chave vazou" não pode ser
"perca tudo": a chave é uma lista, a primeira cifra e qualquer uma decifra. A
ordem dos passos é o que separa rotacionar de perder dado.

```
# 1. gere a nova
openssl rand -base64 32 | tr '+/' '-_'

# 2. no deploy/.env: a nova em SECRETS_ENCRYPTION_KEY, e a que estava lá em
#    SECRETS_ENCRYPTION_KEYS_PREVIOUS

# 3. suba. Nada quebra: a nova cifra, a antiga ainda decifra
cd deploy && ./publicar.sh

# 4. recifre o que está guardado
docker compose -f docker-compose.prod.yml exec api python -m scripts.rotacionar_chave

# 5. confira antes de tirar a rede
docker compose -f docker-compose.prod.yml exec api python -m scripts.rotacionar_chave --verificar

# 6. só agora apague SECRETS_ENCRYPTION_KEYS_PREVIOUS do .env e suba de novo
```

O passo 5 existe para que o passo 6 não seja um palpite: ele sai com erro se
algum segredo não abrir com as chaves configuradas. Fazer o 6 antes do 4 torna
ilegível toda credencial de email e chave de API que as empresas já salvaram — e
o estrago não aparece na hora, aparece no próximo disparo. Enquanto a chave antiga
estiver na lista, a API avisa no log a cada subida.

**Fechar o mês.** No **Painel da plataforma**, a seção de fechamento gera uma
fatura por empresa ativa com o consumo do mês que acabou. Antes disso, cada
empresa precisa do contrato preenchido (mensalidade e, se houver, o preço da
unidade de IA acima da cota) — sem preço, a fatura fecha mostrando o consumo e
cobrando zero, de propósito: a plataforma não inventa quanto você cobra.

Emitir congela os números daquela fatura; refazer o fechamento depois disso
recalcula só os rascunhos. A nota fiscal continua saindo de onde você já emite —
aqui fica o valor, o estado dele e o custo real de IA do período ao lado, que é o
que diz se aquele contrato fecha em dinheiro.

**Mais de uma réplica da API** (quando um servidor deixar de dar conta). O freio
de requisições conta dentro de cada processo: com duas réplicas, o teto de 300
por minuto passa a deixar passar 600, sem nada na configuração dizendo isso. O
conserto é um balde compartilhado:

```
# no deploy/.env
REDIS_URL=redis://redis:6379/0

# e suba o Redis junto (ele fica fora do caminho normal de propósito)
cd deploy && docker compose -f docker-compose.prod.yml --profile escala up -d
```

Se o Redis cair, a aplicação **não** cai: o freio volta a contar por processo e a
API avisa no log a cada subida e no primeiro erro. Pior do que o ideal, melhor do
que abrir a porta — e o log diz qual dos dois está valendo.

**Descarte automático** (retenção). Por empresa, em **Configurações → Descarte
automático**: nasce desligado, e ligado descarta o rastro operacional mais velho
que o prazo, uma vez por dia. A tela mostra o que sairia **antes** de sair, e diz
o que nunca é apagado — o registro de quem pediu para não receber mais email, o
consumo ainda não faturado, a auditoria dos últimos 90 dias e o rascunho à espera
de revisão. Nada disso precisa de comando no servidor.

**Ver o que está acontecendo:**

```
docker compose -f docker-compose.prod.yml logs -f api worker
```

O worker é quem lê as caixas de email, despacha o que foi aprovado e responde
quando um lead escreve de volta. Se as respostas pararem de chegar, é o log dele
que conta o motivo.

## Por que a API pode recusar subir

De propósito, e a mensagem diz qual é o caso:

- **Segredo de exemplo.** `JWT_SECRET` ou `SECRETS_ENCRYPTION_KEY` com o valor
  que está no repositório. Qualquer pessoa que leu o código conseguiria assinar
  um token válido para qualquer empresa.
- **`PUBLIC_BASE_URL` sem HTTPS ou apontando para localhost.** É a base do link
  de descadastro que vai em todo email: errada, o link não abre.
- **`APP_BASE_URL` sem HTTPS ou apontando para localhost.** É a base do link de
  convite, que abre a tela de aceite. Errada, ninguém entra na empresa que
  convidou; em `http://`, o token de aceite — que é uma credencial de uso único —
  viaja em claro. O `docker-compose.prod.yml` deriva as duas do `DOMAIN`, então
  isto só dá problema em instalação feita à mão.
- **Role do banco com privilégio demais.** Se a aplicação conectasse como
  superusuário, o Row Level Security seria ignorado e as empresas enxergariam os
  dados umas das outras. É melhor não subir do que subir assim.
