"use client";

import { useActionState } from "react";

import type { AdminTenant, Invoice } from "@/lib/api";
import { emCentavos, paraOCampo } from "@/lib/dinheiro";

import {
  alternarEmpresaDaPlataforma,
  fecharMes,
  mudarEstadoDaFatura,
  salvarEmpresaDaPlataforma,
} from "../actions";
import MarcaDeHidratacao from "../hydrated";

/** Os limites que um override pode mexer, com o nome que uma pessoa entende. */
const LIMITES: { chave: string; rotulo: string }[] = [
  { chave: "campaigns", rotulo: "Campanhas" },
  { chave: "prospects_per_month", rotulo: "Prospects por mês" },
  { chave: "users", rotulo: "Pessoas" },
  { chave: "email_accounts", rotulo: "Contas de email" },
  { chave: "ai_units_per_month", rotulo: "Unidades de IA por mês" },
  { chave: "ai_cost_usd_per_month", rotulo: "Custo de IA por mês (US$)" },
  { chave: "knowledge_documents", rotulo: "Documentos na base" },
];

/** As três moedas que um contrato desta plataforma usa hoje. */
const MOEDAS = ["BRL", "USD", "EUR"];

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
        <p className="ajuda">
          O custo em dólar é outro freio, e mede outra coisa: unidade é o que o cliente
          compra, dólar é o que a Anthropic cobra. Vale para o gasto que a unidade não
          pega — execução que falha depois de chamar o modelo cobra e não consome
          unidade. Chegando ao teto, os agentes desta empresa param até o mês virar ou
          você subir o número.
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

        <h4>Contrato</h4>
        <p className="ajuda">
          O que este cliente paga por mês, na moeda do contrato — <code>1999,90</code>, sem
          separador de milhar. Em branco não mexe no valor que já está salvo; zero significa
          &ldquo;sem preço ainda&rdquo;: o mês fecha, mostra o consumo e não cobra nada.
        </p>
        <div className="dupla">
          <label>
            Mensalidade{" "}
            <small>
              hoje:{" "}
              {empresa.contract_monthly_cents
                ? emCentavos(empresa.contract_monthly_cents, empresa.contract_currency)
                : "sem preço"}
            </small>
            <input
              name="contract_monthly_cents"
              inputMode="decimal"
              defaultValue={paraOCampo(empresa.contract_monthly_cents)}
              placeholder="sem preço ainda"
            />
          </label>
          <label>
            Moeda
            <select name="contract_currency" defaultValue={empresa.contract_currency}>
              {MOEDAS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Unidade de IA acima da cota{" "}
          <small>
            hoje:{" "}
            {empresa.overage_cents_per_unit
              ? emCentavos(empresa.overage_cents_per_unit, empresa.contract_currency)
              : "não cobra excedente"}
          </small>
          <input
            name="overage_cents_per_unit"
            inputMode="decimal"
            defaultValue={paraOCampo(empresa.overage_cents_per_unit)}
            placeholder="não cobra excedente"
          />
        </label>
        <p className="ajuda">
          Excedente só existe com preço <em>e</em> com cota de unidades definida. Em plano
          ilimitado não há excedente por definição — não há de onde exceder.
        </p>

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


/**
 * Fechar o mês: uma fatura por empresa ativa, com o consumo medido por trás.
 *
 * O padrão é o mês que acabou, que é quando se fecha. Rodar de novo é seguro de
 * propósito — recalcula rascunho e não toca no que já foi emitido.
 */
export function FecharMes({ ano, mes }: { ano: number; mes: number }) {
  const [resultado, fechar, fechando] = useActionState(fecharMes, null);

  return (
    <div data-secao="fechar-mes">
      <MarcaDeHidratacao />
      <form action={fechar} className="inline">
        <input type="hidden" name="year" value={ano} />
        <input type="hidden" name="month" value={mes} />
        <button className="primary" type="submit" disabled={fechando}>
          {fechando ? "Fechando…" : `Fechar ${String(mes).padStart(2, "0")}/${ano}`}
        </button>
        <span className="ajuda">
          Gera ou atualiza o rascunho de cada empresa ativa. Nada é enviado ao cliente
          daqui: a fatura é um número seu, para emitir nota onde você já emite.
        </span>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </div>
  );
}

/** Os rótulos de cada passo, do lado de quem opera. */
const PASSOS: Record<string, { proximo: string; rotulo: string }[]> = {
  draft: [
    { proximo: "issued", rotulo: "Emitir" },
    { proximo: "void", rotulo: "Cancelar" },
  ],
  issued: [
    { proximo: "paid", rotulo: "Marcar como paga" },
    { proximo: "void", rotulo: "Cancelar" },
  ],
  // Paga ainda pode ser anulada, e o botão existe porque o serviço permite:
  // quem baixou a fatura errada precisa de saída pela tela, não por `curl`.
  // Cancelada é o único estado final de verdade.
  paid: [{ proximo: "void", rotulo: "Cancelar" }],
  void: [],
};

export function AcoesDaFatura({ fatura }: { fatura: Invoice }) {
  const [resultado, mudar, mudando] = useActionState(mudarEstadoDaFatura, null);
  const passos = PASSOS[fatura.status] ?? [];

  if (passos.length === 0) {
    return <span className="ajuda">nada a fazer</span>;
  }

  return (
    <>
      <form action={mudar} className="inline" data-secao="acoes-da-fatura">
        <input type="hidden" name="invoice_id" value={fatura.id} />
        {passos.map((passo) => (
          <button
            key={passo.proximo}
            name="status"
            value={passo.proximo}
            type="submit"
            className={passo.proximo === "issued" ? "primary" : "ghost"}
            disabled={mudando}
          >
            {passo.rotulo}
          </button>
        ))}
      </form>
      {resultado ? (
        <span className={resultado.ok ? "ok" : "erro"}>{resultado.message}</span>
      ) : null}
    </>
  );
}
