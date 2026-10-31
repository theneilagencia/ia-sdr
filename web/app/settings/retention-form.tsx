"use client";

import { useActionState } from "react";

import type { RetentionPolicy, RetentionPreview } from "@/lib/api";

import { salvarRetencao } from "../actions";
import MarcaDeHidratacao from "../hydrated";

/** O nome de cada classe em português de gente, e a ordem em que assusta menos. */
const CLASSES: { chave: string; rotulo: string }[] = [
  { chave: "jobs", rotulo: "trabalhos já concluídos" },
  { chave: "agent_runs", rotulo: "execuções de agente" },
  { chave: "messages", rotulo: "mensagens já enviadas ou recusadas" },
  { chave: "usage_events", rotulo: "registros de consumo já faturados" },
  { chave: "audit_logs", rotulo: "linhas de auditoria" },
  { chave: "prospects", rotulo: "leads que nunca responderam" },
  { chave: "contacts", rotulo: "contatos que ficariam sem lead" },
];

/**
 * Prazo de retenção.
 *
 * A tela tem duas obrigações que valem mais do que o formulário em si. A
 * primeira é dizer **o que nunca é apagado**: quem lê "descartar depois de 90
 * dias" imagina o pior, e o que tranquiliza é a lista do que fica. A segunda é
 * mostrar o número antes — apagar não tem desfazer, e "5.312 mensagens" na tela
 * é o que faz alguém reler o prazo antes de salvar.
 */
export default function RetentionForm({
  politica,
  previsao,
}: {
  politica: RetentionPolicy;
  previsao: RetentionPreview | null;
}) {
  const [resultado, salvar, salvando] = useActionState(salvarRetencao, null);
  const ligada = politica.days > 0;
  const linhas = CLASSES.filter((c) => (previsao?.counts?.[c.chave] ?? 0) > 0);
  const total = Object.values(previsao?.counts ?? {}).reduce((a, b) => a + b, 0);

  return (
    <section className="card" data-secao="retencao">
      <MarcaDeHidratacao />
      <h3>Descarte automático</h3>
      <p className="ajuda">
        Guardar tudo para sempre é uma decisão, e não é a mais segura: dado de quem nunca
        respondeu um email não fica mais útil com o tempo, fica mais arriscado. Aqui você
        define depois de quantos dias a plataforma descarta o rastro do trabalho desta
        empresa. <strong>Zero é o padrão e significa nunca descartar.</strong>
      </p>

      <form action={salvar} className="campos">
        <div className="dupla">
          <label>
            Descartar depois de (dias)
            <input
              name="days"
              type="number"
              min={0}
              max={3650}
              step={1}
              defaultValue={politica.days}
            />
          </label>
          <div className="ajuda">
            {ligada
              ? `Hoje: ${politica.days} dias. O descarte roda uma vez por dia, em segundo plano.`
              : "Hoje: nada é descartado automaticamente."}
          </div>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            name="include_cold_prospects"
            defaultChecked={politica.include_cold_prospects}
          />
          Incluir também os leads que nunca responderam
        </label>
        <p className="ajuda">
          Lead frio é o que nunca respondeu, nunca teve reunião e nunca foi qualificado. Quem
          respondeu uma vez — mesmo há anos — nunca entra no descarte: a resposta é histórico
          comercial, não rastro. Desligado por padrão, porque lista é ativo da empresa.
        </p>

        <button className="primary" type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar prazo"}
        </button>
        {resultado ? (
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
        ) : null}
      </form>

      {/* O que sairia hoje, com o prazo que está salvo. Sem prazo, não há
          previsão: a plataforma não sugere um número para o cliente. */}
      {ligada ? (
        <div data-secao="previsao-da-retencao">
          <h4>O que sairia hoje</h4>
          {total === 0 ? (
            <p className="ok">Nada passou do prazo ainda.</p>
          ) : (
            <table>
              <tbody>
                {linhas.map((c) => (
                  <tr key={c.chave}>
                    <td>{c.rotulo}</td>
                    <td className="mono">
                      {(previsao?.counts?.[c.chave] ?? 0).toLocaleString("pt-BR")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}

      <h4>O que nunca é apagado</h4>
      <ul className="ajuda">
        <li>
          <strong>Quem pediu para não receber mais email.</strong> O registro do descadastro
          sobrevive a qualquer prazo — sem ele, o próximo import traria essa pessoa de volta
          para a lista.
        </li>
        <li>
          <strong>Consumo de mês que ainda não foi faturado</strong>, e nenhuma fatura.
        </li>
        <li>
          <strong>Auditoria dos últimos 90 dias</strong>, mesmo com prazo menor: é o registro
          de quem fez o quê, e é depois de um susto que alguém precisa dele.
        </li>
        <li>
          <strong>Rascunho à espera de revisão</strong> e mensagem aprovada à espera de envio:
          é trabalho pendente, não histórico.
        </li>
        <li>
          <strong>Base de conhecimento, Company Brain, campanhas e contas.</strong> É o acervo
          da empresa; prazo de retenção não decide o que você precisa ter.
        </li>
      </ul>
    </section>
  );
}
