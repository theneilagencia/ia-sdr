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
const EMAIL = process.env.E2E_EMAIL ?? "owner@apymine.com";
const SENHA = process.env.E2E_PASSWORD ?? "demo-senha-12345";

const falhas = [];
function checar(condicao, descricao) {
  console.log(`${condicao ? "✓" : "✗"} ${descricao}`);
  if (!condicao) falhas.push(descricao);
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
  const antes = await page.locator("article.card").count();
  checar(antes > 0, `fila de revisão tem rascunho (${antes})`);

  await page.locator('button:has-text("Aprovar")').first().click();
  await page.waitForTimeout(2000);
  const depois = await page.locator("article.card").count();
  checar(depois === antes - 1, "aprovar tira o rascunho da fila");

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
