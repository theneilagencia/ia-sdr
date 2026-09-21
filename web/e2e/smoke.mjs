/**
 * Fumaça de ponta a ponta: login, funil e o portão de aprovação.
 *
 * Cobre o que build e typecheck não pegam — Server Action que compila e
 * falha em execução, cookie que não chega, tela que renderiza vazia porque
 * a API respondeu 401. Roda contra a API e o web de verdade.
 *
 *   node e2e/smoke.mjs
 */
import { chromium } from "playwright";

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const API = process.env.API_URL ?? "http://localhost:8000";
const EMAIL = process.env.E2E_EMAIL ?? "owner@apymine.com";
const SENHA = process.env.E2E_PASSWORD ?? "demo-senha-12345";

const falhas = [];
function checar(condicao, descricao) {
  console.log(`${condicao ? "✓" : "✗"} ${descricao}`);
  if (!condicao) falhas.push(descricao);
}

/**
 * Espera a condição virar verdadeira, em vez de dormir um tempo fixo.
 *
 * Dormir dois segundos depois de um clique é uma corrida: passa na máquina de
 * quem escreveu e falha no runner mais lento — que foi exatamente o que
 * aconteceu, com duas execuções do mesmo commit dando resultados diferentes.
 * Um teste que falha por tempo ensina a ignorar teste vermelho, que é o pior
 * hábito que uma suíte pode criar.
 */
/** O marcador é um elemento oculto, então a espera é por presença no DOM. */
async function hidratada() {
  await page.waitForSelector('[data-hidratado="1"]', {
    state: "attached",
    timeout: 20000,
  });
}

async function ate(condicao, { limite = 20000, passo = 250 } = {}) {
  const fim = Date.now() + limite;
  for (;;) {
    if (await condicao()) return true;
    if (Date.now() >= fim) return false;
    await page.waitForTimeout(passo);
  }
}

/**
 * O que a API acha do estado, quando a tela discorda dela.
 *
 * Uma falha que só diz "✗ aprovar tira o rascunho" não distingue "a ação não
 * rodou" de "a ação rodou e a tela ficou parada" — e essas duas têm correções
 * opostas. Isto custa duas requisições e resolve a dúvida no log do CI.
 */
async function diagnosticar() {
  try {
    const login = await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: EMAIL, password: SENHA }),
    });
    const { access_token: token } = await login.json();
    const contar = async (status) => {
      const r = await fetch(`${API}/api/v1/messages?status=${status}`, {
        headers: { authorization: `Bearer ${token}` },
      });
      return (await r.json()).length;
    };
    const draft = await contar("draft");
    const queued = await contar("queued");
    console.error(
      `  diagnóstico: api.draft=${draft} api.queued=${queued} — ` +
        (draft === 0
          ? "a aprovação CHEGOU à API; a tela é que não atualizou"
          : "a aprovação NÃO chegou à API; o clique não virou ação"),
    );
    const marca = await page.locator('[data-hidratado="1"]').count();
    console.error(`  hidratação marcada na tela: ${marca === 1 ? "sim" : "não"}`);
  } catch (erro) {
    console.error("  diagnóstico falhou:", erro.message);
  }
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage();

try {
  await page.goto(`${WEB}/login`);
  await page.fill('input[name="email"]', EMAIL);
  await page.fill('input[name="password"]', SENHA);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${WEB}/`, { timeout: 15000 });
  checar(
    (await page.locator("h1").innerText()).includes("trabalhando"),
    "login leva ao funil",
  );
  checar(
    (await page.locator(".funnel .k").first().innerText()).length > 0,
    "funil renderiza com dados da API",
  );

  await page.goto(`${WEB}/drafts`);
  await page.waitForSelector(".cota", { timeout: 10000 });
  // Esperar por `.cota` prova que o servidor renderizou — não que o React já
  // assumiu a página. Clicar antes da hidratação engole o clique, e foi
  // exatamente isso que fez esta fumaça falhar de forma intermitente no CI:
  // passava na máquina rápida, estourava os 20 segundos no runner frio.
  await hidratada();
  checar(
    (await page.locator(".cota").innerText()).includes("enviados hoje"),
    "cota do dia aparece na tela de revisão",
  );
  // Contagem por seção: aprovar move o cartão de "revisão" para "aprovados",
  // então contar a página inteira daria o mesmo número e o teste passaria à toa.
  const emRevisao = () => page.locator('[data-secao="revisao"]').count();
  const antes = await emRevisao();
  checar(antes > 0, `fila de revisão tem rascunho (${antes})`);

  await page.locator('button:has-text("Aprovar")').first().click();
  const saiuDaRevisao = await ate(async () => (await emRevisao()) === antes - 1);
  checar(saiuDaRevisao, "aprovar tira o rascunho da revisão");
  if (!saiuDaRevisao) {
    // Diz de qual lado quebrou, em vez de deixar a próxima pessoa adivinhando:
    // se a API já não tem o rascunho, a aprovação funcionou e a tela não
    // atualizou; se ainda tem, o clique não chegou ao servidor.
    await diagnosticar();
  }
  checar(
    await ate(async () => (await page.locator('[data-secao="aprovados"]').count()) > 0),
    "o aprovado aparece na fila de envio",
  );

  await page.goto(`${WEB}/prospects`);
  checar((await page.locator("h1").innerText()) === "Prospects", "prospects carrega");

  await page.goto(`${WEB}/settings`);
  await page.waitForSelector('input[name="api_key"]', { timeout: 10000 });
  checar(
    (await page.locator("section.card h3").allInnerTexts()).length === 3,
    "configurações mostra IA, email e volume",
  );
  await page.locator('input[value="smtp"]').check();
  checar(
    (await page.locator('input[name="host"]').count()) === 1,
    "escolher servidor próprio revela host e porta",
  );

  // As três telas do backoffice: o caminho crítico de cada uma, não o CRUD
  // inteiro — esse está coberto pelos testes de backend.
  await page.goto(`${WEB}/brain`);
  await hidratada();
  const marca = `posicionamento ${Date.now()}`;
  await page.fill('textarea[name="positioning"]', marca);
  await page.locator('button:has-text("Salvar")').click();
  checar(
    await ate(async () => (await page.locator("p.ok").count()) > 0),
    "cérebro salva",
  );
  await page.reload();
  await hidratada();
  checar(
    (await page.locator('textarea[name="positioning"]').inputValue()) === marca,
    "o que foi salvo no cérebro volta na recarga",
  );

  await page.goto(`${WEB}/knowledge`);
  await hidratada();
  const documentosAntes = await page.locator("tbody tr").count();
  const colar = page.locator("section.card", { hasText: "Colar texto" });
  await colar.locator('input[name="title"]').fill(`Base ${Date.now()}`);
  await colar.locator('textarea[name="content"]').fill("O reembolso integral vale por trinta dias.");
  await colar.locator('button[type="submit"]').click();
  checar(
    await ate(async () => (await page.locator("p.ok").count()) > 0),
    "documento colado é indexado",
  );
  await page.reload();
  await hidratada();
  checar(
    (await page.locator("tbody tr").count()) === documentosAntes + 1,
    "o documento aparece na base",
  );

  await page.goto(`${WEB}/team`);
  await hidratada();
  checar((await page.locator("tbody tr").count()) >= 1, "equipe lista os membros");
  checar(
    (await page.locator("tbody tr", { hasText: EMAIL }).locator("select").count()) === 0,
    "o próprio usuário não tem seletor de papel",
  );

  // As telas que fazem o funil andar: campanha, lista e disparo. O caminho é
  // encadeado de propósito — a campanha recebe a lista, e a lista alimenta o
  // seletor de alvo do agente. Se um elo quebra, o teste aponta qual.
  await page.goto(`${WEB}/campaigns`);
  await hidratada();
  checar(
    (await page.locator(".pendencias li").first().innerText()).includes("critérios"),
    "a campanha diz o que falta para ela produzir",
  );
  const campanha = page.locator('[data-secao="campanha"]').first();
  await campanha.locator("summary").click();
  await campanha.locator('input[name="criterio_chave"]').first().fill("orçamento");
  await campanha
    .locator('textarea[name="criterio_valor"]')
    .first()
    .fill("tem verba aprovada para este ano");
  await campanha.locator('button:has-text("Salvar")').click();
  checar(
    await ate(async () => (await page.locator("p.ok").count()) > 0),
    "critério de qualificação salva",
  );
  await page.reload();
  await hidratada();
  const reaberta = page.locator('[data-secao="campanha"]').first();
  await reaberta.locator("summary").click();
  checar(
    (await reaberta.locator('input[name="criterio_chave"]').first().inputValue()) === "orçamento",
    "o critério volta na recarga",
  );

  await page.goto(`${WEB}/prospects`);
  await hidratada();
  const prospectsAntes = await page.locator("tbody tr").count();
  // Latin-1, ponto e vírgula e cabeçalho "E-mail": é como a planilha exportada
  // do Excel em português chega. Uma linha sem empresa e uma com email inválido
  // entram de propósito — o relatório tem de apontar as duas pela linha.
  const lista =
    "Empresa;Nome;E-mail;Cargo\n" +
    "Mineradora Açaí;José Antônio;jose@acai.com.br;Diretor\n" +
    ";Fulano Sem Empresa;fulano@x.com;\n" +
    "Britagem Norte;Carlos Dias;nao-e-email;COO\n";
  await page.setInputFiles('input[name="file"]', {
    name: "lista.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(lista, "latin1"),
  });
  await page.selectOption('select[name="campaign_id"]', { index: 1 });
  await page.locator('section.card button[type="submit"]').click();
  checar(
    await ate(async () => (await page.locator("p.ok, p.erro").count()) > 0),
    "o import responde com um relatório",
  );
  const relato = await page.locator("p.ok, p.erro").first().innerText();
  checar(relato.includes("3 linhas lidas"), `o relatório diz quantas linhas leu: "${relato}"`);
  checar(/linha \d+/.test(relato), "o relatório aponta a linha que ficou de fora");

  await page.reload();
  await hidratada();
  checar(
    (await page.locator("tbody tr").count()) === prospectsAntes + 1,
    "só a linha válida entrou na base",
  );
  checar(
    (await page.locator("tbody tr", { hasText: "José Antônio" }).innerText()).includes(
      "Mineradora Açaí",
    ),
    "o acento em Latin-1 sobreviveu ao import",
  );

  await page.goto(`${WEB}/agents`);
  await hidratada();
  checar(
    (await page.locator('select[name="agent"] option').count()) === 4,
    "o catálogo traz os quatro agentes",
  );
  checar(
    (await page.locator('select[name="prospect"] option').allInnerTexts()).some((t) =>
      t.includes("José Antônio"),
    ),
    "o prospect importado pode ser escolhido pelo nome, não por UUID",
  );
  await page.selectOption('select[name="agent"]', { value: "research" });
  await page.selectOption('select[name="prospect"]', { index: 1 });
  await page.locator('button:has-text("Disparar")').click();
  checar(
    await ate(async () =>
      (await page.locator("p.ok, p.erro").first().innerText()).includes("fila"),
    ),
    "a pesquisa vai para a fila em vez de pendurar a tela",
  );
  await page.reload();
  await hidratada();
  checar(
    (await page.locator("table").first().innerText()).includes("Pesquisa"),
    "o trabalho enfileirado aparece em Na fila",
  );
  // O alvo muda com o agente: conversa para o agente de conversa, pessoa para
  // os outros. Trocar a lista é o que poupa a pergunta "entity_type é qual?".
  await page.selectOption('select[name="agent"]', { value: "conversation" });
  checar(
    (await page.locator('select[name="conversation"]').count()) === 1 &&
      (await page.locator('select[name="prospect"]').count()) === 0,
    "escolher Conversa troca a lista de alvos",
  );

  // Onde o humano assume: a caixa de entrada, a thread e o detalhe do prospect.
  await page.goto(`${WEB}/conversations`);
  await page.waitForSelector("h1");
  checar(
    (await page.locator("tbody tr").innerText()).includes("Northern Ore"),
    "a caixa de entrada mostra o lead pelo nome",
  );
  await page.locator("tbody tr a").first().click();
  await page.waitForURL(/\/conversations\/[0-9a-f-]+$/);
  await hidratada();
  checar(
    (await page.locator('[data-secao="mensagem"]').count()) >= 1,
    "a thread mostra as mensagens da conversa",
  );
  const manual = `Resposta manual ${Date.now()}`;
  const escrever = page.locator("section.card", { hasText: "Responder à mão" });
  await escrever.locator('textarea[name="body"]').fill(manual);
  await escrever.locator('button[type="submit"]').click();
  checar(
    await ate(async () => (await page.locator("p.ok").count()) > 0),
    "a resposta escrita à mão salva",
  );
  await page.reload();
  await hidratada();
  // Nasce rascunho de propósito: o caminho de envio (limite diário, aquecimento,
  // horário, descadastro) está todo depois da aprovação, e um texto que pulasse
  // essa fila sairia sem freio nenhum — inclusive o escrito por uma pessoa.
  checar(
    (await page.locator('[data-secao="mensagem"]').last().innerText()).includes("rascunho"),
    "a resposta escrita à mão nasce rascunho",
  );
  const responsavel = page.locator("section.card", { hasText: "Quem cuida daqui" });
  await responsavel.locator('select[name="handoff_to_user_id"]').selectOption({ index: 1 });
  await responsavel.locator('button:has-text("Definir responsável")').click();
  checar(
    await ate(async () => (await page.locator("span.ok, p.ok").count()) > 0),
    "passar a conversa para uma pessoa funciona",
  );

  await page.goto(`${WEB}/drafts`);
  await hidratada();
  checar(
    (await page.locator('[data-secao="revisao"]').allInnerTexts()).some((t) =>
      t.includes(manual),
    ),
    "o rascunho escrito à mão entra na fila de Revisão",
  );

  await page.goto(`${WEB}/prospects`);
  await hidratada();
  await page.locator("tbody tr a").first().click();
  await page.waitForURL(/\/prospects\/[0-9a-f-]+$/);
  await hidratada();
  const detalhe = await page.locator("main").innerText();
  checar(
    detalhe.includes("Aderência ao ICP") && detalhe.includes("Qualificação"),
    "o detalhe do prospect mostra nota e veredito",
  );
  // O disparo a partir do detalhe: quem está decidindo sobre este lead não
  // deveria ter que procurá-lo numa lista em outra tela.
  const mandar = page.locator("section.card", { hasText: "Mandar um agente trabalhar" });
  await mandar.locator('button[value="outreach"]').click();
  checar(
    await ate(async () => (await mandar.locator("p.ok, p.erro").count()) > 0),
    "disparar um agente no detalhe do prospect devolve uma frase",
  );

  const marcar = page.locator("section.card", { hasText: "Marcar reunião" });
  await marcar.locator('input[name="scheduled_at"]').fill("2026-12-01T15:00");
  await marcar.locator('input[name="location"]').fill("Google Meet");
  await marcar.locator('button[type="submit"]').click();
  checar(
    await ate(async () => (await marcar.locator("p.ok").count()) > 0),
    "marcar reunião responde",
  );
  await page.reload();
  await hidratada();
  checar(
    (await page.locator("main").innerText()).includes("reunião marcada"),
    "a reunião move o prospect no funil",
  );
} catch (erro) {
  falhas.push(`exceção: ${erro.message}`);
  console.error(erro);
} finally {
  await browser.close();
}

if (falhas.length) {
  console.error(`\n${falhas.length} verificação(ões) falharam`);
  process.exit(1);
}
console.log("\ntudo certo");
