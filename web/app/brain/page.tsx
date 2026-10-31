import { api, type CompanyBrain } from "@/lib/api";

import Nav from "../nav";
import BrainForm from "./brain-form";

/**
 * O Company Brain: o que a IA sabe sobre o negócio desta empresa.
 *
 * Era o centro da arquitetura e a única forma de preencher era por API. Sem
 * isto, o Outreach personaliza sem saber o que a empresa vende e o Conversation
 * escala para humano em tudo.
 */
export default async function BrainPage() {
  const cerebro = await api<CompanyBrain>("/api/v1/company-brain");

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>O que a IA sabe sobre você</h1>
        <p className="lede">
          É daqui que sai a personalização das mensagens e o que o agente pode ou não
          dizer. Cada bloco avisa qual agente o lê — o que estiver vazio simplesmente não
          entra no contexto, e o agente trabalha com menos.
        </p>
        <BrainForm cerebro={cerebro} />
      </main>
    </>
  );
}
