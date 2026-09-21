"use client";

import { useActionState, useState } from "react";

import type { EmailAccount, EmailPreset } from "@/lib/api";

import { salvarEmail, testarEmail } from "../actions";

/**
 * Configuração da conta de envio.
 *
 * O provedor escolhido preenche servidor e porta sozinho e troca a instrução
 * que aparece embaixo — é ali que mora o motivo mais comum de falha, a senha
 * de app. Servidor e porta só aparecem quando a pessoa escolhe "outro".
 */
export default function EmailForm({
  estado,
  presets,
}: {
  estado: EmailAccount;
  presets: EmailPreset[];
}) {
  const [provider, setProvider] = useState(estado.provider ?? "gmail");
  const [teste, testar, testando] = useActionState(testarEmail, null);
  const [salvo, salvar, salvando] = useActionState(salvarEmail, null);
  const resultado = salvo ?? teste;
  const preset = presets.find((p) => p.provider === provider) ?? presets[0];

  return (
    <section className="card">
      <h3>Email de envio</h3>
      <p className="ajuda">
        O endereço de onde as abordagens saem. Vale usar um domínio secundário, não o email
        principal da empresa: se a reputação desse endereço cair, o email do dia a dia
        continua funcionando.
      </p>

      <p className="estado">
        {estado.configured ? (
          <>
            <span className="ok">● Conectado</span> — {estado.from_email} via {estado.host}
          </>
        ) : (
          <span className="aviso">● Nenhuma conta conectada</span>
        )}
      </p>

      <form action={salvar} className="campos">
        <label>
          Provedor
          <div className="opcoes">
            {presets.map((p) => (
              <label key={p.provider} className="opcao">
                <input
                  type="radio"
                  name="provider"
                  value={p.provider}
                  checked={provider === p.provider}
                  onChange={() => setProvider(p.provider)}
                />
                {p.label}
              </label>
            ))}
          </div>
          <small>{preset?.help}</small>
        </label>

        <label>
          Email de envio
          <input
            name="from_email"
            type="email"
            defaultValue={estado.from_email ?? ""}
            placeholder="vendas@suaempresa.com"
            required
          />
        </label>

        <label>
          Nome que aparece para quem recebe
          <input
            name="from_name"
            defaultValue={estado.from_name ?? ""}
            placeholder="Vendas — Sua Empresa"
          />
        </label>

        <label>
          Senha
          <input name="password" type="password" autoComplete="off" required />
          <small>
            No Gmail e no Outlook com verificação em duas etapas, use uma senha de app — a
            senha normal da conta é recusada.
          </small>
        </label>

        {provider === "smtp" ? (
          <div className="dupla">
            <label>
              Servidor de envio
              <input name="host" defaultValue={estado.host ?? ""} placeholder="smtp.seudominio.com" />
            </label>
            <label>
              Porta
              <input name="port" type="number" defaultValue={estado.port ?? 587} />
            </label>
          </div>
        ) : null}

        <div className="inline">
          <button className="primary" type="submit" disabled={salvando}>
            {salvando ? "Conectando…" : "Conectar"}
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
