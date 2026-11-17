import Link from "next/link";

import { api, type Company, type Me } from "@/lib/api";

import Nav from "../nav";
import { EditarConta, NovaConta } from "./forms";

/**
 * Contas-alvo.
 *
 * É a tabela que o import escreve e que a pesquisa lê. Antes desta tela ela só
 * existia por API, e o erro mais caro de uma planilha não tinha conserto: com o
 * domínio trocado, o Research Agent pesquisa a empresa errada, escreve com os
 * fatos dela, e ninguém percebe até o lead responder confuso.
 *
 * Não há apagar, de propósito: a conta está amarrada a prospects, pesquisa e
 * conversas. O que se faz aqui é corrigir.
 */
export default async function AccountsPage() {
  const [contas, eu] = await Promise.all([
    api<Company[]>("/api/v1/companies?limit=200"),
    api<Me>("/api/v1/auth/me"),
  ]);
  const podeOperar = eu.role !== "viewer";

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Contas-alvo</h1>
        <p className="lede">
          As empresas que os agentes pesquisam. O domínio é o que mais importa estar
          certo: é por ele que a pesquisa encontra a empresa.
        </p>

        {contas.length === 0 ? (
          <p className="empty">
            Nenhuma conta ainda. Elas nascem do <Link href="/prospects">import de lista</Link>{" "}
            — ou você cria uma aqui para pesquisar antes de ter a pessoa.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>conta</th>
                <th>domínio</th>
                <th>setor</th>
                <th>porte</th>
              </tr>
            </thead>
            <tbody>
              {contas.map((c) => (
                <tr key={c.id} data-secao="conta">
                  <td>
                    {c.name}
                    {c.description ? <span className="meta">{c.description}</span> : null}
                    {podeOperar ? <EditarConta conta={c} /> : null}
                  </td>
                  <td>{c.domain ?? <span className="meta">sem domínio</span>}</td>
                  <td>{c.industry ?? "—"}</td>
                  <td>
                    {c.employee_count ? `${c.employee_count} pessoas` : "—"}
                    {c.country ? <span className="meta">{c.country}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {podeOperar ? <NovaConta /> : null}
      </main>
    </>
  );
}
