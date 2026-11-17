"use client";

import { useActionState } from "react";

import type { CrmSettings } from "@/lib/api";

import { desligarRavi, salvarRavi, testarRavi } from "../actions";

/**
 * Conexão com o RAVI, o CRM que a operação já usa.
 *
 * Esta plataforma não tem CRM próprio: o lead vive no RAVI, e o que o AI SDR faz
 * é pesquisar, pontuar, abordar e qualificar — empurrando isso para lá. Duas
 * bases com o mesmo lead divergem em uma semana.
 *
 * O token é campo de senha, nunca volta para a tela e aparece como os quatro
 * últimos caracteres, para a pessoa reconhecer qual configurou.
 */
export default function CrmForm({ estado }: { estado: CrmSettings }) {
  const [teste, testar, testando] = useActionState(testarRavi, null);
  const [salvo, salvar, salvando] = useActionState(salvarRavi, null);
  const resultado = salvo ?? teste;

  return (
    <section className="card">
      <h3>CRM (RAVI)</h3>
      <p className="ajuda">
        Opcional. Sem esta conexão a plataforma funciona inteira — o que não acontece é o
        lead aparecer no RAVI. O envio é só de ida (AI SDR → RAVI) e o RAVI deduplica por
        email ou telefone, então reenviar o mesmo lead não cria dois.
      </p>

      <p className="estado">
        {estado.configured ? (
          <>
            <span className="ok">● Conectado</span> — {estado.base_url}, empresa{" "}
            <code>{estado.ravi_tenant_id}</code>, token terminado em{" "}
            <code>{estado.token_hint}</code>
            {estado.last_error ? (
              <span className="erro"> · última falha: {estado.last_error}</span>
            ) : null}
          </>
        ) : (
          <span className="aviso">● Não conectado — nenhum lead sobe para o CRM</span>
        )}
      </p>

      <form action={salvar} className="campos">
        <div className="dupla">
          <label>
            Endereço do RAVI
            <input
              name="base_url"
              placeholder="https://api.ravi.seudominio.com"
              defaultValue={estado.base_url ?? ""}
              required
            />
            <small>O endereço da API, não o do painel onde vocês entram.</small>
          </label>
          <label>
            Identificador da empresa no RAVI
            <input
              name="ravi_tenant_id"
              placeholder="ex.: apymine"
              defaultValue={estado.ravi_tenant_id ?? ""}
              required
            />
            <small>
              Vai no cabeçalho <code>x-tenant-id</code>: é como o RAVI sabe de quem é o
              lead.
            </small>
          </label>
        </div>
        <label>
          Token do agente
          <input
            name="token"
            type="password"
            placeholder={estado.configured ? "digite para substituir" : "cole o token"}
            autoComplete="off"
            required
          />
          <small>Quem administra o RAVI gera este token. Ele nunca volta para esta tela.</small>
        </label>
        <label>
          Estágio inicial no funil do RAVI (opcional)
          <input name="default_stage" placeholder="deixe vazio para o RAVI decidir" />
        </label>

        <div className="inline">
          <button className="primary" type="submit" disabled={salvando}>
            {salvando ? "Testando e salvando…" : "Conectar"}
          </button>
          <button className="ghost" type="submit" formAction={testar} disabled={testando}>
            {testando ? "Testando…" : "Só testar"}
          </button>
          {estado.configured ? (
            // `formNoValidate` porque desligar não precisa dos campos — e o
            // token nunca volta preenchido, então a validação do browser
            // bloquearia o clique pedindo um campo que não deveria ser
            // preenchido para desligar nada.
            <button className="ghost" type="submit" formNoValidate formAction={desligarRavi}>
              Desligar
            </button>
          ) : null}
        </div>
      </form>

      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}
