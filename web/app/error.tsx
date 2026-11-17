"use client";

import Link from "next/link";

/**
 * A última rede: o que aparece quando nada previu a falha.
 *
 * Sem este arquivo, qualquer exceção numa página do servidor mostra a tela do
 * Next — "A server error occurred", em inglês, com um número de erro e um botão
 * de recarregar. Para quem está operando, isso não diz nem o que aconteceu nem o
 * que fazer.
 *
 * O que ela **não** faz é mostrar a mensagem do erro: em produção o Next já a
 * substitui por um texto genérico de propósito, e insistir nisso na tela seria
 * prometer um detalhe que não chega. O `digest` vai visível porque é a única
 * coisa que liga esta tela à linha no log do servidor.
 */
export default function Erro({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="shell login">
      <h1>Algo falhou aqui</h1>
      <p className="lede">
        Não foi você. A falha é do nosso lado, e nada do que você fez antes desta
        tela foi desfeito.
      </p>
      <p>
        <button className="primary" type="button" onClick={reset}>
          Tentar de novo
        </button>{" "}
        <Link className="botao" href="/">
          Voltar ao funil
        </Link>
      </p>
      {error.digest ? (
        <p className="ajuda">
          Se precisar pedir ajuda, mande este código: <span className="mono">{error.digest}</span>.
          É por ele que a falha é encontrada no registro do servidor.
        </p>
      ) : null}
    </main>
  );
}
