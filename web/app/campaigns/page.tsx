import { api, type Campaign } from "@/lib/api";

import Nav from "../nav";

export default async function CampaignsPage() {
  const campanhas = await api<Campaign[]>("/api/v1/campaigns");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Campanhas</h1>
        <p className="lede">
          Cada campanha carrega seu próprio ICP, oferta e critérios — é o que impede o agente de
          confundir uma com a outra.
        </p>

        {campanhas.length === 0 ? (
          <p className="empty">Nenhuma campanha ainda.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>campanha</th>
                <th>status</th>
                <th>geografia</th>
              </tr>
            </thead>
            <tbody>
              {campanhas.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>
                    <span className="tag">{c.status}</span>
                  </td>
                  <td>{c.target_geography.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  );
}
