# Go-live

Publicar e estar pronto para operar são coisas diferentes. Este documento é a
distância entre as duas, na ordem em que ela se percorre, com o que a aplicação
verifica sozinha separado do que só uma pessoa pode fazer.

O passo a passo da publicação em si está em [06-publicar.md](06-publicar.md).
Aqui começa depois: a stack está de pé.

## O comando que diz o que falta

```
docker compose -f docker-compose.prod.yml exec api \
  python -m scripts.pronto_para_ir_ao_ar
```

Ele separa **o que impede de operar** (✗, e o comando sai com erro) do **que é
escolha consciente** (!, e não impede nada). Um pré-voo que reclama de tudo é um
pré-voo que ninguém roda duas vezes.

O que ele impede:

- role do banco com privilégio de ignorar Row Level Security — seria o isolamento
  entre empresas deixando de existir;
- segredo de exemplo ou endereço público sem HTTPS em produção;
- nenhuma conta com a marca de platform admin: sem ela, o painel da plataforma, o
  fechamento do mês e o suporte ficam inacessíveis, e a marca é concedida por
  linha de comando, no servidor;
- nenhuma empresa ativa;
- empresa sem chave da Anthropic (os agentes não trabalham) ou sem conta de email
  conectada (nenhuma abordagem sai e nenhuma resposta entra). Conta salva com erro
  conta como impedimento: parece configurada e não é.

O que ele só avisa:

- `UNSUBSCRIBE_SECRET` vazio — funciona, herdando o `JWT_SECRET`, mas rotacionar o
  segredo das sessões passaria a invalidar todo link de descadastro já enviado;
- sem `REDIS_URL` — basta para uma réplica; com duas, o teto do freio de
  requisições vale o dobro;
- empresa usando a chave da plataforma em vez da própria — é o interruptor entre
  "o cliente paga o consumo dele" e "você revende tokens";
- sem prazo de descarte automático e sem preço no contrato: os dois são o padrão,
  e os dois são decisão de quem opera.

## O que nenhum comando verifica

Isto não é lista de pendência do software; é o que exige uma pessoa e algumas
horas. Nenhum dos itens pode virar ✓ automático sem virar mentira.

1. **O backup restaura.** Um dump que nunca foi restaurado não é backup, é
   esperança. Restaure em outra máquina, suba a aplicação contra ele e entre.
2. **O `deploy/.env` está guardado fora do servidor.** O dump guarda o texto
   cifrado; a chave está no `.env`. Sem ele, o backup não devolve nenhuma
   credencial de email nem chave de API das empresas.
3. **DNS e certificado.** Abra a aplicação pelo domínio, por HTTPS, de fora da
   máquina. A emissão pelo Let's Encrypt depende do DNS já apontar para o
   servidor — é o único passo que nem o CI nem a validação local conseguem provar.
4. **SPF, DKIM e DMARC do domínio de envio.** Sem os três, email frio vai para
   spam e a reputação queima antes da primeira resposta. Isso se configura no DNS
   do domínio, não aqui.
5. **O primeiro disparo de verdade.** Chave real, alvo real, uma pessoa lendo o
   que a IA escreveu antes de aprovar. O passo a passo está em
   [07-primeiro-disparo.md](07-primeiro-disparo.md). Até ele acontecer, a
   qualidade do texto dos agentes é desconhecida — a mecânica está testada, o
   texto não.

## O que está verificado, e por quem

| O que | Como |
|---|---|
| Regras de negócio, isolamento, cotas, agentes, fila, email, cobrança, retenção | 512 testes contra PostgreSQL de verdade (`make test`) |
| As telas, ponta a ponta | 134 verificações num Chromium de verdade (`web/e2e/smoke.mjs`) |
| A stack de produção sobe, migra, roteia e responde por HTTPS | `deploy/validar_stack.sh`, e o job `stack` do CI |
| Esta instalação está configurada para operar | `python -m scripts.pronto_para_ir_ao_ar` |
| Backup, DNS, reputação de domínio e qualidade do texto | uma pessoa, uma vez, conforme acima |

## Primeira semana

- **Olhe o log do worker** (`docker compose ... logs -f worker`): é ele que lê as
  caixas, despacha o aprovado e avança as cadências. Se as respostas pararem de
  chegar, é o log dele que conta o motivo.
- **Deixe o volume baixo e o aquecimento ligado.** Provedor de email que vê
  volume novo e alto trata como spam, e recuperar reputação custa muito mais do
  que subir devagar.
- **Aprove tudo à mão nos primeiros dias.** É o desenho da plataforma — nada sai
  sem alguém ler —, e é nessa leitura que se descobre o que ajustar no Company
  Brain e nos critérios da campanha.
- **Confira o consumo em Configurações.** O teto de custo por empresa nasce
  ilimitado de propósito: quanto vale gastar com cada cliente é decisão comercial,
  não número para um padrão inventar. Defina o seu na primeira semana, com dado
  real na mão.
