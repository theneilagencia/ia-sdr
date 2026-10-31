"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../../hydrated";

import { aceitarConvite } from "../../actions";

export default function Aceitar({ token }: { token: string }) {
  const [erro, aceitar, aceitando] = useActionState(aceitarConvite, null);

  return (
    <form action={aceitar} data-secao="aceitar-convite">
      <MarcaDeHidratacao />
      {/* O token vem da URL e volta no corpo do POST: o Server Action é a única
          coisa que fala com a API, então ele precisa recebê-lo por aqui. */}
      <input type="hidden" name="token" value={token} />
      <input name="full_name" placeholder="seu nome" autoComplete="name" />
      {/* `minLength` combina com os dois casos porque todo caminho que cria ou
          troca senha na plataforma exige 10: não existe conta antiga com senha
          menor que o navegador fosse barrar aqui. E `current-password` deixa o
          gerenciador oferecer a senha de quem já tem conta. */}
      <input
        name="password"
        type="password"
        placeholder="senha (10+ caracteres)"
        minLength={10}
        autoComplete="current-password"
        required
      />
      <button className="primary" type="submit" disabled={aceitando}>
        {aceitando ? "Entrando…" : "Aceitar e entrar"}
      </button>
      {erro ? <p className="erro">{erro}</p> : null}
      <p className="ajuda">
        Se você já tem conta nesta plataforma, informe a senha dela: o convite dá acesso a
        mais uma empresa e não troca sua senha. O nome só é usado quando a conta é nova.
      </p>
    </form>
  );
}
