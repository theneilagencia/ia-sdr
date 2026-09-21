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
