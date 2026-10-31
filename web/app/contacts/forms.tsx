"use client";

import { useActionState } from "react";

import type { Company, Contact } from "@/lib/api";

import { apagarContato, criarContato, descadastrarContato, salvarContato } from "../actions";
import MarcaDeHidratacao from "../hydrated";

function Campos({ contato, contas }: { contato?: Contact; contas: Company[] }) {
  return (
    <>
      <div className="dupla">
        <label>
          Nome
          <input name="full_name" defaultValue={contato?.full_name ?? ""} required />
        </label>
        <label>
          Email
          <input
            name="email"
            type="email"
            defaultValue={contato?.email ?? ""}
            placeholder="alice@empresa.com"
          />
        </label>
      </div>
      <div className="dupla">
        <label>
          Cargo
          <input name="title" defaultValue={contato?.title ?? ""} placeholder="CFO" />
        </label>
        <label>
          Telefone
          <input name="phone" defaultValue={contato?.phone ?? ""} />
        </label>
      </div>
      <div className="dupla">
        <label>
          Conta
          <select name="company_id" defaultValue={contato?.company_id ?? ""}>
            <option value="">— sem conta —</option>
            {contas.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Persona
          <input name="persona" defaultValue={contato?.persona ?? ""} placeholder="decisor" />
        </label>
      </div>
    </>
  );
}

export function NovoContato({ contas }: { contas: Company[] }) {
  const [resultado, criar, criando] = useActionState(criarContato, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Novo contato</h3>
      <p className="ajuda">
        Para a pessoa que chegou por fora da lista. Email repetido é recusado: o mesmo
        lead entrando duas vezes na campanha é o que faz a empresa parecer um robô.
      </p>
      <form action={criar} className="campos">
        <Campos contas={contas} />
        <button type="submit" disabled={criando}>
          {criando ? "Criando…" : "Criar contato"}
        </button>
      </form>
      {resultado ? <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p> : null}
    </section>
  );
}

export function EditarContato({
  contato,
  contas,
}: {
  contato: Contact;
  contas: Company[];
}) {
  const [resultado, salvar, salvando] = useActionState(salvarContato, null);
  const [apagado, apagar, apagando] = useActionState(apagarContato, null);

  return (
    <details className="dobra">
      <MarcaDeHidratacao />
      <summary>Editar</summary>
      <form action={salvar} className="campos">
        <input type="hidden" name="id" value={contato.id} />
        <Campos contato={contato} contas={contas} />
        <button type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar"}
        </button>
      </form>
      {resultado ? <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p> : null}

      {/* Apagar só funciona para quem nunca foi usado: a API recusa remover
          contato com histórico, e isso é proteção — apagar levaria embora a
          prova de um descadastro, e o próximo import traria a pessoa de volta. */}
      <form action={apagar} className="inline">
        <input type="hidden" name="id" value={contato.id} />
        <button type="submit" className="ghost" disabled={apagando}>
          {apagando ? "Apagando…" : "Apagar (só se nunca foi usado)"}
        </button>
      </form>
      {apagado ? <p className={apagado.ok ? "ok" : "erro"}>{apagado.message}</p> : null}
    </details>
  );
}

export function RegistrarDescadastro({ contato }: { contato: Contact }) {
  const [resultado, registrar, registrando] = useActionState(descadastrarContato, null);

  // Sem desfazer, de propósito: a API não desmarca descadastro, e um botão que
  // sempre dá erro é pior do que nenhum botão.
  if (resultado?.ok) return <span className="tag urgente">descadastrado</span>;

  return (
    <>
      <MarcaDeHidratacao />
      <form action={registrar} className="inline">
        <input type="hidden" name="id" value={contato.id} />
        <button type="submit" className="ghost" disabled={registrando}>
          {registrando ? "Registrando…" : "Registrar descadastro"}
        </button>
      </form>
      {resultado && !resultado.ok ? <p className="erro">{resultado.message}</p> : null}
    </>
  );
}
