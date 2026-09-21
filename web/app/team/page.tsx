import { api, type Me, type Member } from "@/lib/api";

import Nav from "../nav";
import { Convidar, LinhaDoMembro, TrocarSenha } from "./forms";

/**
 * Equipe e papéis.
 *
 * Só existia por API, o que significava que convidar alguém exigia `curl` — e
 * que a senha de todo mundo era escolhida por quem convidou, sem forma de
 * trocar. As duas coisas ficam nesta tela.
 */
export default async function TeamPage() {
  const [membros, eu] = await Promise.all([
    api<Member[]>("/api/v1/tenants/me/members"),
    api<Me>("/api/v1/auth/me"),
  ]);
  const podeAdministrar = eu.role === "owner" || eu.role === "admin";
  const owners = membros.filter((m) => m.role === "owner" && m.is_active).length;

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Equipe</h1>
        <p className="lede">
          {membros.length} {membros.length === 1 ? "pessoa" : "pessoas"} nesta empresa.
          Quem revisa e aprova texto de IA precisa ser <strong>operator</strong> ou acima;{" "}
          <strong>viewer</strong> só olha.
        </p>

        <table>
          <thead>
            <tr>
              <th>pessoa</th>
              <th>papel</th>
              <th>acesso</th>
              {podeAdministrar ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {membros.map((m) => (
              <LinhaDoMembro
                key={m.user_id}
                membro={m}
                souEu={m.user_id === eu.user_id}
                podeAdministrar={podeAdministrar}
                ultimoOwner={m.role === "owner" && owners <= 1}
              />
            ))}
          </tbody>
        </table>

        {podeAdministrar ? <Convidar /> : null}
        <TrocarSenha email={eu.email} />
      </main>
    </>
  );
}
