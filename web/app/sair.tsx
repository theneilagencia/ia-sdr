"use client";

import { logout } from "./actions";

/**
 * Encerrar a sessão — que existia como ação e não tinha botão.
 *
 * Faltava desde o começo, e ficou mais sério com o segundo fator: exigir código
 * para entrar não protege nada se a pessoa não consegue sair num computador
 * compartilhado. O botão apaga o cookie no servidor, que é o único lugar onde o
 * token existe.
 */
export default function Sair() {
  return (
    <form action={logout} className="inline" data-secao="sair">
      <button className="ghost" type="submit">
        Sair
      </button>
    </form>
  );
}
