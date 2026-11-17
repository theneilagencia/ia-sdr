"use client";

import { useActionState } from "react";

import type { Campaign } from "@/lib/api";

import { criarCampanha, mudarStatusCampanha, salvarCampanha } from "../actions";
import MarcaDeHidratacao from "../hydrated";
import Repetivel from "../repetivel";

/**
 * Valor de dicionário livre virando texto de campo.
 *
 * A API aceita qualquer JSON nestes campos, e uma campanha criada por API pode
 * ter lista (`{"geografia": ["CA"]}`) ou objeto no valor. Um `defaultValue` que
 * recebe lista renderiza os itens colados, e objeto vira "[object Object]" — e o
 * salvamento gravaria isso de volta. A conversão é explícita: lista vira lista
 * separada por vírgula, estrutura mais funda vira o JSON dela, e o que já era
 * texto fica igual. Salvar depois disso grava texto, e o agente lê texto — o que
 * ele receberia de qualquer forma, já que o contexto vai como JSON serializado.
 */
function comoTexto(valor: unknown): string {
  if (valor === null || valor === undefined) return "";
  if (typeof valor === "string") return valor;
  if (typeof valor === "number" || typeof valor === "boolean") return String(valor);
  if (Array.isArray(valor) && valor.every((v) => typeof v !== "object"))
    return valor.join(", ");
  return JSON.stringify(valor);
}

/** Um dicionário guardado vira as linhas que a tela mostra de volta. */
function paraLinhas(dicionario: Record<string, unknown>) {
  return Object.entries(dicionario).map(([chave, valor]) => ({
    chave,
    valor: comoTexto(valor),
  }));
}

export function NovaCampanha() {
  const [resultado, criar, criando] = useActionState(criarCampanha, null);

  return (
    <section className="card">
      <MarcaDeHidratacao />
      <h3>Nova campanha</h3>
      <p className="ajuda">
        Comece pelo nome e pelo objetivo. Ela nasce como rascunho: o que os agentes leem
        você preenche abaixo, e só depois ativa.
      </p>
      <form action={criar} className="campos">
        <div className="dupla">
          <label>
            Nome
            <input name="name" placeholder="Mineração — médio porte — 2026" required />
          </label>
          <label>
            Onde (separado por vírgula)
            <input name="target_geography" placeholder="Brasil, Chile" />
          </label>
        </div>
        <label>
          Objetivo
          <textarea
            name="objective"
            rows={2}
            placeholder="Agendar 15 reuniões com diretores de operação até o fim do trimestre"
          />
        </label>
        <button className="primary" type="submit" disabled={criando}>
          {criando ? "Criando…" : "Criar campanha"}
        </button>
      </form>
      {resultado ? (
        <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
      ) : null}
    </section>
  );
}

export function MudarStatus({ campanha }: { campanha: Campaign }) {
  const [resultado, mudar, mudando] = useActionState(mudarStatusCampanha, null);
  const ativa = campanha.status === "active";

  return (
    <form action={mudar} className="inline">
      <input type="hidden" name="id" value={campanha.id} />
      <input type="hidden" name="status" value={ativa ? "paused" : "active"} />
      <button className={ativa ? "ghost" : "primary"} type="submit" disabled={mudando}>
        {ativa ? "Pausar" : "Ativar"}
      </button>
      {resultado && !resultado.ok ? <span className="erro">{resultado.message}</span> : null}
    </form>
  );
}

/**
 * O conteúdo da campanha que chega ao contexto dos agentes.
 *
 * Nenhum campo aqui é decorativo: o ICP entra na pesquisa, a oferta e a
 * mensagem na abordagem, e os critérios são a única coisa contra a qual o
 * agente de qualificação consegue julgar — sem eles ele se recusa a rodar, e
 * está certo.
 */
export function EditarCampanha({ campanha }: { campanha: Campaign }) {
  const [resultado, salvar, salvando] = useActionState(salvarCampanha, null);

  return (
    <details className="dobra">
      <summary>O que os agentes leem nesta campanha</summary>
      <form action={salvar} className="campos">
        <input type="hidden" name="id" value={campanha.id} />

        <div className="dupla">
          <label>
            Objetivo
            <textarea name="objective" rows={2} defaultValue={campanha.objective ?? ""} />
          </label>
          <label>
            Onde (separado por vírgula)
            <input
              name="target_geography"
              defaultValue={campanha.target_geography.join(", ")}
            />
          </label>
        </div>

        <h4>Perfil de cliente ideal</h4>
        <p className="ajuda">
          Característica e valor — setor, porte, país, o que faz uma conta servir. O agente
          de pesquisa cruza isto com o que encontra e dá a nota de aderência.
        </p>
        <Repetivel
          prefixo="icp"
          linhas={paraLinhas(campanha.icp)}
          campos={[
            { nome: "chave", rotulo: "Característica" },
            { nome: "valor", rotulo: "Valor", largo: true },
          ]}
        />

        <h4>Personas</h4>
        <p className="ajuda">Com quem falar, e qual dor dessa pessoa a oferta resolve.</p>
        <Repetivel
          prefixo="persona"
          linhas={campanha.personas.map((pessoa) => ({
            cargo: comoTexto(pessoa.cargo),
            dor: comoTexto(pessoa.dor),
          }))}
          campos={[
            { nome: "cargo", rotulo: "Cargo" },
            { nome: "dor", rotulo: "Dor", largo: true },
          ]}
        />

        <h4>Oferta</h4>
        <p className="ajuda">
          Lida pelo agente de abordagem. Quando está vazia, ele cai nos produtos do
          Cérebro — o que serve, mas fala da empresa toda em vez desta campanha.
        </p>
        <label>
          O que vendemos aqui
          <input
            name="o_que_vendemos"
            defaultValue={comoTexto(campanha.offer?.o_que_vendemos)}
            placeholder="Módulo de manutenção preditiva"
          />
        </label>
        <div className="dupla">
          <label>
            Resultado esperado
            <input
              name="resultado_esperado"
              defaultValue={comoTexto(campanha.offer?.resultado_esperado)}
              placeholder="12% menos parada não planejada em seis meses"
            />
          </label>
          <label>
            Prova
            <input
              name="prova"
              defaultValue={comoTexto(campanha.offer?.prova)}
              placeholder="Caso da mineradora X, auditado"
            />
          </label>
        </div>

        <h4>Mensagem</h4>
        <p className="ajuda">Como esta campanha fala. Lida pelo agente de abordagem.</p>
        <div className="dupla">
          <label>
            Ângulo
            <input
              name="angulo"
              defaultValue={comoTexto(campanha.messaging?.angulo)}
              placeholder="parada de equipamento custa mais que o software"
            />
          </label>
          <label>
            Gancho
            <input
              name="gancho"
              defaultValue={comoTexto(campanha.messaging?.gancho)}
              placeholder="entrar pelo indicador que a conta publicou"
            />
          </label>
        </div>
        <div className="dupla">
          <label>
            O que pedir
            <input
              name="chamada_para_acao"
              defaultValue={comoTexto(campanha.messaging?.chamada_para_acao)}
              placeholder="15 minutos na próxima semana"
            />
          </label>
          <label>
            Não usar
            <input
              name="evitar"
              defaultValue={comoTexto(campanha.messaging?.evitar)}
              placeholder="desconto, urgência falsa"
            />
          </label>
        </div>

        <h4>Critérios de qualificação</h4>
        <p className="ajuda">
          Critério e o que conta como atendido. O agente de qualificação devolve veredito
          com evidência, critério a critério — e se não houver nenhum critério aqui, ele se
          recusa a julgar. Se você usar <strong>orçamento</strong>,{" "}
          <strong>autoridade</strong>, <strong>necessidade</strong> e <strong>prazo</strong>,
          o RAVI recebe o BANT já preenchido.
        </p>
        <Repetivel
          prefixo="criterio"
          linhas={paraLinhas(campanha.qualification_criteria)}
          campos={[
            { nome: "chave", rotulo: "Critério" },
            { nome: "valor", rotulo: "O que conta como atendido", largo: true },
          ]}
        />

        <button className="primary" type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar"}
        </button>
        {resultado ? (
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
        ) : null}
      </form>
    </details>
  );
}
