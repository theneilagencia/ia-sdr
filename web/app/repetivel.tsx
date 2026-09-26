"use client";

/** Linhas existentes mais duas vazias: quem precisa de mais salva e ganha duas. */
const EXTRA = 2;

export type CampoRepetido = { nome: string; rotulo: string; largo?: boolean };

/**
 * Lista de campos que se repete, sem botão "adicionar" e sem estado no cliente.
 *
 * Renderiza o que já existe mais duas linhas vazias. Linha vazia não entra no
 * envio — quem precisa de mais salva e ganha duas novas. Menos código do que um
 * repetidor dinâmico, e nada para dessincronizar entre o que a tela mostra e o
 * que o servidor recebe.
 */
export default function Repetivel({
  prefixo,
  linhas,
  campos,
}: {
  prefixo: string;
  linhas: Record<string, string | undefined>[];
  campos: CampoRepetido[];
}) {
  const total = linhas.length + EXTRA;
  return (
    <>
      {Array.from({ length: total }, (_, i) => (
        <div className="dupla" key={i}>
          {campos.map((campo) => (
            <label key={campo.nome}>
              {i === 0 ? campo.rotulo : <span className="repetido">{campo.rotulo}</span>}
              {campo.largo ? (
                <textarea
                  name={`${prefixo}_${campo.nome}`}
                  rows={2}
                  defaultValue={linhas[i]?.[campo.nome] ?? ""}
                />
              ) : (
                <input
                  name={`${prefixo}_${campo.nome}`}
                  defaultValue={linhas[i]?.[campo.nome] ?? ""}
                />
              )}
            </label>
          ))}
        </div>
      ))}
    </>
  );
}
