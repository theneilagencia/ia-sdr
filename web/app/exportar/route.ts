import { api } from "@/lib/api";

/**
 * Baixar os dados da empresa pelo navegador.
 *
 * A API já devolve o pacote inteiro, com `Content-Disposition` — mas o token
 * vive num cookie httpOnly e a API roda em outra origem, então o browser não
 * consegue pedir direto. Este manipulador de rota é a ponte: a chamada sai do
 * servidor do Next, com a sessão, e a resposta desce como arquivo.
 *
 * Sem isto, "dá para ir embora" era verdade só para quem sabe usar `curl` — o
 * que, para um cliente avaliando se entra, é o mesmo que não ser verdade.
 */
export async function GET() {
  const pacote = await api<{ tenant?: { slug?: string | null } }>(
    "/api/v1/tenants/me/export",
  );
  const slug = pacote?.tenant?.slug ?? "empresa";
  const hoje = new Date().toISOString().slice(0, 10);

  return new Response(JSON.stringify(pacote, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": `attachment; filename="${slug}-${hoje}.json"`,
      // Dado de cliente não fica em cache de proxy nenhum.
      "cache-control": "no-store",
    },
  });
}
