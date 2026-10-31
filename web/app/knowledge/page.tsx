import { api, type KnowledgeDocument } from "@/lib/api";

import Nav from "../nav";
import { Apagar, Colar, Subir } from "./forms";

/**
 * A base de conhecimento.
 *
 * O agente de conversa responde **apenas** com o que está aqui e escala para
 * humano quando não encontra. Uma base vazia é um agente que escala tudo, todo
 * dia — e um agente assim é desligado.
 */
export default async function KnowledgePage() {
  const documentos = await api<KnowledgeDocument[]>("/api/v1/knowledge/documents");
  const fatias = documentos.reduce((total, d) => total + d.chunk_count, 0);

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Base de conhecimento</h1>
        <p className="lede">
          {documentos.length === 0
            ? "Vazia. O agente de conversa responde só com o que estiver aqui — enquanto estiver vazia, ele passa toda resposta para uma pessoa."
            : `${documentos.length} ${documentos.length === 1 ? "documento" : "documentos"} · ${fatias} ${fatias === 1 ? "trecho" : "trechos"} indexados. É daqui que o agente de conversa tira o que responde.`}
        </p>

        <Subir />
        <Colar />

        <h2>Indexado</h2>
        {documentos.length === 0 ? (
          <p className="empty">Nada ainda.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>documento</th>
                <th>origem</th>
                <th>trechos</th>
                <th>entrou em</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {documentos.map((d) => (
                <tr key={d.id}>
                  <td>
                    {d.title}
                    {d.status !== "indexed" ? (
                      <span className="tag"> {d.status}</span>
                    ) : null}
                    {d.error ? <span className="erro"> {d.error}</span> : null}
                  </td>
                  <td>{d.source_uri ?? d.source_type}</td>
                  <td>{d.chunk_count}</td>
                  <td>{new Date(d.created_at).toLocaleDateString("pt-BR")}</td>
                  <td>
                    <Apagar id={d.id} titulo={d.title} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  );
}
