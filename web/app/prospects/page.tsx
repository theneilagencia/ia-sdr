import { api, type Campaign, type Me, type Prospect } from "@/lib/api";

import Nav from "../nav";
import { Importar } from "./forms";

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
  const [prospects, campanhas, eu] = await Promise.all([
    api<Prospect[]>("/api/v1/prospects?limit=200"),
    api<Campaign[]>("/api/v1/campaigns"),
    api<Me>("/api/v1/auth/me"),
  ]);
  const podeOperar = eu.role !== "viewer";
  const abertas = campanhas.filter((c) => c.status !== "archived");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Prospects</h1>
        <p className="lede">
          {prospects.length === 0
            ? "Nenhum ainda. A lista é o combustível: sem ela os agentes não têm para quem trabalhar."
            : `${prospects.length} na base. Em Agentes você manda pesquisar, abordar ou qualificar qualquer um deles.`}
        </p>

        {podeOperar ? <Importar campanhas={abertas} /> : null}

        {prospects.length === 0 ? null : (
          <table>
            <thead>
              <tr>
                <th>pessoa</th>
                <th>empresa</th>
                <th>campanha</th>
                <th>estágio</th>
                <th>origem</th>
                <th>entrou em</th>
              </tr>
            </thead>
            <tbody>
              {prospects.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.contact_name ?? "—"}
                    {p.contact_email ? (
                      <span className="meta"> {p.contact_email}</span>
                    ) : null}
                  </td>
                  <td>{p.company_name ?? "—"}</td>
                  <td>{p.campaign_name ?? "—"}</td>
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
