"use client";

import { useActionState } from "react";

import MarcaDeHidratacao from "../hydrated";
import Repetivel from "../repetivel";

import type { CompanyBrain } from "@/lib/api";

import { salvarCerebro } from "../actions";

export default function BrainForm({ cerebro }: { cerebro: CompanyBrain }) {
  const [resultado, salvar, salvando] = useActionState(salvarCerebro, null);

  return (
    <form action={salvar}>
      <MarcaDeHidratacao />
      <section className="card">
        <h3>Identidade</h3>
        <p className="ajuda">
          O posicionamento é a frase que explica o que vocês fazem. Os três agentes leem.
        </p>
        <div className="campos">
          <div className="dupla">
            <label>
              Razão social
              <input name="legal_name" defaultValue={cerebro.legal_name ?? ""} />
            </label>
            <label>
              Site
              <input
                name="website"
                placeholder="https://"
                defaultValue={cerebro.website ?? ""}
              />
            </label>
          </div>
          <label>
            Posicionamento
            <textarea
              name="positioning"
              rows={3}
              placeholder="Software de gestão para operações de mineração de médio porte"
              defaultValue={cerebro.positioning ?? ""}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <h3>Produtos</h3>
        <p className="ajuda">
          O que vocês vendem. Lido pelo agente de pesquisa e pelo de abordagem — é o que
          ele cruza com a conta pesquisada para achar o gancho.
        </p>
        <div className="campos">
          <Repetivel
            prefixo="produto"
            linhas={cerebro.products}
            campos={[
              { nome: "nome", rotulo: "Produto" },
              { nome: "descricao", rotulo: "O que resolve", largo: true },
            ]}
          />
        </div>
      </section>

      <section className="card">
        <h3>Casos</h3>
        <p className="ajuda">
          Cliente e resultado, com número quando houver. Lido pelo agente de pesquisa; é
          a prova social que ele usa sem inventar.
        </p>
        <div className="campos">
          <Repetivel
            prefixo="caso"
            linhas={cerebro.cases}
            campos={[
              { nome: "cliente", rotulo: "Cliente ou setor" },
              { nome: "resultado", rotulo: "Resultado", largo: true },
            ]}
          />
        </div>
      </section>

      <section className="card">
        <h3>Objeções conhecidas</h3>
        <p className="ajuda">
          O que os clientes costumam responder, e o que responder de volta. Lido pelos
          agentes de abordagem e de conversa. O que não estiver aqui, o agente de conversa
          escala para um humano em vez de improvisar.
        </p>
        <div className="campos">
          <Repetivel
            prefixo="objecao"
            linhas={cerebro.objections}
            campos={[
              { nome: "objecao", rotulo: "Objeção" },
              { nome: "resposta", rotulo: "Resposta", largo: true },
            ]}
          />
        </div>
      </section>

      <section className="card">
        <h3>Tom de voz</h3>
        <p className="ajuda">
          Como a empresa escreve. Lido pelos agentes de abordagem e de conversa.
        </p>
        <div className="campos">
          <div className="dupla">
            <label>
              Tom
              <input
                name="tom"
                placeholder="direto, sem jargão"
                defaultValue={cerebro.brand_voice?.tom ?? ""}
              />
            </label>
            <label>
              Idioma
              <input
                name="idioma"
                placeholder="pt-BR"
                defaultValue={cerebro.brand_voice?.idioma ?? ""}
              />
            </label>
          </div>
          <label>
            Nunca escrever
            <input
              name="evitar"
              placeholder="bajulação, promessa de prazo, jargão de consultoria"
              defaultValue={cerebro.brand_voice?.evitar ?? ""}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <h3>Playbook</h3>
        <p className="ajuda">Lido pelo agente de abordagem.</p>
        <div className="campos">
          <label>
            Como abrir
            <textarea
              name="abertura"
              rows={2}
              placeholder="Entrar pelo gatilho encontrado na pesquisa, nunca pela empresa"
              defaultValue={cerebro.sales_playbook?.abertura ?? ""}
            />
          </label>
          <label>
            O que pedir
            <input
              name="proxima_etapa"
              placeholder="15 minutos de conversa na semana seguinte"
              defaultValue={cerebro.sales_playbook?.proxima_etapa ?? ""}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <h3>Limites da IA</h3>
        <p className="ajuda">
          Lido por todos os agentes. É o que impede um agente de criar compromisso que
          alguém vai ter que honrar — ou desfazer.
        </p>
        <div className="campos">
          <label>
            Nunca prometer
            <input
              name="nunca_prometer"
              placeholder="desconto, prazo de implantação, integração que não existe"
              defaultValue={cerebro.ai_policies?.nunca_prometer ?? ""}
            />
          </label>
          <label>
            Sempre passar para uma pessoa quando
            <input
              name="escalar_quando"
              placeholder="o assunto for contrato, jurídico ou preço"
              defaultValue={cerebro.ai_policies?.escalar_quando ?? ""}
            />
          </label>
        </div>

        <button className="primary" type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar"}
        </button>
        {resultado ? (
          <p className={resultado.ok ? "ok" : "erro"}>{resultado.message}</p>
        ) : null}
      </section>
    </form>
  );
}
