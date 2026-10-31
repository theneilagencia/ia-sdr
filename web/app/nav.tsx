import Link from "next/link";

import { api, type Me } from "@/lib/api";

import Sair from "./sair";
import TrocarEmpresa from "./trocar-empresa";

/** Cabeçalho com o tenant ativo à vista: em plataforma multiempresa, saber
 *  em qual empresa você está evita o pior tipo de engano. */
export default async function Nav() {
  const me = await api<Me>("/api/v1/auth/me");
  const tenant =
    me.memberships.find((m) => m.tenant_id === me.tenant_id)?.tenant_name ?? "—";
  const administra = me.role === "owner" || me.role === "admin";

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
          {/* Auditoria é permissão de admin. Oferecer o link a quem a API vai
              recusar é o mesmo engano do painel da plataforma: o menu promete
              uma tela que não abre. A tela ainda explica a recusa, para quem
              chegar por link salvo ou tiver o papel rebaixado com a aba aberta. */}
          {administra ? <Link href="/audit">Auditoria</Link> : null}
          <Link href="/settings">Configurações</Link>
          {/* Só quem opera a plataforma vê o painel dela. Quem não tem a marca
              não ganha um link que só levaria a uma recusa. */}
          {me.is_platform_admin ? <Link href="/platform">Plataforma</Link> : null}
        </nav>
        <span className="who">
          {/* Identidade é global nesta plataforma: a mesma conta serve várias
              empresas — e o convite tornou isso comum. Com mais de um vínculo, a
              barra troca o nome fixo por um seletor; sem ele, quem entrava caía
              sempre na empresa mais antiga e não tinha como chegar na outra. */}
          {me.memberships.length > 1 ? (
            <TrocarEmpresa atual={me.tenant_id} vinculos={me.memberships} />
          ) : (
            tenant
          )}{" "}
          · {me.email} · {me.role} <Sair />
        </span>
      </div>
    </header>
  );
}
