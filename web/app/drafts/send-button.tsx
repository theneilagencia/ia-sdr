"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../hydrated";

import { buscarRespostas, enviarFila, enviarMensagem, reenviar } from "../actions";

/**
 * Botões de envio com resposta visível.
 *
 * Envio falha por motivos que a pessoa precisa ler — cota do dia, fora do
 * horário, senha do email recusada, servidor fora do ar. Um botão que apenas
 * não faz nada deixaria ela clicando de novo sem entender.
 */
export function SendOne({ id, disabled }: { id: string; disabled: boolean }) {
  const [resultado, acao, enviando] = useActionState(enviarMensagem, null);

  return (
    <>
      <form action={acao} className="inline">
        <input type="hidden" name="id" value={id} />
        <button type="submit" disabled={disabled || enviando}>
          {enviando ? "Enviando…" : "Enviar esta"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </>
  );
}

export function SendAll({ quantos, disabled }: { quantos: number; disabled: boolean }) {
  const [resultado, acao, enviando] = useActionState(enviarFila, null);

  return (
    <>
      <form action={acao} className="inline enviar-tudo">
        <button className="primary" type="submit" disabled={disabled || enviando}>
          {enviando ? "Enviando…" : `Enviar ${quantos} agora`}
        </button>
        {disabled ? (
          <span className="aviso">Cota de hoje esgotada — o resto sai amanhã.</span>
        ) : null}
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </>
  );
}

export function Requeue({ id }: { id: string }) {
  const [resultado, acao, enviando] = useActionState(reenviar, null);

  return (
    <>
      <form action={acao} className="inline">
        <input type="hidden" name="id" value={id} />
        <button type="submit" disabled={enviando}>
          {enviando ? "Devolvendo…" : "Tentar de novo"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </>
  );
}

export function FetchInbox() {
  const [resultado, acao, buscando] = useActionState(buscarRespostas, null);

  return (
    <>
      <MarcaDeHidratacao />
      <form action={acao} className="inline">
        <button className="ghost" type="submit" disabled={buscando}>
          {buscando ? "Lendo a caixa…" : "Buscar respostas"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </>
  );
}
