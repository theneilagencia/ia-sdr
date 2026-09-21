"use client";

import { useActionState, useState } from "react";

import { dispararAgente } from "../actions";
import MarcaDeHidratacao from "../hydrated";

export type Opcao = { valor: string; rotulo: string };

type Agente = { kind: string; rotulo: string; faz: string; unidades: number };

/** Agentes cujo alvo é uma conversa, não um prospect. */
const SOBRE_CONVERSA = new Set(["conversation"]);

/**
 * Escolher o agente e o alvo, nesta ordem.
 *
 * O alvo muda com o agente: conversa para o agente de conversa, pessoa para os
 * outros três. Trocar a lista no cliente é o que evita a pergunta que ninguém
 * deveria responder — "entity_type é prospect, company ou conversation?".
 */
export function Disparar({
  agentes,
  prospects,
  conversas,
}: {
  agentes: Agente[];
  prospects: Opcao[];
  conversas: Opcao[];
}) {
  const [resultado, enviar, enviando] = useActionState(dispararAgente, null);
  const [kind, setKind] = useState(agentes[0]?.kind ?? "");
  const escolhido = agentes.find((a) => a.kind === kind);
  const sobreConversa = SOBRE_CONVERSA.has(kind);
  const alvos = sobreConversa ? conversas : prospects;

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Mandar um agente trabalhar</h3>
      <form action={enviar} className="campos">
        <div className="dupla">
          <label>
            Agente
            <select name="agent" value={kind} onChange={(e) => setKind(e.target.value)}>
              {agentes.map((a) => (
                <option key={a.kind} value={a.kind}>
                  {a.rotulo}
                </option>
              ))}
            </select>
          </label>
          <label>
            {sobreConversa ? "Conversa" : "Prospect"}
            {/* O `key` troca o campo junto com o agente: sem isso o React
                reaproveita o select e o valor escolhido para uma lista ficaria
                pendurado na outra. */}
            <select key={sobreConversa ? "conversa" : "prospect"} name={sobreConversa ? "conversation" : "prospect"} defaultValue="">
              <option value="">— escolha —</option>
              {alvos.map((o) => (
                <option key={o.valor} value={o.valor}>
                  {o.rotulo}
                </option>
              ))}
            </select>
          </label>
        </div>

        {escolhido ? (
          <p className="ajuda">
            {escolhido.faz} Custa {escolhido.unidades}{" "}
            {escolhido.unidades === 1 ? "unidade" : "unidades"} de IA da cota do mês.
          </p>
        ) : null}

        {alvos.length === 0 ? (
          <p className="empty">
            {sobreConversa
              ? "Nenhuma conversa ainda. Elas aparecem quando um lead responde."
              : "Nenhum prospect na base. Importe uma lista em Prospects para começar."}
          </p>
        ) : (
          <button className="primary" type="submit" disabled={enviando}>
            {enviando ? "Trabalhando…" : "Disparar"}
          </button>
        )}
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}
