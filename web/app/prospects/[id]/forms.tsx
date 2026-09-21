"use client";

import { useActionState } from "react";

import {
  dispararNoProspect,
  enviarParaRavi,
  marcarReuniao,
  registrarDescadastro,
  registrarRespostaRecebida,
} from "../../actions";
import MarcaDeHidratacao from "../../hydrated";

/** Os três agentes cujo alvo é este prospect, na ordem em que o funil anda. */
const AGENTES = [
  { kind: "research", rotulo: "Pesquisar a conta" },
  { kind: "outreach", rotulo: "Escrever a abordagem" },
  { kind: "qualification", rotulo: "Qualificar" },
];

export function MandarAgente({
  prospectId,
  companyId,
  contactId,
  campaignId,
}: {
  prospectId: string;
  companyId: string | null;
  contactId: string;
  campaignId: string | null;
}) {
  const [resultado, disparar, disparando] = useActionState(dispararNoProspect, null);

  return (
    <section className="card">
      <h3>Mandar um agente trabalhar</h3>
      <p className="ajuda">
        A ordem importa: a abordagem precisa da pesquisa, e a qualificação precisa dos
        critérios da campanha. Quando falta algo, o agente recusa e diz o que falta em vez
        de improvisar.
      </p>
      <form action={disparar} className="inline">
        <input type="hidden" name="prospect_id" value={prospectId} />
        <input type="hidden" name="company_id" value={companyId ?? ""} />
        <input type="hidden" name="contact_id" value={contactId} />
        <input type="hidden" name="campaign_id" value={campaignId ?? ""} />
        {AGENTES.map((a) => (
          <button key={a.kind} name="agent" value={a.kind} type="submit" disabled={disparando}>
            {a.rotulo}
          </button>
        ))}
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function MarcarReuniao({ prospectId }: { prospectId: string }) {
  const [resultado, marcar, marcando] = useActionState(marcarReuniao, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Marcar reunião</h3>
      <p className="ajuda">
        A conversão que a plataforma existe para produzir. A integração de calendário
        ainda não existe; marcar aqui move o prospect para &ldquo;reunião marcada&rdquo; e
        entra no funil da tela inicial.
      </p>
      <form action={marcar} className="campos">
        <input type="hidden" name="prospect_id" value={prospectId} />
        <div className="dupla">
          <label>
            Quando
            <input type="datetime-local" name="scheduled_at" required />
          </label>
          <label>
            Duração (minutos)
            <input type="number" name="duration_minutes" defaultValue={30} min={5} max={480} />
          </label>
        </div>
        <label>
          Onde
          <input name="location" placeholder="Google Meet, telefone, escritório" />
        </label>
        <label>
          Notas
          <textarea name="notes" rows={2} />
        </label>
        <button className="primary" type="submit" disabled={marcando}>
          {marcando ? "Marcando…" : "Marcar reunião"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function RegistrarResposta({ prospectId }: { prospectId: string }) {
  const [resultado, registrar, registrando] = useActionState(registrarRespostaRecebida, null);

  return (
    <section className="card">
      <h3>Registrar resposta recebida fora do email</h3>
      <p className="ajuda">
        O caminho normal é a plataforma buscar as respostas na caixa de entrada. Isto é
        para quando a pessoa respondeu por outro canal — telefone, WhatsApp, um encontro —
        e o agente ficaria sem saber o que ela disse.
      </p>
      <form action={registrar} className="campos">
        <input type="hidden" name="prospect_id" value={prospectId} />
        <label>
          Assunto
          <input name="subject" />
        </label>
        <label>
          O que a pessoa disse
          <textarea name="body" rows={4} required />
        </label>
        <button type="submit" disabled={registrando}>
          {registrando ? "Registrando…" : "Registrar"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function SubirParaRavi({ prospectId }: { prospectId: string }) {
  const [resultado, enviar, enviando] = useActionState(enviarParaRavi, null);

  return (
    <section className="card">
      <h3>Enviar para o RAVI</h3>
      <p className="ajuda">
        O lead vive no CRM; aqui é o motor. O envio faz upsert por email ou telefone do
        lado do RAVI, então mandar duas vezes não cria dois leads.
      </p>
      <form action={enviar}>
        <input type="hidden" name="prospect_id" value={prospectId} />
        <button type="submit" disabled={enviando}>
          {enviando ? "Enviando…" : "Enviar agora"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function Descadastrar({
  prospectId,
  contactId,
  nome,
  jaDescadastrado,
}: {
  prospectId: string;
  contactId: string;
  nome: string;
  jaDescadastrado: boolean;
}) {
  const [resultado, registrar, registrando] = useActionState(registrarDescadastro, null);

  return (
    <section className="card">
      <h3>Descadastro</h3>
      {jaDescadastrado ? (
        // O cartão fica, no estado registrado. Sumir com ele levaria embora a
        // confirmação da ação que acabou de acontecer — a pessoa apertaria o
        // botão, o botão desapareceria e ninguém diria que deu certo.
        <p className="ajuda">
          {nome} pediu para não receber mais contato, e isto vale no ato: nenhum agente
          escreve, e a plataforma recusa qualquer tentativa. Não há desfazer nesta tela de
          propósito.
        </p>
      ) : (
        <>
          <p className="ajuda">
            Para quando {nome} pede para não receber mais contato. Vale no ato e não tem
            desfazer nesta tela — de propósito: quem pediu para sair não volta para a
            lista por um clique errado.
          </p>
          <form action={registrar}>
            <input type="hidden" name="prospect_id" value={prospectId} />
            <input type="hidden" name="contact_id" value={contactId} />
            <button className="ghost" type="submit" disabled={registrando}>
              {registrando ? "Registrando…" : "Pediu para não receber mais"}
            </button>
          </form>
        </>
      )}
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}
