import Link from "next/link";

import {
  api,
  type Contact,
  type Conversation,
  type Criterion,
  type Meeting,
  type Me,
  type Message,
  type Prospect,
  type Qualification,
  type Score,
} from "@/lib/api";

import Nav from "../../nav";
import {
  Descadastrar,
  MandarAgente,
  MarcarReuniao,
  RegistrarResposta,
  SubirParaRavi,
} from "./forms";

const ESTAGIO: Record<string, string> = {
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

const VEREDITO: Record<string, string> = {
  qualified: "qualificado",
  disqualified: "desqualificado",
  needs_more_info: "falta informação",
};

const CRITERIO: Record<string, string> = { met: "atendido", not_met: "não atendido" };

/** O que o agente escreveu, na forma em que ele escreve. */
function criterios(q: Qualification): Criterion[] {
  const lista = q.criteria_results?.criteria;
  return Array.isArray(lista) ? lista : [];
}

/**
 * Um prospect por inteiro: nota, conversas, veredito e reuniões.
 *
 * É a tela que uma pessoa abre quando vai decidir algo sobre este lead — e a que
 * mostra de onde veio a decisão do agente. A nota aparece com o porquê e o
 * veredito critério a critério com a evidência, porque "qualificado, confiança
 * 80" sem evidência é um palpite com aparência de número.
 */
export default async function ProspectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const prospect = await api<Prospect>(`/api/v1/prospects/${id}`);
  const [notas, mensagens, qualificacoes, reunioes, conversas, contato, eu] = await Promise.all([
    api<Score[]>(`/api/v1/prospects/${id}/scores`),
    api<Message[]>(`/api/v1/prospects/${id}/messages`),
    api<Qualification[]>(`/api/v1/prospects/${id}/qualifications`),
    api<Meeting[]>(`/api/v1/prospects/${id}/meetings`),
    api<Conversation[]>(`/api/v1/conversations?prospect_id=${id}`),
    api<Contact>(`/api/v1/contacts/${prospect.contact_id}`),
    api<Me>("/api/v1/auth/me"),
  ]);
  const podeOperar = eu.role !== "viewer";
  const nota = notas[0];

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>{prospect.contact_name ?? prospect.contact_email ?? "Prospect"}</h1>
        <p className="lede">
          {[
            contato.title,
            prospect.company_name,
            prospect.contact_email ?? "sem email",
            prospect.campaign_name,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <p className="meta">
          <span className="tag">{ESTAGIO[prospect.status] ?? prospect.status}</span>
          {contato.opted_out ? <span className="tag urgente"> descadastrado</span> : null}
          {prospect.source ? ` · origem: ${prospect.source}` : null}
          {" · "}
          <Link href="/prospects">Voltar para a lista</Link>
        </p>

        {contato.opted_out ? (
          <p className="aviso">
            Esta pessoa pediu para não receber mais contato. Nenhum agente escreve para
            ela, e a plataforma recusa qualquer tentativa.
          </p>
        ) : null}

        <h2>Aderência ao ICP</h2>
        {nota === undefined ? (
          <p className="empty">
            Sem nota ainda. Ela sai da pesquisa: dispare o agente de pesquisa em Agentes.
          </p>
        ) : (
          <article className="card">
            <h3>
              {nota.value.toFixed(0)} de 100{" "}
              {nota.band ? <span className="tag">banda {nota.band}</span> : null}
            </h3>
            <div className="meta">{new Date(nota.created_at).toLocaleString("pt-BR")}</div>
            {nota.rationale ? <pre>{nota.rationale}</pre> : null}
            {Object.keys(nota.signals ?? {}).length > 0 ? (
              <div className="anchors">
                {Object.entries(nota.signals).map(([chave, valor]) => (
                  <div key={chave}>
                    <strong>{chave}</strong> — {String(valor)}
                  </div>
                ))}
              </div>
            ) : null}
          </article>
        )}

        <h2>Qualificação</h2>
        {qualificacoes.length === 0 ? (
          <p className="empty">
            Sem veredito. Ele sai do agente de qualificação, que precisa dos critérios da
            campanha para ter contra o que julgar.
          </p>
        ) : (
          qualificacoes.map((q) => (
            <article className="card" data-secao="qualificacao" key={q.id}>
              <h3>
                {VEREDITO[q.outcome] ?? q.outcome}{" "}
                <span className="tag">confiança {q.confidence}</span>
              </h3>
              <div className="meta">{new Date(q.created_at).toLocaleString("pt-BR")}</div>
              {q.rationale ? <pre>{q.rationale}</pre> : null}
              {criterios(q).length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>critério</th>
                      <th>situação</th>
                      <th>evidência</th>
                    </tr>
                  </thead>
                  <tbody>
                    {criterios(q).map((c) => (
                      <tr key={c.criterion}>
                        <td>{c.criterion}</td>
                        <td>
                          <span className="tag">{CRITERIO[c.status] ?? c.status}</span>
                        </td>
                        {/* Sem evidência o critério é palpite: o agente é obrigado a
                            rebaixar o que não consegue sustentar, e a tela mostra
                            exatamente o que ele sustentou. */}
                        <td>{c.evidence ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </article>
          ))
        )}

        <h2>Conversas</h2>
        {conversas.length === 0 ? (
          <p className="empty">Nenhuma conversa aberta com esta pessoa.</p>
        ) : (
          <ul className="lista">
            {conversas.map((c) => (
              <li key={c.id}>
                <Link href={`/conversations/${c.id}`}>
                  {c.subject ?? "(sem assunto)"}
                </Link>{" "}
                — {c.message_count} {c.message_count === 1 ? "mensagem" : "mensagens"}
                {c.awaiting_reply ? <span className="tag urgente"> esperando resposta</span> : null}
              </li>
            ))}
          </ul>
        )}

        <h2>Reuniões</h2>
        {reunioes.length === 0 ? (
          <p className="empty">Nenhuma reunião marcada.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>quando</th>
                <th>duração</th>
                <th>onde</th>
                <th>estado</th>
              </tr>
            </thead>
            <tbody>
              {reunioes.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.scheduled_at).toLocaleString("pt-BR")}</td>
                  <td>{r.duration_minutes} min</td>
                  <td>{r.location ?? "—"}</td>
                  <td>
                    <span className="tag">{r.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {podeOperar ? (
          <>
            <MandarAgente
              prospectId={prospect.id}
              companyId={prospect.company_id}
              contactId={prospect.contact_id}
              campaignId={prospect.campaign_id}
            />
            <MarcarReuniao prospectId={prospect.id} />
            <RegistrarResposta prospectId={prospect.id} />
            <SubirParaRavi prospectId={prospect.id} />
            <Descadastrar
              prospectId={prospect.id}
              contactId={contato.id}
              nome={contato.full_name}
              jaDescadastrado={contato.opted_out}
            />
          </>
        ) : null}

        <h2>Mensagens</h2>
        {mensagens.length === 0 ? (
          <p className="empty">Nada escrito ainda.</p>
        ) : (
          mensagens.map((m) => (
            <article className="card mensagem" key={m.id}>
              <div className="meta">
                <strong>{m.direction === "inbound" ? "o lead" : "nós"}</strong> ·{" "}
                {m.status} · {new Date(m.created_at).toLocaleString("pt-BR")}
              </div>
              <pre>{m.body}</pre>
            </article>
          ))
        )}
      </main>
    </>
  );
}
