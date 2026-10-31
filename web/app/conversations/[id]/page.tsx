import Link from "next/link";

import { notFound } from "next/navigation";

import {
  api,
  apiOuRecusa,
  foiRecusado,
  type ConversationDetail,
  type Me,
  type Member,
} from "@/lib/api";

import Nav from "../../nav";
import { Fechar, PassarPara, PedirAoAgente, Responder } from "./forms";

const ESTADO_MENSAGEM: Record<string, string> = {
  draft: "rascunho",
  queued: "aprovada",
  sent: "enviada",
  delivered: "entregue",
  replied: "resposta do lead",
  failed: "falhou",
  rejected: "recusada",
  bounced: "voltou",
};

/**
 * Uma conversa, do primeiro email até agora.
 *
 * É a tela onde o humano assume. Tudo o que ele pode fazer daqui — escrever,
 * pedir ao agente, passar para alguém, fechar — passa pelos mesmos freios do
 * resto da plataforma: a resposta escrita à mão nasce rascunho igual à do
 * agente, porque o caminho de envio (limite diário, aquecimento, horário,
 * descadastro) está todo depois da aprovação.
 */
export default async function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  // O id vem da URL, e URL é coisa que se cola errada, se guarda depois de o
  // registro sair e se herda de um link antigo. A API responde 404 (ou 422, se
  // nem uuid é), e sem isto a resposta previsível virava a tela de erro do Next.
  const [conversa, membros, eu] = await Promise.all([
    apiOuRecusa<ConversationDetail>(`/api/v1/conversations/${id}`, { aceitar: [404, 422] }),
    api<Member[]>("/api/v1/tenants/me/members"),
    api<Me>("/api/v1/auth/me"),
  ]);
  if (foiRecusado(conversa)) notFound();
  const podeOperar = eu.role !== "viewer";
  const responsavel = membros.find((m) => m.user_id === conversa.handoff_to_user_id);

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>{conversa.contact_name ?? conversa.contact_email ?? "Conversa"}</h1>
        <p className="lede">
          {[
            conversa.company_name,
            conversa.contact_email,
            conversa.subject ?? "(sem assunto)",
          ]
            .filter(Boolean)
            .join(" · ")}
          {responsavel ? ` · com ${responsavel.full_name}` : null}
        </p>
        <p className="meta">
          <Link href={`/prospects/${conversa.prospect_id}`}>Ver o prospect inteiro</Link>
          {" · "}
          <Link href="/conversations">Voltar para a caixa</Link>
        </p>

        {conversa.awaiting_reply ? (
          <p className="aviso">
            A última mensagem é do lead. Enquanto ninguém responder, isto conta como
            silêncio da nossa parte.
          </p>
        ) : null}

        {conversa.messages.length === 0 ? (
          <p className="empty">Nenhuma mensagem nesta conversa ainda.</p>
        ) : (
          conversa.messages.map((m) => (
            <article
              className={m.direction === "inbound" ? "card mensagem lead" : "card mensagem"}
              data-secao="mensagem"
              key={m.id}
            >
              <div className="meta">
                <strong>{m.direction === "inbound" ? "o lead" : "nós"}</strong>
                {" · "}
                {ESTADO_MENSAGEM[m.status] ?? m.status}
                {" · "}
                {new Date(m.created_at).toLocaleString("pt-BR")}
                {m.metrics?.escalate === true ? (
                  <span className="tag">
                    {" "}
                    precisa de humano
                    {m.metrics?.escalation_reason
                      ? `: ${String(m.metrics.escalation_reason)}`
                      : ""}
                  </span>
                ) : null}
              </div>
              {m.subject && m.subject !== conversa.subject ? <h3>{m.subject}</h3> : null}
              <pre>{m.body}</pre>
            </article>
          ))
        )}

        {podeOperar ? (
          <>
            <Responder conversa={conversa} />
            <PedirAoAgente conversa={conversa} />
            <section className="card">
              <h3>Quem cuida daqui</h3>
              <p className="ajuda">
                Passar para uma pessoa é o outro lado do escalonamento: o agente marca
                que precisa de humano, e aqui se diz qual. Sem ninguém escolhido, a
                conversa volta para o agente.
              </p>
              <PassarPara conversa={conversa} membros={membros.filter((m) => m.is_active)} />
              <Fechar conversa={conversa} />
            </section>
          </>
        ) : null}
      </main>
    </>
  );
}
