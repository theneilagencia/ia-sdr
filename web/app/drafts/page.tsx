import { api, type Allowance, type Message } from "@/lib/api";

import { approveDraft, rejectDraft } from "../actions";
import Nav from "../nav";
import { FetchInbox, Requeue, SendAll, SendOne } from "./send-button";

type Anchor = { fact?: string; how_used?: string };

/**
 * A fila de revisão.
 *
 * É a tela que torna o envio seguro: nada sai daqui sem uma pessoa ler. Os
 * ganchos de personalização aparecem junto do texto, para quem revisa
 * conferir de onde veio cada afirmação sem reabrir a pesquisa.
 */
export default async function DraftsPage() {
  const [rascunhos, aprovados, falhados, cota] = await Promise.all([
    api<Message[]>("/api/v1/messages?status=draft"),
    api<Message[]>("/api/v1/messages?status=queued"),
    api<Message[]>("/api/v1/messages?status=failed"),
    api<Allowance>("/api/v1/messages/allowance"),
  ]);

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Revisão e envio</h1>
        <p className="lede">
          {rascunhos.length === 0 && aprovados.length === 0
            ? "Nada esperando."
            : [
                rascunhos.length
                  ? `${rascunhos.length} ${rascunhos.length === 1 ? "rascunho espera" : "rascunhos esperam"} sua leitura`
                  : null,
                aprovados.length
                  ? `${aprovados.length} ${aprovados.length === 1 ? "aprovado espera" : "aprovados esperam"} envio`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
          {rascunhos.length ? ". Nada é enviado sem aprovação." : null}
        </p>

        <div className="cota">
          <strong>
            {cota.used} de {cota.limit} enviados hoje
          </strong>
          <span> · {cota.reason}</span>
          {cota.within_business_hours ? null : (
            <span className="aviso"> · fora do horário de envio configurado</span>
          )}
          <div className="acao-caixa">
            <FetchInbox />
          </div>
        </div>

        <h2>Aguardando revisão</h2>
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
              <article className="card" data-secao="revisao" key={mensagem.id}>
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

        <h2>Aprovados, prontos para enviar</h2>
        {aprovados.length === 0 ? (
          <p className="empty">Nada aprovado esperando envio.</p>
        ) : (
          <>
            <SendAll
              quantos={Math.min(aprovados.length, cota.remaining)}
              disabled={cota.remaining === 0}
            />
            {aprovados.map((mensagem) => (
              <article className="card" data-secao="aprovados" key={mensagem.id}>
                <h3>{mensagem.subject ?? "(sem assunto)"}</h3>
                <div className="meta">
                  para {String(mensagem.metrics?.to ?? "—")} · aprovado
                </div>
                <pre>{mensagem.body}</pre>
                <SendOne id={mensagem.id} disabled={cota.remaining === 0} />
              </article>
            ))}
          </>
        )}

        {falhados.length > 0 ? (
          <>
            <h2>Não saíram</h2>
            <p className="lede">
              Estas falharam no envio. O texto continua aprovado — corrija o que causou a
              falha e devolva para a fila.
            </p>
            {falhados.map((mensagem) => (
              <article className="card" data-secao="falhados" key={mensagem.id}>
                <h3>{mensagem.subject ?? "(sem assunto)"}</h3>
                <div className="meta">
                  para {String(mensagem.metrics?.to ?? "—")}
                </div>
                <p className="erro">
                  {String(
                    mensagem.metrics?.send_error_message ??
                      mensagem.metrics?.send_error ??
                      "falha no envio",
                  )}
                </p>
                <Requeue id={mensagem.id} />
              </article>
            ))}
          </>
        ) : null}
      </main>
    </>
  );
}
