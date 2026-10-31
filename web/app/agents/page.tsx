import {
  api,
  type AgentCatalogItem,
  type AgentConfig,
  type AgentRun,
  type Conversation,
  type Job,
  type Me,
  type Prospect,
} from "@/lib/api";

import Nav from "../nav";
import { ConfigurarAgente } from "./configurar";
import { Disparar, type Opcao } from "./disparar";

/**
 * O que cada agente faz, dito pelo efeito na operação.
 *
 * Não é a instrução do agente — essa vive no backend e é o que o modelo lê.
 * Aqui é a consequência de apertar o botão: o que aparece depois, onde, e
 * quanto tempo leva. É o que a pessoa precisa saber para escolher.
 */
const AGENTES: Record<string, { rotulo: string; faz: string }> = {
  research: {
    rotulo: "Pesquisa",
    faz:
      "Lê a web sobre a empresa do prospect e guarda fatos com a fonte de cada um. " +
      "É o material que impede a abordagem de sair genérica. Vai para a fila: " +
      "busca na web leva alguns minutos.",
  },
  outreach: {
    rotulo: "Abordagem",
    faz:
      "Escreve o primeiro email usando a pesquisa, a oferta e o tom de voz do Cérebro. " +
      "O texto vai para Revisão — nada sai sem uma pessoa ler.",
  },
  conversation: {
    rotulo: "Conversa",
    faz:
      "Responde a última mensagem do lead com o que está na base de conhecimento. " +
      "Quando a resposta não está lá, o rascunho vem marcado para um humano.",
  },
  qualification: {
    rotulo: "Qualificação",
    faz:
      "Avalia o prospect contra os critérios da campanha e devolve veredito com " +
      "evidência, critério a critério.",
  },
};

const ESTADO_RUN: Record<string, string> = {
  running: "rodando",
  succeeded: "concluído",
  failed: "falhou",
  rejected: "recusado",
};

const ESTADO_JOB: Record<string, string> = {
  pending: "na fila",
  running: "rodando",
  done: "feito",
  failed: "falhou",
};

/** Fila parada há mais do que isto é sinal de trabalhador fora do ar. */
const PARADO_MS = 5 * 60 * 1000;

function quando(iso: string) {
  return new Date(iso).toLocaleString("pt-BR");
}

/**
 * Disparo de agente.
 *
 * É a tela que faz o funil andar por decisão de uma pessoa, e não só quando o
 * trabalhador resolve. Sem ela, mover um prospect exigia `curl` com três UUIDs
 * — e descobrir, por tentativa, que a pesquisa quer a empresa e a abordagem
 * quer o prospect.
 */
export default async function AgentsPage() {
  const [catalogo, config, prospects, conversas, runs, jobs, eu] = await Promise.all([
    api<AgentCatalogItem[]>("/api/v1/agents/catalog"),
    api<AgentConfig[]>("/api/v1/agents/config"),
    api<Prospect[]>("/api/v1/prospects?limit=200"),
    api<Conversation[]>("/api/v1/conversations?limit=200"),
    api<AgentRun[]>("/api/v1/agents/runs?limit=20"),
    api<Job[]>("/api/v1/agents/jobs?limit=20"),
    api<Me>("/api/v1/auth/me"),
  ]);
  // Viewer lê o histórico e não dispara nada: a API recusaria de qualquer
  // forma, e um botão que sempre dá erro é pior do que nenhum botão.
  const podeOperar = eu.role !== "viewer";

  const opcoesProspect: Opcao[] = prospects.map((p) => ({
    valor: JSON.stringify({
      prospect: p.id,
      company: p.company_id,
      contact: p.contact_id,
      campaign: p.campaign_id,
    }),
    rotulo:
      `${p.contact_name ?? p.contact_email ?? "sem nome"} — ` +
      `${p.company_name ?? "sem empresa"}` +
      `${p.campaign_name ? ` · ${p.campaign_name}` : ""}`,
  }));

  const opcoesConversa: Opcao[] = conversas.map((c) => ({
    valor: JSON.stringify({ conversation: c.id, campaign: c.campaign_id }),
    rotulo:
      `${c.contact_name ?? c.contact_email ?? "sem nome"} — ` +
      `${c.company_name ?? "sem empresa"} · ` +
      `${c.message_count} ${c.message_count === 1 ? "mensagem" : "mensagens"}` +
      `${c.awaiting_reply ? " · esperando resposta" : ""}`,
  }));

  const agora = Date.now();
  const emperrados = jobs.filter(
    (j) => j.status === "pending" && agora - new Date(j.created_at).getTime() > PARADO_MS,
  );
  const naFila = jobs.filter((j) => j.status !== "done");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Agentes</h1>
        <p className="lede">
          Quatro agentes, um papel: descobrir, abordar, conversar e qualificar. O
          trabalhador dispara sozinho o que está na cadência; aqui é onde uma pessoa
          manda trabalhar agora — um prospect que não pode esperar, uma resposta que
          precisa sair hoje.
        </p>

        {podeOperar ? (
          <Disparar
            agentes={catalogo.map((a) => ({
              kind: a.kind,
              rotulo: AGENTES[a.kind]?.rotulo ?? a.name,
              faz: AGENTES[a.kind]?.faz ?? "",
              unidades: a.units_per_run,
            }))}
            prospects={opcoesProspect}
            conversas={opcoesConversa}
          />
        ) : null}

        <h2>Como cada agente está configurado</h2>
        <p className="ajuda">
          Modelo, instruções e teto de resposta valem por empresa e por agente. Trocar o
          modelo é a alavanca de custo mais direta que existe aqui: a pesquisa em Opus e a
          abordagem em Haiku é uma escolha legítima, e o consumo do mês mostra a diferença.
        </p>
        {config.map((c) => {
          const rotulo = AGENTES[c.kind]?.rotulo ?? c.name;
          return (
            <article
              className="card"
              data-secao="config-agente"
              data-agente={c.kind}
              key={c.kind}
            >
              <h3>
                {rotulo} <span className="tag">{c.model}</span>
                {c.configured ? null : <span className="tag">padrão</span>}
                {c.is_active ? null : <span className="tag urgente">desativado</span>}
              </h3>
              <div className="meta">
                {c.units_per_run} {c.units_per_run === 1 ? "unidade" : "unidades"} por
                execução · teto de resposta {c.max_output_tokens.toLocaleString("pt-BR")}{" "}
                tokens
                {c.configured && c.instructions !== c.default_instructions
                  ? " · instrução própria"
                  : " · instrução padrão"}
              </div>
              {podeOperar ? <ConfigurarAgente config={c} rotulo={rotulo} /> : null}
            </article>
          );
        })}

        {emperrados.length > 0 ? (
          <p className="erro">
            {emperrados.length} {emperrados.length === 1 ? "trabalho está" : "trabalhos estão"} na
            fila há mais de cinco minutos. A fila só anda com o processo trabalhador no ar — se
            ele está fora, nada aqui vai sair do lugar.
          </p>
        ) : null}

        <h2>Na fila</h2>
        {naFila.length === 0 ? (
          <p className="empty">Nada esperando.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>trabalho</th>
                <th>estado</th>
                <th>tentativas</th>
                <th>entrou em</th>
              </tr>
            </thead>
            <tbody>
              {naFila.map((j) => {
                const agente = String(
                  (j.payload as { agent?: string }).agent ?? "",
                );
                return (
                  <tr key={j.id}>
                    <td>{AGENTES[agente]?.rotulo ?? j.kind}</td>
                    <td>
                      <span className="tag">{ESTADO_JOB[j.status] ?? j.status}</span>
                      {j.last_error ? <span className="erro"> {j.last_error}</span> : null}
                    </td>
                    <td>
                      {j.attempts} de {j.max_attempts}
                    </td>
                    <td>{quando(j.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <h2>Execuções</h2>
        {runs.length === 0 ? (
          <p className="empty">Nenhum agente rodou nesta empresa ainda.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>agente</th>
                <th>estado</th>
                <th>unidades</th>
                <th>quando</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{AGENTES[r.agent_kind]?.rotulo ?? r.agent_kind}</td>
                  <td>
                    <span className="tag">{ESTADO_RUN[r.status] ?? r.status}</span>
                    {r.error ? <span className="erro"> {r.error}</span> : null}
                  </td>
                  <td>{r.units}</td>
                  <td>{quando(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  );
}
