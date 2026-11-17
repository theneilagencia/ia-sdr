"use client";

import { useActionState, useState } from "react";

import { login } from "../actions";

/**
 * Entrar — em uma ou duas etapas, dependendo da conta.
 *
 * O campo de código só aparece depois de a API pedir, e ela só pede depois de a
 * senha conferir. É o que permite esta tela falar de segundo fator sem contar
 * nada a quem está chutando senha: quem erra a senha recebe a mesma frase de
 * sempre, sem pista de que a conta existe ou está protegida.
 *
 * **Os campos são controlados de propósito.** O React limpa formulário depois
 * que um Server Action termina, e com campos soltos a segunda etapa mandava
 * email e senha vazios — quem digitasse o código receberia "email ou senha
 * inválidos" sem ter errado nada, e a conta com segundo fator ficaria sem
 * caminho de entrada. Guardar os três valores em estado é o que faz a segunda
 * volta ter o que enviar.
 */
export default function LoginPage() {
  const [estado, acao, pendente] = useActionState(login, null);
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const pedindoCodigo = estado?.etapa === "codigo";

  return (
    <main className="shell login">
      <h1>Entrar</h1>
      <p className="lede">Sua força de trabalho comercial de IA.</p>
      <form action={acao}>
        <input
          name="email"
          type="email"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
        />
        <input
          name="password"
          type="password"
          placeholder="senha"
          value={senha}
          onChange={(e) => setSenha(e.target.value)}
          autoComplete="current-password"
          required
        />
        {pedindoCodigo ? (
          <>
            {/* `autoFocus` aqui e não no email: a segunda volta começa neste
                campo, e quem está com o celular na mão não deveria ter de
                clicar para digitar. */}
            <input
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="código de 6 dígitos"
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              autoFocus
              required
            />
            <p className="ajuda">
              O código muda a cada trinta segundos e serve uma vez. Perdeu o aparelho?
              Use um dos códigos de recuperação que você guardou ao ativar.
            </p>
          </>
        ) : null}
        <button className="primary" type="submit" disabled={pendente}>
          {pendente ? "Entrando…" : pedindoCodigo ? "Confirmar código" : "Entrar"}
        </button>
        {estado ? (
          <p className={estado.etapa === "codigo" ? "aviso" : "erro"}>{estado.message}</p>
        ) : null}
      </form>
    </main>
  );
}
