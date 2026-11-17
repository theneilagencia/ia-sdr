import { apiUrl } from "@/lib/api-url";

/**
 * `/api/*` chega à API por aqui.
 *
 * **Por que não uma reescrita do `next.config.ts`:** porque `next build`
 * **congela** o destino da reescrita no `routes-manifest.json`. Com a API_URL
 * lida na configuração, o endereço gravado é o que existia na hora do build — e
 * numa imagem Docker o build não tem como saber o endereço interno que a
 * plataforma vai atribuir ao serviço da API. O resultado era uma reescrita
 * apontando para `localhost:8000` dentro do container do web, onde não há nada
 * ouvindo. Isto não apareceu em teste nenhum porque, em desenvolvimento e no CI,
 * build e execução usam o mesmo endereço; apareceu ao construir com um endereço
 * e rodar com outro, que é exatamente o que uma plataforma gerenciada faz.
 *
 * Um route handler roda a cada requisição, então `apiUrl()` é lido na hora.
 *
 * **Para que serve, já que as telas não passam por aqui:** as telas são
 * renderizadas no servidor e chamam a API direto (`lib/api.ts`). Por este
 * caminho passa o que o **browser de fora** pede: o link de descadastro que vai
 * em todo email — obrigação legal, e o endereço dele tem que ser o mesmo da
 * aplicação. Em servidor próprio quem faz esse roteamento é o Caddy, e este
 * handler nunca é alcançado; ele existe para o caso sem proxy nosso na frente,
 * em que a API fica num serviço privado, sem porta para a internet.
 */
async function encaminhar(requisicao: Request): Promise<Response> {
  const entrada = new URL(requisicao.url);
  const destino = `${apiUrl()}${entrada.pathname}${entrada.search}`;

  const cabecalhos = new Headers(requisicao.headers);
  // `host` é o do web; mandá-lo adiante faria a API responder achando-se no
  // domínio errado. `connection` e `content-length` pertencem a esta conexão,
  // não à próxima: o corpo é reenviado, e um tamanho herdado pode não bater.
  cabecalhos.delete("host");
  cabecalhos.delete("connection");
  cabecalhos.delete("content-length");

  const temCorpo = requisicao.method !== "GET" && requisicao.method !== "HEAD";
  const resposta = await fetch(destino, {
    method: requisicao.method,
    headers: cabecalhos,
    body: temCorpo ? await requisicao.arrayBuffer() : undefined,
    // `manual`: um 3xx da API é para o browser seguir, não para o servidor do
    // Next seguir por ele — seguir aqui trocaria o endereço final por um
    // interno, que o browser não alcança.
    redirect: "manual",
  });

  const saida = new Headers(resposta.headers);
  // O `fetch` já decodificou o corpo; repassar o cabeçalho de codificação faria
  // o browser tentar descomprimir texto puro. Os outros dois são recalculados
  // pelo servidor que está entregando.
  saida.delete("content-encoding");
  saida.delete("content-length");
  saida.delete("transfer-encoding");

  return new Response(resposta.body, {
    status: resposta.status,
    statusText: resposta.statusText,
    headers: saida,
  });
}

export const GET = encaminhar;
export const HEAD = encaminhar;
export const POST = encaminhar;
export const PUT = encaminhar;
export const PATCH = encaminhar;
export const DELETE = encaminhar;
export const OPTIONS = encaminhar;

//: Nada aqui pode ser pré-renderizado nem guardado em cache: é um proxy.
export const dynamic = "force-dynamic";
