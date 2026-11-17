import Link from "next/link";

import { api, type Conversation } from "@/lib/api";

import Nav from "../nav";

const ESTADO: Record<string, string> = {
  open: "aberta",
  replied: "respondida",
  closed: "fechada",
  handed_off: "com uma pessoa",
};

const FILTROS = [
  { chave: "", rotulo: "Todas" },
  { chave: "esperando", rotulo: "Esperando resposta" },
  { chave: "handed_off", rotulo: "Passadas para alguém" },
  { chave: "closed", rotulo: "Fechadas" },
] as const;

/**
 * A caixa de entrada.
 *
 * O agente de conversa escala para humano quando a resposta não está na base de
 * conhecimento — e até aqui o humano não tinha onde olhar. As respostas dos
 * leads chegavam por IMAP, eram ligadas à conversa certa e ficavam no banco.
 *
 * A ordem não é cronológica por acaso: quem escreveu e não foi respondido vem
 * primeiro, porque é o único item da lista com prazo.
 */
export default async function ConversationsPage({
  searchParams,
}: {
  searchParams: Promise<{ filtro?: string }>;
}) {
  const { filtro = "" } = await searchParams;
  const consulta =
    filtro === "esperando"
      ? "?awaiting_reply=true"
      : filtro === "handed_off" || filtro === "closed"
        ? `?status=${filtro}`
        : "";
  const conversas = await api<Conversation[]>(`/api/v1/conversations${consulta}`);

  const esperando = conversas.filter((c) => c.awaiting_reply);
  const resto = conversas.filter((c) => !c.awaiting_reply);
  const emOrdem = [...esperando, ...resto];

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Conversas</h1>
        <p className="lede">
          {conversas.length === 0
            ? "Nada aqui. As conversas aparecem quando um lead responde."
            : esperando.length > 0
              ? `${esperando.length} ${esperando.length === 1 ? "espera" : "esperam"} resposta — ${esperando.length === 1 ? "é a única com prazo" : "são as únicas com prazo"}. ${conversas.length} no total.`
              : `${conversas.length} ${conversas.length === 1 ? "conversa" : "conversas"}. Ninguém esperando resposta.`}
        </p>

        <nav className="filtros">
          {FILTROS.map((f) => (
            <Link
              key={f.chave}
              href={f.chave ? `/conversations?filtro=${f.chave}` : "/conversations"}
              className={f.chave === filtro ? "atual" : undefined}
            >
              {f.rotulo}
            </Link>
          ))}
        </nav>

        {emOrdem.length === 0 ? (
          <p className="empty">Nenhuma conversa com este filtro.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>lead</th>
                <th>assunto</th>
                <th>mensagens</th>
                <th>estado</th>
                <th>última</th>
              </tr>
            </thead>
            <tbody>
              {emOrdem.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/conversations/${c.id}`}>
                      {c.contact_name ?? c.contact_email ?? "sem nome"}
                    </Link>
                    <span className="meta">{c.company_name ?? "—"}</span>
                  </td>
                  <td>{c.subject ?? "(sem assunto)"}</td>
                  <td>{c.message_count}</td>
                  <td>
                    {c.awaiting_reply ? (
                      <span className="tag urgente">esperando resposta</span>
                    ) : (
                      <span className="tag">{ESTADO[c.status] ?? c.status}</span>
                    )}
                  </td>
                  <td>
                    {c.last_message_at
                      ? new Date(c.last_message_at).toLocaleString("pt-BR")
                      : "—"}
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
