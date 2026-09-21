"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../hydrated";

import type { Member } from "@/lib/api";

import { alternarMembro, convidarMembro, mudarPapel, removerMembro, trocarSenha } from "../actions";

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
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Convidar</h3>
      <p className="ajuda">
        Você escolhe a senha inicial e ela pode trocar depois, nesta mesma tela. Entregue
        por um canal seguro — por enquanto não há email de convite.
      </p>
      <form action={convidar} className="campos">
        <div className="dupla">
          <label>
            Email
            <input name="email" type="email" required />
          </label>
          <label>
            Nome
            <input name="full_name" />
          </label>
        </div>
        <div className="dupla">
          <label>
            Senha inicial
            <input name="password" type="password" minLength={10} required />
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
          {convidando ? "Convidando…" : "Convidar"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
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
