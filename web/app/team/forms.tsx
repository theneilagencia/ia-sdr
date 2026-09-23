"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../hydrated";

import type { Invitation, Member, MfaState } from "@/lib/api";

import {
  alternarMembro,
  confirmarSegundoFator,
  convidarMembro,
  desligarSegundoFator,
  iniciarSegundoFator,
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


/**
 * Verificação em duas etapas.
 *
 * Por que TOTP e não SMS: SMS depende de operadora, custa por mensagem e é
 * vulnerável a troca de chip — e, para uma plataforma que guarda a senha do
 * email e a chave de IA de cada cliente, o fator fraco seria pior do que a
 * ausência, porque parece proteção.
 *
 * Não há QR code na tela de propósito: gerar um exigiria uma biblioteca no
 * browser, e o link `otpauth://` faz o mesmo trabalho — no celular, tocar nele
 * abre o aplicativo com a conta pronta. No computador, a entrada manual da
 * chave é o caminho que todo autenticador aceita.
 */
export function SegundoFator({ estado }: { estado: MfaState }) {
  const [inicio, iniciar, iniciando] = useActionState(iniciarSegundoFator, null);
  const [confirma, confirmar, confirmando] = useActionState(confirmarSegundoFator, null);
  const [desliga, desligar, desligando] = useActionState(desligarSegundoFator, null);

  // O segredo só existe nesta resposta; recarregar a tela o perde de propósito.
  const segredo = inicio?.secret;
  const configurando = estado.pending || Boolean(segredo);

  return (
    <section className="card" data-secao="segundo-fator">
      <h3>Verificação em duas etapas</h3>

      {estado.enabled ? (
        <>
          <p className="estado">
            <span className="ok">● ativa</span> — o login pede um código do seu aplicativo
            autenticador. Restam <strong>{estado.recovery_codes_left}</strong> códigos de
            recuperação.
          </p>
          <p className="ajuda">
            Desligar exige a sua senha e um código atual: token de sessão sozinho não
            desfaz a proteção que existe justamente para o caso de a senha ter vazado.
          </p>
          <form action={desligar} className="campos">
            <div className="dupla">
              <label>
                Sua senha
                <input name="password" type="password" autoComplete="current-password" required />
              </label>
              <label>
                Código do aplicativo
                <input name="code" inputMode="numeric" autoComplete="one-time-code" required />
              </label>
            </div>
            <button className="ghost" type="submit" disabled={desligando}>
              {desligando ? "Desligando…" : "Desligar verificação em duas etapas"}
            </button>
          </form>
        </>
      ) : (
        <>
          <p className="ajuda">
            Uma senha vazada abre tudo o que esta empresa configurou aqui — a conta de
            email de prospecção e a chave da IA. Com duas etapas, não abre.
          </p>
          {!configurando ? (
            <form action={iniciar}>
              <button className="primary" type="submit" disabled={iniciando}>
                {iniciando ? "Gerando…" : "Configurar"}
              </button>
            </form>
          ) : null}

          {segredo ? (
            <>
              <p className="ajuda">
                No celular, toque no link para abrir seu aplicativo autenticador já com a
                conta preenchida. No computador, adicione uma conta manualmente e digite a
                chave abaixo.
              </p>
              <p>
                <a className="botao" href={inicio?.otpauth_uri}>
                  Abrir no aplicativo autenticador
                </a>
              </p>
              <input
                className="link-de-convite"
                data-campo="chave-do-fator"
                readOnly
                value={segredo}
                onFocus={(e) => e.currentTarget.select()}
              />
              <p className="ajuda">
                Esta chave aparece uma vez só. Se você fechar a tela antes de adicioná-la,
                comece de novo — não existe rota que a mostre outra vez.
              </p>
            </>
          ) : null}

          {configurando ? (
            <form action={confirmar} className="campos">
              <label>
                Código que o aplicativo está mostrando
                <input name="code" inputMode="numeric" autoComplete="one-time-code" required />
              </label>
              <button className="primary" type="submit" disabled={confirmando}>
                {confirmando ? "Confirmando…" : "Confirmar e ativar"}
              </button>
              <p className="ajuda">
                Enquanto você não confirmar, nada muda no seu login — é o que evita ficar
                trancado fora por um aplicativo configurado errado.
              </p>
            </form>
          ) : null}
        </>
      )}

      {/* As mensagens ficam **fora** dos dois galhos de propósito: desligar muda
          o estado do cartão, e uma confirmação renderizada dentro do galho
          "ativo" desapareceria no mesmo instante em que teria algo a dizer — a
          pessoa clicaria em Desligar e não veria resposta nenhuma. */}
      {desliga ? <p className={desliga.ok ? "ok" : "erro"}>{desliga.message}</p> : null}
      {inicio && !inicio.ok ? <p className="erro">{inicio.message}</p> : null}
      {confirma ? (
        <p className={confirma.ok ? "ok" : "erro"}>{confirma.message}</p>
      ) : null}
      {confirma?.recovery_codes ? (
        <ul className="lista" data-secao="codigos-de-recuperacao">
          {confirma.recovery_codes.map((c) => (
            <li key={c} className="mono">
              {c}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
