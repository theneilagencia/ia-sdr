import { api, type Invitation, type Me, type Member } from "@/lib/api";

import Nav from "../nav";
import { Convidar, LinhaDoMembro, Pendentes, TrocarSenha } from "./forms";

/**
 * Equipe, convites e papéis.
 *
 * Só existia por API, o que significava que convidar alguém exigia `curl`. E o
 * convite de antes criava a conta na hora, com uma senha digitada por quem
 * convidava: agora o que sai daqui é um link, e a senha é de quem entra.
 */
export default async function TeamPage() {
  const [membros, convites, eu] = await Promise.all([
    api<Member[]>("/api/v1/tenants/me/members"),
    api<Invitation[]>("/api/v1/tenants/me/invitations"),
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
          {membros.length} {membros.length === 1 ? "pessoa" : "pessoas"} nesta empresa
          {convites.length > 0
            ? `, mais ${convites.length} ${convites.length === 1 ? "convite" : "convites"} esperando aceite`
            : ""}
          . Quem revisa e aprova texto de IA precisa ser <strong>operator</strong> ou acima;{" "}
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

        <Pendentes convites={convites} podeAdministrar={podeAdministrar} />
        {podeAdministrar ? <Convidar /> : null}
        <TrocarSenha email={eu.email} />
      </main>
    </>
  );
}
