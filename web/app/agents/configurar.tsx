"use client";

import { useActionState } from "react";

import type { AgentConfig } from "@/lib/api";

import { salvarConfigAgente } from "../actions";
import MarcaDeHidratacao from "../hydrated";

/**
 * Os modelos que a plataforma sabe cobrar.
 *
 * A tabela de preços do backend é o que permite medir margem; um modelo fora
 * dela seria gasto medido pelo teto. A API recusa o que não está aqui, então a
 * tela oferece exatamente a mesma lista — em vez de deixar a pessoa digitar e
 * descobrir no erro.
 */
const MODELOS: { valor: string; rotulo: string }[] = [
  { valor: "claude-opus-5", rotulo: "Opus 5 — o mais capaz (padrão)" },
  { valor: "claude-opus-4-8", rotulo: "Opus 4.8" },
  { valor: "claude-sonnet-5", rotulo: "Sonnet 5 — equilíbrio de custo" },
  { valor: "claude-haiku-4-5", rotulo: "Haiku 4.5 — o mais barato e rápido" },
  { valor: "claude-fable-5-1", rotulo: "Fable 5.1 — escrita" },
];

export function ConfigurarAgente({
  config,
  rotulo,
}: {
  config: AgentConfig;
  rotulo: string;
}) {
  const [resultado, salvar, salvando] = useActionState(salvarConfigAgente, null);

  return (
    <details className="dobra">
      <MarcaDeHidratacao />
      <summary>
        Configurar {rotulo.toLowerCase()} — {config.model}
        {config.configured ? "" : " (padrão da plataforma)"}
      </summary>
      <form action={salvar} className="campos">
        <input type="hidden" name="kind" value={config.kind} />

        <label>
          Modelo
          <select name="model" defaultValue={config.model}>
            {MODELOS.map((m) => (
              <option key={m.valor} value={m.valor}>
                {m.rotulo}
              </option>
            ))}
          </select>
        </label>

        <label>
          Instruções deste agente
          <textarea
            name="instructions"
            rows={5}
            maxLength={8000}
            defaultValue={config.configured ? config.instructions : ""}
            placeholder={config.default_instructions}
          />
        </label>
        <p className="ajuda">
          Substitui a instrução padrão inteira — não é um acréscimo. Em branco volta ao
          padrão, que é o texto em cinza acima. As regras que protegem a operação
          (não inventar fato, não prometer prazo, escalar preço para humano) vivem no
          código e continuam valendo de qualquer forma.
        </p>

        <div className="dupla">
          <label>
            Teto de resposta (tokens)
            <input
              name="max_output_tokens"
              type="number"
              min={500}
              max={32000}
              step={100}
              defaultValue={config.max_output_tokens}
            />
          </label>
          <label className="opcao">
            <input
              type="checkbox"
              name="is_active"
              value="true"
              defaultChecked={config.is_active}
            />
            Agente ativo
          </label>
        </div>
        <p className="ajuda">
          Resposta truncada é execução perdida — e paga. Se o veredito ou a pesquisa
          vierem cortados, é este número que sobe. Custa mais: saída é a parte cara.
        </p>

        <button type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar configuração"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </details>
  );
}
