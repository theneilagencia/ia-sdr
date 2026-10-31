"use client";

import { useActionState } from "react";

import type { AISettings } from "@/lib/api";

import { salvarChaveIA, testarChaveIA } from "../actions";

/**
 * Configuração da chave da Anthropic.
 *
 * Escrito para quem nunca ouviu falar em API key: diz o que é, onde pegar e
 * quanto custa. O campo é de senha e a chave salva nunca volta — só os quatro
 * últimos caracteres, para a pessoa reconhecer qual configurou.
 */
export default function AIForm({ estado }: { estado: AISettings }) {
  const [teste, testar, testando] = useActionState(testarChaveIA, null);
  const [salvo, salvar, salvando] = useActionState(salvarChaveIA, null);
  const resultado = salvo ?? teste;

  return (
    <section className="card">
      <h3>Inteligência artificial</h3>
      <p className="ajuda">
        Os agentes usam a Claude, da Anthropic. Cada empresa usa a própria chave: o consumo
        aparece na sua conta da Anthropic, e nada é compartilhado entre empresas.
      </p>

      <p className="estado">
        {estado.configured ? (
          <>
            <span className="ok">● Configurada</span> — chave terminada em{" "}
            <code>{estado.key_hint}</code>
            {estado.status === "error" ? " (a última chamada falhou; troque a chave)" : null}
          </>
        ) : estado.using_platform_key ? (
          <span className="aviso">● Usando a chave da plataforma</span>
        ) : (
          <span className="aviso">● Não configurada — os agentes não vão rodar</span>
        )}
      </p>

      <form action={salvar} className="campos">
        <label>
          Chave da API
          <input
            name="api_key"
            type="password"
            placeholder={estado.configured ? "digite para substituir" : "sk-ant-…"}
            autoComplete="off"
            required
          />
          <small>
            Pegue em console.anthropic.com → API Keys. Começa com <code>sk-ant-</code>.
          </small>
        </label>

        <div className="inline">
          <button className="primary" type="submit" disabled={salvando}>
            {salvando ? "Salvando…" : "Salvar"}
          </button>
          <button className="ghost" type="submit" formAction={testar} disabled={testando}>
            {testando ? "Testando…" : "Só testar"}
          </button>
        </div>
      </form>

      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}
