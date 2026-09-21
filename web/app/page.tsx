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
            <div className="k">custo estimado</div>
          </div>
        </div>
      </main>
    </>
  );
}
