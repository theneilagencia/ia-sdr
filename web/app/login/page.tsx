"use client";

import { useActionState } from "react";

import { login } from "../actions";

export default function LoginPage() {
  const [erro, acao, pendente] = useActionState(login, null);

  return (
    <main className="shell login">
      <h1>Entrar</h1>
      <p className="lede">Sua força de trabalho comercial de IA.</p>
      <form action={acao}>
        <input name="email" type="email" placeholder="email" required autoFocus />
        <input name="password" type="password" placeholder="senha" required />
        <button className="primary" type="submit" disabled={pendente}>
          {pendente ? "Entrando…" : "Entrar"}
        </button>
        {erro ? <p className="erro">{erro}</p> : null}
      </form>
    </main>
  );
}
