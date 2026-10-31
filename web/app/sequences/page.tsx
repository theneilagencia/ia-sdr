import Link from "next/link";

import {
  api,
  type Campaign,
  type Enrollment,
  type Me,
  type Prospect,
  type Sequence,
} from "@/lib/api";

import Nav from "../nav";
import { Apagar, Avancar, Editar, Inscrever, NovaCadencia, Parar, Alternar } from "./forms";

const ESTADO: Record<string, string> = {
  active: "ativa",
  completed: "concluída",
  stopped: "parada",
};

/**
 * Cadências: o follow-up que acontece sem ninguém lembrar.
 *
 * A maior parte das respostas em prospecção fria vem do segundo ou do terceiro
 * contato — mandar uma mensagem por lead e esperar é a forma mais cara de não
 * vender. O que a cadência automatiza é *lembrar* e *escrever*; enviar continua
 * passando pela fila de revisão, como todo o resto.
 *
 * A tela existe porque montar isso por API significava escrever JSON de passos
 * à mão e descobrir as regras de espera pelo erro que voltava.
 */
export default async function SequencesPage() {
  const [cadencias, campanhas, prospects, eu] = await Promise.all([
    api<Sequence[]>("/api/v1/sequences"),
    api<Campaign[]>("/api/v1/campaigns"),
    api<Prospect[]>("/api/v1/prospects?limit=200"),
    api<Me>("/api/v1/auth/me"),
  ]);
  const podeOperar = eu.role !== "viewer";
  const abertas = campanhas.filter((c) => c.status !== "archived");
  const porCampanha = new Map(campanhas.map((c) => [c.id, c.name]));

  // Os inscritos de cada cadência, em paralelo: a tela mostra em que passo cada
  // prospect está, que é a única forma de responder "por que este não recebeu".
  const inscritos = await Promise.all(
    cadencias.map((c) => api<Enrollment[]>(`/api/v1/sequences/${c.id}/enrollments`)),
  );
  const nomeDoProspect = new Map(
    prospects.map((p) => [
      p.id,
      `${p.contact_name ?? p.contact_email ?? "sem nome"} — ${p.company_name ?? "sem empresa"}`,
    ]),
  );

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Cadências</h1>
        <p className="lede">
          {cadencias.length === 0
            ? "Nenhuma cadência. A maior parte das respostas vem do segundo ou do terceiro contato — uma mensagem por lead é a forma mais cara de não vender."
            : `${cadencias.length} ${cadencias.length === 1 ? "cadência" : "cadências"}. O que ela automatiza é lembrar e escrever; enviar continua passando pela fila de Revisão.`}
        </p>

        {podeOperar ? <Avancar /> : null}

        {cadencias.map((cadencia, i) => {
          const ativos = inscritos[i].filter((e) => e.status === "active");
          const fora = inscritos[i].filter((e) => e.status !== "active");
          const jaDentro = new Set(inscritos[i].map((e) => e.prospect_id));
          const disponiveis = prospects.filter(
            (p) => p.campaign_id === cadencia.campaign_id && !jaDentro.has(p.id),
          );

          return (
            <article className="card" data-secao="cadencia" key={cadencia.id}>
              <h3>
                {cadencia.name}{" "}
                <span className={cadencia.is_active ? "tag" : "tag urgente"}>
                  {cadencia.is_active ? "ativa" : "desativada"}
                </span>
              </h3>
              <div className="meta">
                {porCampanha.get(cadencia.campaign_id) ?? "campanha removida"} ·{" "}
                {cadencia.steps.length} {cadencia.steps.length === 1 ? "toque" : "toques"} ·{" "}
                {ativos.length} {ativos.length === 1 ? "prospect dentro" : "prospects dentro"}
              </div>

              <ol className="passos">
                {cadencia.steps.map((passo, indice) => (
                  <li key={indice}>
                    <strong>
                      {indice === 0
                        ? "abordagem inicial"
                        : `+${passo.wait_days ?? 3} ${(passo.wait_days ?? 3) === 1 ? "dia" : "dias"}`}
                    </strong>{" "}
                    — {passo.instruction}
                  </li>
                ))}
              </ol>

              {podeOperar ? (
                <>
                  <Editar cadencia={cadencia} />
                  {/* Ativar vem antes de inscrever porque é essa a ordem real:
                      ninguém entra numa cadência que não anda. */}
                  <div className="inline">
                    <Alternar cadencia={cadencia} />
                    {ativos.length === 0 ? <Apagar cadencia={cadencia} /> : null}
                  </div>
                  <Inscrever
                    cadencia={cadencia}
                    candidatos={disponiveis.map((p) => ({
                      id: p.id,
                      rotulo: nomeDoProspect.get(p.id) ?? p.id,
                    }))}
                  />
                </>
              ) : null}

              {inscritos[i].length === 0 ? (
                <p className="empty">Ninguém inscrito.</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>prospect</th>
                      <th>passo</th>
                      <th>estado</th>
                      <th>próximo toque</th>
                      {podeOperar ? <th /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {[...ativos, ...fora].map((inscricao) => (
                      <tr key={inscricao.id}>
                        <td>
                          <Link href={`/prospects/${inscricao.prospect_id}`}>
                            {nomeDoProspect.get(inscricao.prospect_id) ?? "prospect"}
                          </Link>
                        </td>
                        <td>
                          {inscricao.current_step} de {cadencia.steps.length}
                        </td>
                        <td>
                          <span className="tag">
                            {ESTADO[inscricao.status] ?? inscricao.status}
                          </span>
                          {inscricao.stop_reason ? (
                            <span className="meta">{inscricao.stop_reason}</span>
                          ) : null}
                        </td>
                        <td>
                          {inscricao.next_run_at
                            ? new Date(inscricao.next_run_at).toLocaleString("pt-BR")
                            : "—"}
                        </td>
                        {podeOperar ? (
                          <td>
                            {inscricao.status === "active" ? (
                              <Parar inscricao={inscricao} />
                            ) : null}
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </article>
          );
        })}

        {podeOperar ? <NovaCadencia campanhas={abertas} /> : null}
      </main>
    </>
  );
}
