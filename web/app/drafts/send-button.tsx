"use client";

import { useActionState, useEffect, useState } from "react";

import { buscarRespostas, enviarFila, enviarMensagem, reenviar } from "../actions";

/**
 * Marca no DOM que o React assumiu a página.
 *
 * Existe por causa de uma falha de verdade: a fumaça esperava por um elemento
 * renderizado no servidor e clicava em "Aprovar" logo depois. Numa máquina
 * rápida a hidratação já tinha terminado; num runner frio, não — e o clique
 * era engolido, o que aparecia como "aprovar não tira o rascunho da revisão"
 * de forma intermitente, sempre estourando o tempo de espera.
 *
 * O atributo não muda nada visualmente. Ele dá ao teste (e a quem depurar) uma
 * forma de saber que os formulários desta tela já respondem, em vez de supor.
 */
function useMarcaDeHidratacao() {
  const [hidratado, setHidratado] = useState(false);
  useEffect(() => setHidratado(true), []);
  return hidratado ? "1" : undefined;
}

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
  const hidratado = useMarcaDeHidratacao();

  return (
    <>
      <form action={acao} className="inline" data-hidratado={hidratado}>
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
