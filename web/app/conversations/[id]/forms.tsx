"use client";

import { useActionState } from "react";

import type { ConversationDetail, Member } from "@/lib/api";

import {
  escreverResposta,
  mudarConversa,
  passarParaPessoa,
  pedirRespostaAoAgente,
} from "../../actions";
import MarcaDeHidratacao from "../../hydrated";

export function Responder({ conversa }: { conversa: ConversationDetail }) {
  const [resultado, enviar, enviando] = useActionState(escreverResposta, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Responder à mão</h3>
      <p className="ajuda">
        O texto nasce rascunho e sai pela fila de Revisão, como o do agente. Não é
        desconfiança: limite diário, aquecimento, horário de envio e descadastro estão
        todos depois da aprovação, e um texto que pulasse essa fila sairia sem freio.
      </p>
      <form action={enviar} className="campos">
        <input type="hidden" name="conversation_id" value={conversa.id} />
        <label>
          Assunto
          <input name="subject" defaultValue={conversa.subject ?? ""} />
        </label>
        <label>
          Mensagem
          <textarea name="body" rows={6} required />
        </label>
        <button className="primary" type="submit" disabled={enviando}>
          {enviando ? "Salvando…" : "Salvar como rascunho"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function PedirAoAgente({ conversa }: { conversa: ConversationDetail }) {
  const [resultado, pedir, pedindo] = useActionState(pedirRespostaAoAgente, null);

  return (
    <section className="card">
      <h3>Pedir a resposta ao agente</h3>
      <p className="ajuda">
        O agente lê a conversa e a base de conhecimento e escreve o rascunho. Quando a
        resposta não está na base, ele diz que vai confirmar e marca para um humano — em
        vez de inventar.
      </p>
      <form action={pedir}>
        <input type="hidden" name="conversation_id" value={conversa.id} />
        <input type="hidden" name="campaign_id" value={conversa.campaign_id ?? ""} />
        <button type="submit" disabled={pedindo}>
          {pedindo ? "O agente está escrevendo…" : "Pedir rascunho ao agente"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function PassarPara({
  conversa,
  membros,
}: {
  conversa: ConversationDetail;
  membros: Member[];
}) {
  const [resultado, passar, passando] = useActionState(passarParaPessoa, null);

  return (
    <form action={passar} className="inline">
      <input type="hidden" name="conversation_id" value={conversa.id} />
      <select name="handoff_to_user_id" defaultValue={conversa.handoff_to_user_id ?? ""}>
        <option value="">— ninguém: o agente cuida —</option>
        {membros.map((m) => (
          <option key={m.user_id} value={m.user_id}>
            {m.full_name} ({m.role})
          </option>
        ))}
      </select>
      <button type="submit" disabled={passando}>
        {passando ? "Passando…" : "Definir responsável"}
      </button>
      {resultado ? (
        <span className={resultado.ok ? "ok" : "erro"}>{resultado.message}</span>
      ) : null}
    </form>
  );
}

export function Fechar({ conversa }: { conversa: ConversationDetail }) {
  const [resultado, mudar, mudando] = useActionState(mudarConversa, null);
  const fechada = conversa.status === "closed";

  return (
    <form action={mudar} className="inline">
      <input type="hidden" name="conversation_id" value={conversa.id} />
      <input type="hidden" name="status" value={fechada ? "open" : "closed"} />
      <button className="ghost" type="submit" disabled={mudando}>
        {fechada ? "Reabrir conversa" : "Fechar conversa"}
      </button>
      {resultado ? (
        <span className={resultado.ok ? "ok" : "erro"}>{resultado.message}</span>
      ) : null}
    </form>
  );
}
