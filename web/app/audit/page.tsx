import { api, type AuditEntry, type Member } from "@/lib/api";

import Nav from "../nav";

/** Ação interna → o que ela foi, em português. */
const ACOES: Record<string, string> = {
  "agent.run.succeeded": "agente executou",
  "agent.run.failed": "agente falhou",
  "agent.config.updated": "configuração de agente alterada",
  "message.approved": "rascunho aprovado",
  "message.rejected": "rascunho recusado",
  "message.sent": "email enviado",
  "message.send_failed": "envio falhou",
  "contact.opted_out": "descadastro registrado",
  "contact.updated": "contato alterado",
  "contact.deleted": "contato apagado",
  "contact.created": "contato criado",
  "company.created": "conta criada",
  "company.updated": "conta corrigida",
  "campaign.created": "campanha criada",
  "campaign.updated": "campanha alterada",
  "prospect.imported": "lista importada",
  "prospect.pushed_to_crm": "lead enviado ao CRM",
  "meeting.created": "reunião marcada",
  "knowledge.document_added": "documento adicionado",
  "knowledge.document_deleted": "documento apagado",
  "member.invited": "pessoa adicionada",
  "member.role_changed": "papel alterado",
  "member.deactivated": "pessoa desativada",
  "password.changed": "senha trocada",
  "settings.ai_key_saved": "chave de IA salva",
  "settings.email_saved": "conta de email salva",
  "settings.crm_saved": "conexão do CRM salva",
  "tenant.updated_by_platform_admin": "alterado pelo suporte da plataforma",
  "sequence.created": "cadência criada",
  "sequence.enrolled": "prospect inscrito em cadência",
};

/** De onde a ação partiu. `worker` é o que aconteceu sem ninguém olhando. */
const ORIGEM: Record<string, string> = {
  api: "pela tela",
  worker: "pelo trabalhador",
  admin: "pelo suporte da plataforma",
  public: "por link público",
  system: "pelo sistema",
};

function quando(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

/** O resumo do que mudou, sem despejar JSON na tela. */
function resumo(entrada: AuditEntry): string | null {
  const p = entrada.payload ?? {};
  const partes: string[] = [];
  for (const [chave, valor] of Object.entries(p)) {
    if (valor === null || valor === undefined || valor === "") continue;
    const texto = Array.isArray(valor)
      ? valor.join(", ")
      : typeof valor === "object"
        ? JSON.stringify(valor)
        : String(valor);
    partes.push(`${chave}: ${texto.length > 120 ? `${texto.slice(0, 120)}…` : texto}`);
    if (partes.length === 4) break;
  }
  return partes.length ? partes.join(" · ") : null;
}

/**
 * O log de auditoria — gravado desde o primeiro dia, visível a partir de agora.
 *
 * Responde a pergunta que aparece quando algo dá errado numa plataforma que age
 * sozinha: **quem fez isso?** Aprovação de rascunho, envio, descadastro, troca de
 * chave, alteração de limite pelo suporte da plataforma — tudo passa por aqui,
 * com autor, origem e hora.
 *
 * A origem importa tanto quanto o autor: `pelo trabalhador` é o que aconteceu
 * sem ninguém olhando, e é o que ninguém consegue reconstituir de memória.
 */
export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<{ limite?: string }>;
}) {
  const { limite = "100" } = await searchParams;
  const quantos = Math.min(Math.max(Number(limite) || 100, 20), 500);

  const [entradas, equipe] = await Promise.all([
    api<AuditEntry[]>(`/api/v1/tenants/me/audit?limit=${quantos}`),
    api<Member[]>("/api/v1/tenants/me/members"),
  ]);
  const nome = new Map(equipe.map((m) => [m.user_id, m.full_name || m.email]));

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Auditoria</h1>
        <p className="lede">
          Quem fez o quê, e o que a plataforma fez sozinha. As {quantos} ações mais
          recentes desta empresa.
        </p>

        {entradas.length === 0 ? (
          <p className="empty">Nada registrado ainda.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>quando</th>
                <th>ação</th>
                <th>quem</th>
                <th>o que mudou</th>
              </tr>
            </thead>
            <tbody>
              {entradas.map((e) => (
                <tr key={e.id} data-secao="auditoria">
                  <td className="mono">{quando(e.created_at)}</td>
                  <td>
                    {ACOES[e.action] ?? e.action}
                    {ACOES[e.action] ? <span className="meta">{e.action}</span> : null}
                  </td>
                  <td>
                    {e.actor_user_id
                      ? (nome.get(e.actor_user_id) ?? "pessoa removida da equipe")
                      : ORIGEM[e.source] ?? e.source}
                    <span className="meta">
                      {ORIGEM[e.source] ?? e.source}
                      {e.actor_role ? ` · ${e.actor_role}` : null}
                    </span>
                  </td>
                  <td>
                    {resumo(e) ?? <span className="meta">—</span>}
                    {e.resource_type ? (
                      <span className="meta">{e.resource_type}</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {entradas.length >= quantos && quantos < 500 ? (
          <p className="ajuda">
            <a href={`/audit?limite=${Math.min(quantos * 2, 500)}`}>Ver mais</a>
          </p>
        ) : null}
      </main>
    </>
  );
}
