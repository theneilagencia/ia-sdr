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
          <Link href="/prospects">Prospects</Link>
          <Link href="/campaigns">Campanhas</Link>
          <Link href="/settings">Configurações</Link>
        </nav>
        <span className="who">
          {tenant} · {me.email} · {me.role}
        </span>
      </div>
    </header>
  );
}
