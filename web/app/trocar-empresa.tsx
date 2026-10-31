"use client";

import { useActionState } from "react";

import { trocarEmpresa } from "./actions";

/**
 * O seletor de empresa, para quem tem mais de um vínculo.
 *
 * Troca ao escolher, sem botão: um seletor que exige confirmar é um clique a
 * mais para a única coisa que ele faz. O `form` continua ali porque é o que
 * carrega o Server Action — e é do lado do servidor que o token novo é emitido
 * e trocado no cookie httpOnly.
 */
export default function TrocarEmpresa({
  atual,
  vinculos,
}: {
  atual: string;
  vinculos: { tenant_id: string; tenant_name: string; role: string }[];
}) {
  const [erro, trocar, trocando] = useActionState(trocarEmpresa, null);

  return (
    <form action={trocar} className="inline" data-secao="trocar-empresa">
      <select
        name="tenant_id"
        defaultValue={atual}
        disabled={trocando}
        aria-label="Empresa ativa"
        onChange={(e) => e.currentTarget.form?.requestSubmit()}
      >
        {vinculos.map((v) => (
          <option key={v.tenant_id} value={v.tenant_id}>
            {v.tenant_name} ({v.role})
          </option>
        ))}
      </select>
      {erro ? <span className="erro">{erro}</span> : null}
    </form>
  );
}
