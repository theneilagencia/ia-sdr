"use client";

import { useActionState } from "react";

import type { AdminTenant } from "@/lib/api";

import { alternarEmpresaDaPlataforma, salvarEmpresaDaPlataforma } from "../actions";
import MarcaDeHidratacao from "../hydrated";

/** Os limites que um override pode mexer, com o nome que uma pessoa entende. */
const LIMITES: { chave: string; rotulo: string }[] = [
  { chave: "campaigns", rotulo: "Campanhas" },
  { chave: "prospects_per_month", rotulo: "Prospects por mês" },
  { chave: "users", rotulo: "Pessoas" },
  { chave: "email_accounts", rotulo: "Contas de email" },
  { chave: "ai_units_per_month", rotulo: "Unidades de IA por mês" },
  { chave: "knowledge_documents", rotulo: "Documentos na base" },
];

const PLANOS = ["starter", "growth", "enterprise"];
const ASSINATURAS = ["trial", "active", "past_due", "canceled"];

export function EditarEmpresa({ empresa }: { empresa: AdminTenant }) {
  const [resultado, salvar, salvando] = useActionState(salvarEmpresaDaPlataforma, null);

  return (
    <details className="dobra">
      <MarcaDeHidratacao />
      <summary>Plano, assinatura e limites</summary>
      <form action={salvar} className="campos">
        <input type="hidden" name="tenant_id" value={empresa.id} />
        <div className="dupla">
          <label>
            Plano
            <select name="plan" defaultValue={empresa.plan}>
              {PLANOS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label>
            Assinatura
            <select name="subscription_status" defaultValue={empresa.subscription_status}>
              {ASSINATURAS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
        </div>

        <h4>Limites contratados</h4>
        <p className="ajuda">
          Em branco vale o que o plano dá — o número cinza ao lado. <code>-1</code> é
          ilimitado. É o valor que a cota consulta, e é ele que responde &ldquo;por que
          este cliente travou&rdquo;.
        </p>
        {LIMITES.map((limite) => {
          const doPlano = empresa.effective_limits?.[limite.chave];
          const atual = empresa.limit_overrides?.[limite.chave];
          return (
            <label key={limite.chave}>
              {limite.rotulo}{" "}
              <small>
                hoje: {typeof doPlano === "number" ? (doPlano === -1 ? "ilimitado" : doPlano) : "—"}
              </small>
              <input
                name={`limite_${limite.chave}`}
                type="number"
                step={1}
                defaultValue={atual ?? ""}
                placeholder="o do plano"
              />
            </label>
          );
        })}

        <button className="primary" type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar"}
        </button>
        {resultado ? (
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
        ) : null}
      </form>
    </details>
  );
}

export function Suspender({ empresa }: { empresa: AdminTenant }) {
  const [resultado, alternar, alternando] = useActionState(alternarEmpresaDaPlataforma, null);

  return (
    <form action={alternar} className="inline">
      <input type="hidden" name="tenant_id" value={empresa.id} />
      <input type="hidden" name="is_active" value={empresa.is_active ? "false" : "true"} />
      <button className="ghost" type="submit" disabled={alternando}>
        {empresa.is_active ? "Suspender acesso" : "Reativar acesso"}
      </button>
      <span className="ajuda">
        {empresa.is_active
          ? "Suspender bloqueia a entrada de todo mundo dessa empresa. O histórico fica."
          : "Reativar devolve o acesso a quem já estava lá."}
      </span>
      {resultado ? (
        <span className={resultado.ok ? "ok" : "erro"}>{resultado.message}</span>
      ) : null}
    </form>
  );
}
