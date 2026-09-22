import Link from "next/link";

import { api, type Me } from "@/lib/api";

/** Cabeçalho com o tenant ativo à vista: em plataforma multiempresa, saber
 *  em qual empresa você está evita o pior tipo de engano. */
export default async function Nav() {
  const me = await api<Me>("/api/v1/auth/me");
  const tenant =
    me.memberships.find((m) => m.tenant_id === me.tenant_id)?.tenant_name ?? "—";

  return (
    <header className="top">
      <div className="inner">
        <strong>AI Sales Workforce</strong>
        <nav>
          <Link href="/">Funil</Link>
          <Link href="/drafts">Revisão</Link>
          <Link href="/conversations">Conversas</Link>
          <Link href="/prospects">Prospects</Link>
          <Link href="/accounts">Contas</Link>
          <Link href="/contacts">Contatos</Link>
          <Link href="/campaigns">Campanhas</Link>
          <Link href="/agents">Agentes</Link>
          <Link href="/sequences">Cadências</Link>
          <Link href="/brain">Cérebro</Link>
          <Link href="/knowledge">Conhecimento</Link>
          <Link href="/team">Equipe</Link>
          <Link href="/audit">Auditoria</Link>
          <Link href="/settings">Configurações</Link>
          {/* Só quem opera a plataforma vê o painel dela. Quem não tem a marca
              não ganha um link que só levaria a uma recusa. */}
          {me.is_platform_admin ? <Link href="/platform">Plataforma</Link> : null}
        </nav>
        <span className="who">
          {tenant} · {me.email} · {me.role}
        </span>
      </div>
    </header>
  );
}
