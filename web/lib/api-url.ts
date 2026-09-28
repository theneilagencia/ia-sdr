/**
 * Endereço **interno** da API, normalizado.
 *
 * O `http://` opcional não é gentileza: é o formato que plataforma gerenciada
 * entrega. O Render, por exemplo, só sabe injetar o endereço de um serviço
 * privado como `host:porta` — não existe jeito de o arquivo de infraestrutura
 * concatenar um esquema na frente. Sem esta normalização, o `fetch` recebe
 * `ia-sdr-api:10000/api/v1/...`, interpreta `ia-sdr-api:` como esquema e falha
 * com "unsupported protocol", que não parece em nada com a causa.
 *
 * A barra final também sai: `API_URL=http://api:8000/` mais `/api/v1/x` viraria
 * `//api/v1/x`, e um caminho com barra dupla não é o mesmo caminho.
 */
export function apiUrl(): string {
  const bruto = (process.env.API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
  return /^https?:\/\//i.test(bruto) ? bruto : `http://${bruto}`;
}
