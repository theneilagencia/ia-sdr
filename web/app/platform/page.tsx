import {
  api,
  type AdminTenant,
  type Me,
  type PlatformUsage,
  type SystemHealth,
} from "@/lib/api";

import Nav from "../nav";
import { EditarEmpresa, Suspender } from "./forms";

const PLANO: Record<string, string> = {
  starter: "Starter",
  growth: "Growth",
  enterprise: "Enterprise",
};

const ASSINATURA: Record<string, string> = {
  trial: "avaliação",
  active: "ativa",
  past_due: "em atraso",
  canceled: "cancelada",
};

const CONSUMO: Record<string, string> = {
  research: "pesquisa",
  deep_research: "pesquisa profunda",
  ai_message: "mensagem de IA",
  qualification: "qualificação",
  voice_interaction: "voz",
  prospect_imported: "prospect importado",
};

function dinheiro(valor: number) {
  return valor.toLocaleString("pt-BR", { style: "currency", currency: "USD" });
}

/**
 * O painel de quem opera a plataforma — não de quem a usa.
 *
 * Toda rota por trás desta tela atravessa o RLS de propósito e registra a ação
 * no log da empresa afetada: é o único lugar da plataforma onde se enxerga além
 * do próprio tenant, e por isso é o lugar onde a auditoria importa mais.
 *
 * O que ele **não** mostra é tão deliberado quanto o que mostra: nome de
 * campanha, lead, conversa e base de conhecimento do cliente não aparecem aqui.
 * Ver a conta de um cliente e ler as conversas dele são coisas diferentes, e a
 * marca de platform admin só concede a primeira.
 */
export default async function PlatformPage() {
  const eu = await api<Me>("/api/v1/auth/me");
  if (!eu.is_platform_admin) {
    return (
      <>
        <Nav />
        <main className="shell">
          <h1>Painel da plataforma</h1>
          <p className="empty">
            Esta tela é de quem opera a plataforma. Sua conta não tem essa marca — e não
            há nada a fazer aqui por conta própria: ela é concedida por linha de comando,
            no servidor.
          </p>
        </main>
      </>
    );
  }

  const [empresas, consumo, sistema] = await Promise.all([
    api<AdminTenant[]>("/api/v1/admin/tenants"),
    api<PlatformUsage>("/api/v1/admin/usage"),
    api<SystemHealth>("/api/v1/admin/system"),
  ]);
  const suspensas = empresas.filter((e) => !e.is_active).length;

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Painel da plataforma</h1>
        <p className="lede">
          {consumo.tenants} {consumo.tenants === 1 ? "empresa" : "empresas"},{" "}
          {consumo.active_tenants} {consumo.active_tenants === 1 ? "ativa" : "ativas"}
          {suspensas ? `, ${suspensas} ${suspensas === 1 ? "suspensa" : "suspensas"}` : null}.{" "}
          No mês: {consumo.total_units.toLocaleString("pt-BR")} unidades de IA,{" "}
          {dinheiro(consumo.estimated_cost_usd)} de custo estimado.
        </p>

        <h2>Consumo do mês, por tipo</h2>
        {Object.keys(consumo.by_kind).length === 0 ? (
          <p className="empty">Nada consumido neste mês.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>tipo</th>
                <th>unidades</th>
                <th>custo estimado</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(consumo.by_kind).map(([tipo, v]) => (
                <tr key={tipo}>
                  <td>{CONSUMO[tipo] ?? tipo}</td>
                  <td>{v.units.toLocaleString("pt-BR")}</td>
                  <td>{dinheiro(v.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2>Empresas</h2>
        {empresas.map((empresa) => (
          <article className="card" data-secao="empresa" key={empresa.id}>
            <h3>
              {empresa.name}{" "}
              <span className="tag">{PLANO[empresa.plan] ?? empresa.plan}</span>
              <span className="tag">
                {ASSINATURA[empresa.subscription_status] ?? empresa.subscription_status}
              </span>
              {empresa.is_active ? null : <span className="tag urgente">suspensa</span>}
            </h3>
            <div className="meta">
              {empresa.slug} · {empresa.users} {empresa.users === 1 ? "pessoa" : "pessoas"} ·{" "}
              {empresa.campaigns} {empresa.campaigns === 1 ? "campanha" : "campanhas"} ·{" "}
              {empresa.ai_units_this_month.toLocaleString("pt-BR")} unidades no mês ·{" "}
              {/* O gasto ao lado do teto contratado: é a única leitura que
                  responde "esta empresa está perto de travar por custo?". */}
              {dinheiro(empresa.estimated_cost_usd)}
              {typeof empresa.effective_limits?.ai_cost_usd_per_month === "number" &&
              empresa.effective_limits.ai_cost_usd_per_month > 0
                ? ` de ${dinheiro(empresa.effective_limits.ai_cost_usd_per_month)}`
                : null}{" "}
              · entrou em {new Date(empresa.created_at).toLocaleDateString("pt-BR")}
            </div>
            {/* Os recursos do plano respondem uma pergunta de suporte direta:
                "por que este cliente não consegue qualificar?" — porque o plano
                dele não inclui qualificação. */}
            <div className="meta">
              recursos:{" "}
              {Array.isArray(empresa.effective_limits?.features)
                ? (empresa.effective_limits.features as string[]).join(", ")
                : "—"}
            </div>
            <EditarEmpresa empresa={empresa} />
            <Suspender empresa={empresa} />
          </article>
        ))}

        <h2>Saúde dos agentes</h2>
        <div className="cota">
          {Object.keys(sistema.agent_runs).length === 0 ? (
            <strong>Nenhuma execução registrada ainda.</strong>
          ) : (
            Object.entries(sistema.agent_runs).map(([estado, quantos]) => (
              <span key={estado}>
                <strong>{quantos}</strong> {estado}{" "}
              </span>
            ))
          )}
        </div>

        {sistema.recent_failures.length === 0 ? (
          <p className="ok">Nenhuma falha recente.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>agente</th>
                <th>empresa</th>
                <th>erro</th>
                <th>quando</th>
              </tr>
            </thead>
            <tbody>
              {sistema.recent_failures.map((f) => (
                <tr key={f.id}>
                  <td>{f.agent}</td>
                  {/* Só o id: quem opera a plataforma precisa saber onde doeu,
                      não quem é o lead do cliente. */}
                  <td className="mono">{f.tenant_id.slice(0, 8)}</td>
                  <td>{f.error ?? f.status}</td>
                  <td>{new Date(f.created_at).toLocaleString("pt-BR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  );
}
