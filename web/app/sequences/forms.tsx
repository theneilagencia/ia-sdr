"use client";

import { useActionState } from "react";

import type { Campaign, Enrollment, Sequence } from "@/lib/api";

import {
  alternarCadencia,
  apagarCadencia,
  avancarCadencias,
  criarCadencia,
  inscreverNaCadencia,
  pararInscricao,
  salvarCadencia,
} from "../actions";
import MarcaDeHidratacao from "../hydrated";

/** Toques existentes mais dois vazios; o teto da API é oito. */
const EXTRA = 2;
const MAX_PASSOS = 8;

type Passo = { order?: number; wait_days?: number; instruction?: string };

/**
 * Os toques de uma cadência.
 *
 * O primeiro não tem espera: é a abordagem inicial, e agendar o primeiro contato
 * para daqui a três dias só confundiria quem está montando. Os outros esperam a
 * partir do toque anterior, com mínimo de um dia — dois emails no mesmo dia, do
 * mesmo remetente, para o mesmo lead, é a definição de ser ignorado.
 */
function Passos({ existentes }: { existentes: Passo[] }) {
  const total = Math.min(existentes.length + EXTRA, MAX_PASSOS);
  return (
    <>
      {Array.from({ length: total }, (_, i) => (
        <div className="dupla" key={i}>
          <label>
            {i === 0 ? "Abordagem inicial — o que escrever" : `Toque ${i + 1} — o que escrever`}
            <textarea
              name="passo_instruction"
              rows={2}
              defaultValue={existentes[i]?.instruction ?? ""}
              placeholder={
                i === 0
                  ? "Entrar pelo gatilho da pesquisa e pedir 15 minutos"
                  : "Trazer um caso parecido e repetir o pedido, mais curto"
              }
            />
          </label>
          <label>
            {i === 0 ? (
              "Espera"
            ) : (
              <span className="repetido">Dias de espera do toque {i + 1}</span>
            )}
            {i === 0 ? (
              <input value="sai na hora" disabled />
            ) : (
              <input
                name="passo_wait_days"
                type="number"
                min={1}
                max={90}
                defaultValue={existentes[i]?.wait_days ?? 3}
              />
            )}
          </label>
        </div>
      ))}
    </>
  );
}

export function NovaCadencia({ campanhas }: { campanhas: Campaign[] }) {
  const [resultado, criar, criando] = useActionState(criarCadencia, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Nova cadência</h3>
      <p className="ajuda">
        Escreva o que cada toque deve dizer — não o texto final. É o agente de abordagem que
        escreve, lendo a pesquisa da conta, o Cérebro e o que já foi mandado antes, para o
        segundo email não repetir o primeiro. Até {MAX_PASSOS} toques: mais que isso não é
        persistência, é spam.
      </p>
      {campanhas.length === 0 ? (
        <p className="empty">Crie uma campanha antes: a cadência pertence a uma.</p>
      ) : (
        <form action={criar} className="campos">
          <div className="dupla">
            <label>
              Nome
              <input name="name" placeholder="Mineração — 3 toques" required />
            </label>
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
          </div>
          <Passos existentes={[]} />
          <button className="primary" type="submit" disabled={criando}>
            {criando ? "Criando…" : "Criar cadência"}
          </button>
        </form>
      )}
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function Editar({ cadencia }: { cadencia: Sequence }) {
  const [resultado, salvar, salvando] = useActionState(salvarCadencia, null);

  return (
    <details className="dobra">
      <summary>Editar os toques</summary>
      <form action={salvar} className="campos">
        <input type="hidden" name="sequence_id" value={cadencia.id} />
        <label>
          Nome
          <input name="name" defaultValue={cadencia.name} required />
        </label>
        <Passos existentes={cadencia.steps} />
        <p className="ajuda">
          Editar vale para quem ainda não chegou nestes toques: quem já recebeu o toque 2 não
          recebe o novo toque 2. Apagar o texto de um toque remove o toque.
        </p>
        <button className="primary" type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar toques"}
        </button>
        {resultado ? (
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
        ) : null}
      </form>
    </details>
  );
}

export function Alternar({ cadencia }: { cadencia: Sequence }) {
  const [resultado, alternar, alternando] = useActionState(alternarCadencia, null);

  return (
    <form action={alternar} className="inline">
      <input type="hidden" name="sequence_id" value={cadencia.id} />
      <input type="hidden" name="is_active" value={cadencia.is_active ? "false" : "true"} />
      <button
        className={cadencia.is_active ? "ghost" : "primary"}
        type="submit"
        disabled={alternando}
      >
        {cadencia.is_active ? "Desativar" : "Ativar"}
      </button>
      {resultado ? (
        <span className={resultado.ok ? "ok" : "erro"}>{resultado.message}</span>
      ) : null}
    </form>
  );
}

export function Apagar({ cadencia }: { cadencia: Sequence }) {
  const [resultado, apagar, apagando] = useActionState(apagarCadencia, null);

  return (
    <form action={apagar} className="inline">
      <input type="hidden" name="sequence_id" value={cadencia.id} />
      <button className="ghost" type="submit" disabled={apagando}>
        Apagar
      </button>
      {resultado ? (
        <span className={resultado.ok ? "ok" : "erro"}>{resultado.message}</span>
      ) : null}
    </form>
  );
}

export function Inscrever({
  cadencia,
  candidatos,
}: {
  cadencia: Sequence;
  candidatos: { id: string; rotulo: string }[];
}) {
  const [resultado, inscrever, inscrevendo] = useActionState(inscreverNaCadencia, null);

  return (
    <details className="dobra">
      <summary>
        Inscrever prospects ({candidatos.length} fora dela)
        {cadencia.is_active ? null : " — ative a cadência primeiro"}
      </summary>
      {/* A API recusa entrada em cadência desativada, e está certa: prospect
          dentro de uma cadência que não anda é gente esperando um toque que
          ninguém vai mandar. A tela diz isso em vez de oferecer um botão que
          sempre devolve erro. */}
      {!cadencia.is_active ? (
        <p className="empty">
          Cadência desativada. Ative acima e depois inscreva — ninguém entra numa cadência
          que não anda.
        </p>
      ) : candidatos.length === 0 ? (
        <p className="empty">
          Todos os prospects desta campanha já passaram por aqui. Importe uma lista para
          alimentar a cadência.
        </p>
      ) : (
        <form action={inscrever} className="campos">
          <input type="hidden" name="sequence_id" value={cadencia.id} />
          <p className="ajuda">
            Quem entra recebe o primeiro toque no próximo avanço. Quem já respondeu, pediu
            descadastro, marcou reunião ou foi qualificado não entra — e a resposta diz quem
            ficou de fora e por quê.
          </p>
          <div className="opcoes">
            {candidatos.map((c) => (
              <label className="opcao" key={c.id}>
                <input type="checkbox" name="prospect_ids" value={c.id} />
                {c.rotulo}
              </label>
            ))}
          </div>
          <button className="primary" type="submit" disabled={inscrevendo}>
            {inscrevendo ? "Inscrevendo…" : "Inscrever os marcados"}
          </button>
        </form>
      )}
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </details>
  );
}

export function Parar({ inscricao }: { inscricao: Enrollment }) {
  const [resultado, parar, parando] = useActionState(pararInscricao, null);

  return (
    <form action={parar} className="inline">
      <input type="hidden" name="enrollment_id" value={inscricao.id} />
      <button className="ghost" type="submit" disabled={parando}>
        Tirar
      </button>
      {resultado && !resultado.ok ? <span className="erro">{resultado.message}</span> : null}
    </form>
  );
}

export function Avancar() {
  const [resultado, avancar, avancando] = useActionState(avancarCadencias, null);

  return (
    <div className="cota">
      <strong>Avançar agora</strong>
      <span>
        {" "}
        · o worker faz isso sozinho a cada ciclo. Aqui serve para não esperar o relógio
        enquanto se monta a cadência.
      </span>
      <div className="acao-caixa">
        <form action={avancar}>
          <button type="submit" disabled={avancando}>
            {avancando ? "Avançando…" : "Avançar cadências"}
          </button>
        </form>
        {resultado ? (
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
        ) : null}
      </div>
    </div>
  );
}
