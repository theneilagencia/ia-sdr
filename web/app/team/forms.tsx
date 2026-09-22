"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../hydrated";

import type { Invitation, Member } from "@/lib/api";

import {
  alternarMembro,
  convidarMembro,
  mudarPapel,
  removerMembro,
  revogarConvite,
  trocarSenha,
} from "../actions";

const PAPEIS = [
  { valor: "owner", rotulo: "owner — tudo, inclusive cobrança" },
  { valor: "admin", rotulo: "admin — equipe e integrações" },
  { valor: "operator", rotulo: "operator — opera o funil e aprova texto" },
  { valor: "viewer", rotulo: "viewer — só leitura" },
];

export function LinhaDoMembro({
  membro,
  souEu,
  podeAdministrar,
  ultimoOwner,
}: {
  membro: Member;
  souEu: boolean;
  podeAdministrar: boolean;
  ultimoOwner: boolean;
}) {
  const [papel, mudar, mudando] = useActionState(mudarPapel, null);
  const [acesso, alternar, alternando] = useActionState(alternarMembro, null);
  const [saida, remover, removendo] = useActionState(removerMembro, null);
  const aviso = papel ?? acesso ?? saida;

  // As duas travas do backend, ditas antes do clique em vez de depois do erro:
  // ninguém se rebaixa ou se remove, e o último owner ativo não sai.
  const travado = souEu || ultimoOwner;
  const motivo = souEu
    ? "Você não pode mudar o próprio papel — peça a outro administrador."
    : "É a única pessoa com papel de owner. Promova outra antes de mudar esta.";

  return (
    <tr>
      <td>
        {membro.full_name || "(sem nome)"}
        <br />
        <span className="ajuda">{membro.email}</span>
        {aviso ? <p className={aviso.ok ? "ok" : "erro"}>{aviso.message}</p> : null}
      </td>
      <td>
        {podeAdministrar && !travado ? (
          <form action={mudar} className="inline">
            <input type="hidden" name="user_id" value={membro.user_id} />
            <select name="role" defaultValue={membro.role} disabled={mudando}>
              {PAPEIS.map((p) => (
                <option key={p.valor} value={p.valor}>
                  {p.rotulo}
                </option>
              ))}
            </select>
            <button type="submit" disabled={mudando}>
              {mudando ? "…" : "Aplicar"}
            </button>
          </form>
        ) : (
          <>
            <span className="tag">{membro.role}</span>
            {podeAdministrar ? <p className="ajuda">{motivo}</p> : null}
          </>
        )}
      </td>
      <td>
        {membro.is_active ? <span className="ok">● ativo</span> : <span className="aviso">● suspenso</span>}
      </td>
      {podeAdministrar ? (
        <td>
          {travado ? null : (
            <>
              <form action={alternar} className="inline">
                <input type="hidden" name="user_id" value={membro.user_id} />
                <input type="hidden" name="is_active" value={String(!membro.is_active)} />
                <button className="ghost" type="submit" disabled={alternando}>
                  {membro.is_active ? "Suspender" : "Reativar"}
                </button>
              </form>
              <form action={remover} className="inline">
                <input type="hidden" name="user_id" value={membro.user_id} />
                <button className="ghost" type="submit" disabled={removendo}>
                  Remover
                </button>
              </form>
            </>
          )}
        </td>
      ) : null}
    </tr>
  );
}

export function Convidar() {
  const [resultado, convidar, convidando] = useActionState(convidarMembro, null);

  return (
    <section className="card" data-secao="convidar">
      <MarcaDeHidratacao />
      <h3>Convidar</h3>
      <p className="ajuda">
        Você não escolhe a senha de ninguém: o convite gera um link, e quem entra define a
        própria senha ao aceitar. Se a pessoa já tem conta na plataforma, ela entra com a
        senha que já usa e ganha acesso a esta empresa também.
      </p>
      <form action={convidar} className="campos">
        <div className="dupla">
          <label>
            Email
            <input name="email" type="email" required />
          </label>
          <label>
            Papel
            <select name="role" defaultValue="operator">
              {PAPEIS.map((p) => (
                <option key={p.valor} value={p.valor}>
                  {p.rotulo}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button className="primary" type="submit" disabled={convidando}>
          {convidando ? "Convidando…" : "Gerar link de convite"}
        </button>
      </form>
      {resultado ? (
        <>
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
          {resultado.link ? (
            <>
              {/* Campo em vez de texto solto porque o que se faz com um link é
                  copiá-lo: `readOnly` e seleção no clique poupam a pessoa de
                  arrastar o mouse sobre 60 caracteres sem errar um. */}
              <input
                className="link-de-convite"
                data-campo="link-de-convite"
                readOnly
                value={resultado.link}
                onFocus={(e) => e.currentTarget.select()}
              />
              <p className="ajuda">
                Copie agora: por segurança este link não é guardado nem mostrado de novo.
                Perdeu? Convide o mesmo email outra vez — o link novo substitui este.
              </p>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

/**
 * Convites pendentes.
 *
 * Existem na tela porque um convite pendente já ocupa vaga do plano: sem esta
 * lista, quem administra vê "2 de 2 pessoas" com um membro só e não tem como
 * descobrir para quem foi o link que falta aceitar.
 */
export function Pendentes({
  convites,
  podeAdministrar,
}: {
  convites: Invitation[];
  podeAdministrar: boolean;
}) {
  return (
    <section className="card" data-secao="convites-pendentes">
      <h3>Convites pendentes</h3>
      {convites.length === 0 ? (
        <p className="ajuda">Nenhum convite esperando aceite.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>email</th>
              <th>papel</th>
              <th>expira</th>
              {podeAdministrar ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {convites.map((c) => (
              <LinhaDoConvite key={c.id} convite={c} podeAdministrar={podeAdministrar} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function LinhaDoConvite({
  convite,
  podeAdministrar,
}: {
  convite: Invitation;
  podeAdministrar: boolean;
}) {
  const [resultado, revogar, revogando] = useActionState(revogarConvite, null);
  const expira = new Date(convite.expires_at);

  return (
    <tr data-convite={convite.email}>
      <td>
        {convite.email}
        {resultado ? <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p> : null}
      </td>
      <td>
        <span className="tag">{convite.role}</span>
      </td>
      <td>
        <span className="ajuda">{expira.toLocaleDateString("pt-BR")}</span>
      </td>
      {podeAdministrar ? (
        <td>
          <form action={revogar} className="inline">
            <input type="hidden" name="id" value={convite.id} />
            <input type="hidden" name="email" value={convite.email} />
            <button className="ghost" type="submit" disabled={revogando}>
              {revogando ? "…" : "Revogar"}
            </button>
          </form>
        </td>
      ) : null}
    </tr>
  );
}

export function TrocarSenha({ email }: { email: string }) {
  const [resultado, trocar, trocando] = useActionState(trocarSenha, null);

  return (
    <section className="card">
      <h3>Sua senha</h3>
      <p className="ajuda">
        Trocar a senha encerra as outras sessões abertas — inclusive a de quem tiver o seu
        token. Esta aba continua conectada.
      </p>
      <form action={trocar} className="campos">
        <input type="hidden" name="email" value={email} autoComplete="username" />
        <div className="dupla">
          <label>
            Senha atual
            <input
              name="current_password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <label>
            Nova senha
            <input
              name="new_password"
              type="password"
              minLength={10}
              autoComplete="new-password"
              required
            />
          </label>
        </div>
        <button type="submit" disabled={trocando}>
          {trocando ? "Trocando…" : "Trocar senha"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}
