import { api, type Prospect } from "@/lib/api";

import Nav from "../nav";

const ROTULOS: Record<string, string> = {
  new: "novo",
  researching: "pesquisando",
  researched: "pesquisado",
  scored: "pontuado",
  contacted: "contatado",
  engaged: "engajado",
  qualified: "qualificado",
  meeting_booked: "reunião marcada",
  disqualified: "desqualificado",
  bounced: "email inválido",
};

export default async function ProspectsPage() {
  const prospects = await api<Prospect[]>("/api/v1/prospects?limit=200");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Prospects</h1>
        <p className="lede">{prospects.length} na base.</p>

        {prospects.length === 0 ? (
          <p className="empty">Importe uma lista para começar.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>estágio</th>
                <th>origem</th>
                <th>entrou em</th>
              </tr>
            </thead>
            <tbody>
              {prospects.map((p) => (
                <tr key={p.id}>
                  <td>
                    <span className="tag">{ROTULOS[p.status] ?? p.status}</span>
                  </td>
                  <td>{p.source ?? "—"}</td>
                  <td>{new Date(p.created_at).toLocaleDateString("pt-BR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  );
}
