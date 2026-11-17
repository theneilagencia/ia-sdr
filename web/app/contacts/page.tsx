import Link from "next/link";

import { api, type Company, type Contact, type Me } from "@/lib/api";

import Nav from "../nav";
import { EditarContato, NovoContato, RegistrarDescadastro } from "./forms";

const FILTROS = [
  { chave: "", rotulo: "Todos" },
  { chave: "descadastrados", rotulo: "Descadastrados" },
  { chave: "sem-email", rotulo: "Sem email" },
] as const;

/**
 * Contatos: as pessoas dentro das contas.
 *
 * Três coisas que só existiam por API e acontecem toda semana numa operação de
 * verdade: corrigir um email digitado errado, atualizar um cargo depois de uma
 * promoção e **registrar um descadastro pedido por fora** — por telefone, no
 * WhatsApp, numa resposta a alguém da equipe. Esse último é o que tem
 * consequência legal, e era o que exigia ir ao banco.
 *
 * O filtro de "sem email" existe porque essas pessoas são invisíveis no funil:
 * o agente de abordagem se recusa a escrever para elas, com razão, e sem uma
 * lista ninguém sabe que a planilha veio incompleta.
 */
export default async function ContactsPage({
  searchParams,
}: {
  searchParams: Promise<{ filtro?: string; q?: string }>;
}) {
  const { filtro = "", q = "" } = await searchParams;
  const params = new URLSearchParams({ limit: "200" });
  if (filtro === "descadastrados") params.set("opted_out", "true");
  if (q) params.set("q", q);

  const [todos, contas, eu] = await Promise.all([
    api<Contact[]>(`/api/v1/contacts?${params.toString()}`),
    api<Company[]>("/api/v1/companies?limit=200"),
    api<Me>("/api/v1/auth/me"),
  ]);
  const contatos = filtro === "sem-email" ? todos.filter((c) => !c.email) : todos;
  const podeOperar = eu.role !== "viewer";
  const nomeDaConta = new Map(contas.map((c) => [c.id, c.name]));
  const semEmail = todos.filter((c) => !c.email).length;

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Contatos</h1>
        <p className="lede">
          As pessoas das contas-alvo. Quem pede para sair, sai — e o registro disso vive
          aqui, inclusive quando o pedido chegou por telefone.
        </p>

        <div className="filtros">
          {FILTROS.map((f) => (
            <Link
              key={f.chave}
              className={f.chave === filtro ? "atual" : ""}
              href={f.chave ? `/contacts?filtro=${f.chave}` : "/contacts"}
            >
              {f.rotulo}
            </Link>
          ))}
        </div>

        <form className="inline" action="/contacts" method="get">
          <input name="q" defaultValue={q} placeholder="nome ou email" aria-label="Buscar" />
          <button type="submit">Buscar</button>
        </form>

        {semEmail > 0 && filtro !== "sem-email" ? (
          <p className="cota">
            <strong>{semEmail}</strong> {semEmail === 1 ? "contato" : "contatos"} sem email:
            o agente de abordagem não escreve para eles.{" "}
            <Link href="/contacts?filtro=sem-email">Ver quais</Link>
          </p>
        ) : null}

        {contatos.length === 0 ? (
          <p className="empty">
            {q || filtro
              ? "Nada com esse filtro."
              : "Nenhum contato ainda — eles nascem do import de lista, em Prospects."}
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>pessoa</th>
                <th>email</th>
                <th>conta</th>
                <th>situação</th>
              </tr>
            </thead>
            <tbody>
              {contatos.map((c) => (
                <tr key={c.id} data-secao="contato">
                  <td>
                    {c.full_name}
                    {c.title ? <span className="meta">{c.title}</span> : null}
                    {podeOperar ? <EditarContato contato={c} contas={contas} /> : null}
                  </td>
                  <td className="mono">
                    {c.email ?? <span className="meta">sem email</span>}
                  </td>
                  <td>
                    {c.company_id ? (nomeDaConta.get(c.company_id) ?? "—") : "—"}
                  </td>
                  <td>
                    {c.opted_out ? (
                      <span className="tag urgente">descadastrado</span>
                    ) : podeOperar ? (
                      <RegistrarDescadastro contato={c} />
                    ) : (
                      "ativo"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {podeOperar ? <NovoContato contas={contas} /> : null}
      </main>
    </>
  );
}
