"use client";

import { useActionState } from "react";

import type { SendingPolicy } from "@/lib/api";

import { salvarVolume } from "../actions";

/**
 * Limites de volume.
 *
 * Os padrões são conservadores de propósito: provedor de email que vê volume
 * novo e alto trata como spam, e recuperar reputação custa muito mais do que
 * subir devagar.
 */
export default function SendingForm({ politica }: { politica: SendingPolicy }) {
  const [salvo, salvar, salvando] = useActionState(salvarVolume, null);

  return (
    <section className="card">
      <h3>Volume de envio</h3>
      <p className="ajuda">
        Quantos emails por dia esta empresa pode disparar. Começar baixo e subir aos poucos é
        o que protege a reputação do domínio.
      </p>

      <form action={salvar} className="campos">
        <div className="dupla">
          <label>
            Máximo por dia
            <input
              name="daily_limit"
              type="number"
              min={1}
              max={2000}
              defaultValue={politica.daily_limit}
            />
          </label>
          <label>
            Fuso horário
            <input name="timezone" defaultValue={politica.timezone} />
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            name="warmup_enabled"
            defaultChecked={politica.warmup_enabled}
          />
          Aquecer o domínio (subir o volume aos poucos)
        </label>

        <div className="dupla">
          <label>
            Começar com
            <input
              name="warmup_start"
              type="number"
              min={1}
              defaultValue={politica.warmup_start}
            />
            <small>emails no primeiro dia</small>
          </label>
          <label>
            Aumentar por dia
            <input
              name="warmup_daily_increment"
              type="number"
              min={1}
              defaultValue={politica.warmup_daily_increment}
            />
            <small>até chegar ao máximo</small>
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            name="business_hours_only"
            defaultChecked={politica.business_hours_only}
          />
          Enviar só em horário comercial
        </label>

        <div className="inline">
          <button className="primary" type="submit" disabled={salvando}>
            {salvando ? "Salvando…" : "Salvar limites"}
          </button>
        </div>
      </form>

      {salvo ? <p className={salvo.ok ? "ok" : "erro"}>{salvo.message}</p> : null}
    </section>
  );
}
