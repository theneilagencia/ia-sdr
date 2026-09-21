import { api, type Message } from "@/lib/api";

import { approveDraft, rejectDraft } from "../actions";
import Nav from "../nav";

type Anchor = { fact?: string; how_used?: string };

/**
 * A fila de revisão.
 *
 * É a tela que torna o envio seguro: nada sai daqui sem uma pessoa ler. Os
 * ganchos de personalização aparecem junto do texto, para quem revisa
 * conferir de onde veio cada afirmação sem reabrir a pesquisa.
 */
export default async function DraftsPage() {
  const rascunhos = await api<Message[]>("/api/v1/messages?status=draft");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Revisão</h1>
        <p className="lede">
          {rascunhos.length === 0
            ? "Nada esperando."
            : `${rascunhos.length} ${rascunhos.length === 1 ? "rascunho espera" : "rascunhos esperam"} sua leitura. Nada é enviado sem aprovação.`}
        </p>

        {rascunhos.length === 0 ? (
          <p className="empty">
            Quando os agentes escreverem, os rascunhos aparecem aqui antes de ir para alguém.
          </p>
        ) : (
          rascunhos.map((mensagem) => {
            const ganchos = (mensagem.metrics?.anchors as Anchor[] | undefined) ?? [];
            const escalar = mensagem.metrics?.escalate === true;
            const motivo = mensagem.metrics?.escalation_reason as string | undefined;
            return (
              <article className="card" key={mensagem.id}>
                <h3>{mensagem.subject ?? "(sem assunto)"}</h3>
                <div className="meta">
                  para {String(mensagem.metrics?.to ?? "—")} ·{" "}
                  {new Date(mensagem.created_at).toLocaleString("pt-BR")}
                  {escalar ? (
                    <>
                      {" · "}
                      <span className="tag">precisa de humano{motivo ? `: ${motivo}` : ""}</span>
                    </>
                  ) : null}
                </div>
                <pre>{mensagem.body}</pre>

                {ganchos.length > 0 ? (
                  <div className="anchors">
                    {ganchos.map((gancho, i) => (
                      <div key={i}>
                        <strong>{gancho.fact}</strong>
                        {gancho.how_used ? ` — ${gancho.how_used}` : null}
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className="inline">
                  <form action={approveDraft}>
                    <input type="hidden" name="id" value={mensagem.id} />
                    <button className="primary" type="submit">
                      Aprovar
                    </button>
                  </form>
                  <form action={rejectDraft} className="inline">
                    <input type="hidden" name="id" value={mensagem.id} />
                    <input name="reason" placeholder="motivo da recusa (ajuda a corrigir)" />
                    <button className="ghost" type="submit">
                      Recusar
                    </button>
                  </form>
                </div>
              </article>
            );
          })
        )}
      </main>
    </>
  );
}
