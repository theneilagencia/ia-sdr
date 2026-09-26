"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../hydrated";

import { apagarDocumento, colarDocumento, subirDocumento } from "../actions";

export function Subir() {
  const [resultado, enviar, enviando] = useActionState(subirDocumento, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Subir arquivo</h3>
      <p className="ajuda">
        Texto, Markdown, CSV, JSON ou YAML. PDF e Word precisam ser convertidos antes —
        se você mandar um, a tela diz isso em vez de aceitar um arquivo vazio.
      </p>
      <form action={enviar} className="campos">
        <div className="dupla">
          <label>
            Arquivo
            <input type="file" name="file" accept=".txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.log" />
          </label>
          <label>
            Título (opcional)
            <input name="title" placeholder="usa o nome do arquivo" />
          </label>
        </div>
        <button className="primary" type="submit" disabled={enviando}>
          {enviando ? "Indexando…" : "Subir e indexar"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function Colar() {
  const [resultado, enviar, enviando] = useActionState(colarDocumento, null);

  return (
    <section className="card">
      <h3>Colar texto</h3>
      <p className="ajuda">
        O caminho mais curto para a base sair do zero: cole o playbook, a lista de
        perguntas frequentes ou a descrição de um produto.
      </p>
      <form action={enviar} className="campos">
        <label>
          Título
          <input name="title" placeholder="Playbook de vendas" required />
        </label>
        <label>
          Texto
          <textarea name="content" rows={8} required />
        </label>
        <button type="submit" disabled={enviando}>
          {enviando ? "Indexando…" : "Indexar"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function Apagar({ id, titulo }: { id: string; titulo: string }) {
  return (
    <form action={apagarDocumento} className="inline">
      <input type="hidden" name="id" value={id} />
      <button className="ghost" type="submit" aria-label={`Apagar ${titulo}`}>
        Apagar
      </button>
    </form>
  );
}
