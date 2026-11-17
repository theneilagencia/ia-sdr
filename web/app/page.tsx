import { api, type Funnel, type Usage } from "@/lib/api";

import Nav from "./nav";

const ETAPAS: [keyof Funnel, string][] = [
  ["prospects", "prospects"],
  ["researched", "pesquisados"],
  ["contacted", "contatados"],
  ["engaged", "engajados"],
  ["qualified", "qualificados"],
  ["meetings", "reuniões"],
];

export default async function Home() {
  const [funil, uso] = await Promise.all([
    api<Funnel>("/api/v1/prospects/funnel"),
    api<Usage>("/api/v1/tenants/me/usage"),
  ]);

  const limite = uso.ai_units_limit < 0 ? "ilimitado" : uso.ai_units_limit.toLocaleString("pt-BR");
  // O teto em dólar só aparece quando existe: dizer "sem teto" a quem não
  // contratou teto é informação que não muda nada para quem lê.
  const tetoEmDolar =
    uso.estimated_cost_limit_usd > 0
      ? uso.estimated_cost_limit_usd.toLocaleString("pt-BR", {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: 0,
        })
      : null;

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Sua IA está trabalhando</h1>
        <p className="lede">Do prospect importado à reunião marcada.</p>

        <div className="funnel">
          {ETAPAS.map(([chave, rotulo]) => (
            <div key={chave}>
              <div className="n">{(funil[chave] as number).toLocaleString("pt-BR")}</div>
              <div className="k">{rotulo}</div>
            </div>
          ))}
        </div>

        {funil.bounced > 0 || funil.disqualified > 0 ? (
          /* As saídas ficam junto do funil, e não escondidas: a taxa de retorno
             é o número que queima o domínio de quem envia, e quem não a vê
             descobre pela reputação do domínio, meses depois. */
          <p className="saidas">
            {funil.bounced > 0 ? (
              <>
                <strong>{funil.bounced.toLocaleString("pt-BR")}</strong>{" "}
                {funil.bounced === 1 ? "email voltou" : "emails voltaram"} (endereço
                inválido)
                {funil.contacted > 0
                  ? ` — ${Math.round((funil.bounced / funil.contacted) * 100)}% dos contatados`
                  : null}
              </>
            ) : null}
            {funil.bounced > 0 && funil.disqualified > 0 ? " · " : null}
            {funil.disqualified > 0 ? (
              <>
                <strong>{funil.disqualified.toLocaleString("pt-BR")}</strong>{" "}
                {funil.disqualified === 1 ? "saiu" : "saíram"} (descadastro ou
                desqualificação)
              </>
            ) : null}
          </p>
        ) : null}

        <h2>Aderência ao ICP</h2>
        {Object.keys(funil.by_band).length === 0 ? (
          <p className="empty">Nenhum prospect pontuado ainda.</p>
        ) : (
          <div className="funnel">
            {["A", "B", "C", "D"].map((banda) => (
              <div key={banda}>
                <div className="n">{(funil.by_band[banda] ?? 0).toLocaleString("pt-BR")}</div>
                <div className="k">banda {banda}</div>
              </div>
            ))}
          </div>
        )}

        <h2>Consumo do mês</h2>
        <div className="funnel">
          <div>
            <div className="n">{uso.ai_units_used.toLocaleString("pt-BR")}</div>
            <div className="k">unidades usadas</div>
          </div>
          <div>
            <div className="n">{limite}</div>
            <div className="k">limite do plano</div>
          </div>
          <div>
            <div className="n">
              {uso.estimated_cost_usd.toLocaleString("pt-BR", {
                style: "currency",
                currency: "USD",
              })}
            </div>
            <div className="k">
              {tetoEmDolar ? `custo estimado · teto ${tetoEmDolar}` : "custo estimado"}
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
