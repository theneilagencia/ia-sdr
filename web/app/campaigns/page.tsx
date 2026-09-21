import { api, type Campaign, type Me } from "@/lib/api";

import Nav from "../nav";
import { EditarCampanha, MudarStatus, NovaCampanha } from "./forms";

const ESTADO: Record<string, string> = {
  draft: "rascunho",
  active: "ativa",
  paused: "pausada",
  archived: "arquivada",
};

/** O que falta para a campanha produzir, em ordem de consequência. */
function pendencias(c: Campaign): string[] {
  const faltando: string[] = [];
  if (Object.keys(c.qualification_criteria).length === 0)
    faltando.push("sem critérios: o agente de qualificação não roda");
  if (Object.keys(c.icp).length === 0)
    faltando.push("sem ICP: a pesquisa não tem contra o que medir aderência");
  if (Object.keys(c.offer).length === 0)
    faltando.push("sem oferta: a abordagem fala da empresa, não desta campanha");
  if (c.status === "draft") faltando.push("em rascunho: a cadência não anda");
  return faltando;
}

/**
 * Campanhas.
 *
 * A campanha é o contexto operacional de tudo que a IA faz: dois clientes da
 * mesma empresa com ICPs diferentes são duas campanhas, e é o que impede o
 * agente de misturar uma com a outra. Antes desta tela ela só existia por API —
 * o que quer dizer que ninguém conseguia importar uma lista, porque importar
 * exige uma campanha.
 */
export default async function CampaignsPage() {
  const [campanhas, eu] = await Promise.all([
    api<Campaign[]>("/api/v1/campaigns"),
    api<Me>("/api/v1/auth/me"),
  ]);
  const podeOperar = eu.role !== "viewer";
  const vivas = campanhas.filter((c) => c.status !== "archived");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Campanhas</h1>
        <p className="lede">
          Cada campanha carrega seu próprio ICP, oferta e critérios — é o que impede o
          agente de confundir uma com a outra.
        </p>

        {vivas.length === 0 ? (
          <p className="empty">
            Nenhuma campanha ainda. Prospect, mensagem e qualificação sempre pertencem a
            uma — é por aqui que tudo começa.
          </p>
        ) : (
          vivas.map((c) => {
            const falta = pendencias(c);
            return (
              <article className="card" data-secao="campanha" key={c.id}>
                <h3>
                  {c.name} <span className="tag">{ESTADO[c.status] ?? c.status}</span>
                </h3>
                <div className="meta">
                  {c.objective ?? "sem objetivo escrito"}
                  {c.target_geography.length ? ` · ${c.target_geography.join(", ")}` : null}
                </div>
                {falta.length > 0 ? (
                  <ul className="pendencias">
                    {falta.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="ok">Pronta: os quatro agentes têm o que ler.</p>
                )}
                {podeOperar ? (
                  <>
                    <EditarCampanha campanha={c} />
                    <MudarStatus campanha={c} />
                  </>
                ) : null}
              </article>
            );
          })
        )}

        {podeOperar ? <NovaCampanha /> : null}
      </main>
    </>
  );
}
