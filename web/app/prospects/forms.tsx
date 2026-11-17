"use client";

import { useActionState } from "react";

import type { Campaign } from "@/lib/api";

import { importarLista } from "../actions";
import MarcaDeHidratacao from "../hydrated";

/**
 * Import de lista em CSV.
 *
 * Quem monta lista exporta do Sales Navigator, do Apollo ou de uma planilha, e
 * cada ferramenta chama as colunas de um jeito. A API aceita os apelidos
 * conhecidos em português e inglês; aqui só dizemos isso, para ninguém achar que
 * precisa renomear colunas antes de tentar.
 */
export function Importar({ campanhas }: { campanhas: Campaign[] }) {
  const [resultado, importar, importando] = useActionState(importarLista, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Importar lista</h3>
      <p className="ajuda">
        CSV exportado de onde você já trabalha. Precisa de uma coluna com o nome da
        empresa e outra com o nome da pessoa; email, cargo, setor, país e LinkedIn entram
        se estiverem lá. Ponto e vírgula e acento resolvidos — não renomeie nada antes de
        tentar. Linha que não entrar volta com o número dela.
      </p>
      {campanhas.length === 0 ? (
        <p className="empty">
          Crie uma campanha antes: prospect sempre pertence a uma, e é ela que diz aos
          agentes contra qual ICP medir.
        </p>
      ) : (
        <form action={importar} className="campos">
          <div className="dupla">
            <label>
              Campanha
              <select name="campaign_id" defaultValue="" required>
                <option value="">— escolha —</option>
                {campanhas.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Arquivo
              <input type="file" name="file" accept=".csv,.tsv,.txt" required />
            </label>
          </div>
          <label>
            De onde veio a lista (opcional)
            <input name="source" placeholder="sales navigator, indicação, evento" />
          </label>
          <button className="primary" type="submit" disabled={importando}>
            {importando ? "Importando…" : "Importar"}
          </button>
        </form>
      )}
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}
