"use client";

import { useActionState } from "react";

import type { Company } from "@/lib/api";

import { criarConta, salvarConta } from "../actions";
import MarcaDeHidratacao from "../hydrated";

function Campos({ conta }: { conta?: Company }) {
  return (
    <>
      <div className="dupla">
        <label>
          Nome
          <input name="name" defaultValue={conta?.name ?? ""} required maxLength={300} />
        </label>
        <label>
          Domínio
          <input
            name="domain"
            defaultValue={conta?.domain ?? ""}
            placeholder="northernore.ca"
            maxLength={255}
          />
        </label>
      </div>
      <div className="dupla">
        <label>
          Setor
          <input name="industry" defaultValue={conta?.industry ?? ""} placeholder="mineração" />
        </label>
        <label>
          País
          <input name="country" defaultValue={conta?.country ?? ""} placeholder="CA" />
        </label>
      </div>
      <label>
        Pessoas
        <input
          name="employee_count"
          type="number"
          min={0}
          defaultValue={conta?.employee_count ?? ""}
        />
      </label>
      <label>
        O que ela faz
        <textarea name="description" rows={2} defaultValue={conta?.description ?? ""} />
      </label>
    </>
  );
}

export function NovaConta() {
  const [resultado, criar, criando] = useActionState(criarConta, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Nova conta-alvo</h3>
      <p className="ajuda">
        Serve para pesquisar uma empresa antes de saber com quem falar lá dentro. O
        prospect vem depois, quando houver a pessoa.
      </p>
      <form action={criar} className="campos">
        <Campos />
        <button type="submit" disabled={criando}>
          {criando ? "Criando…" : "Criar conta"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function EditarConta({ conta }: { conta: Company }) {
  const [resultado, salvar, salvando] = useActionState(salvarConta, null);

  return (
    <details className="dobra">
      <MarcaDeHidratacao />
      <summary>Corrigir</summary>
      <form action={salvar} className="campos">
        <input type="hidden" name="id" value={conta.id} />
        <Campos conta={conta} />
        <button type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </details>
  );
}
