import { api, type AISettings, type EmailAccount, type EmailPreset, type SendingPolicy } from "@/lib/api";

import Nav from "../nav";
import AIForm from "./ai-form";
import EmailForm from "./email-form";
import SendingForm from "./sending-form";

/**
 * Tudo que uma empresa precisa configurar antes de operar, numa página só.
 *
 * As três seções são independentes: dá para conectar o email sem ter a chave
 * da IA e vice-versa, e a página diz, em cada uma, o que falta.
 */
export default async function SettingsPage() {
  const [ia, email, presets, volume] = await Promise.all([
    api<AISettings>("/api/v1/settings/ai"),
    api<EmailAccount>("/api/v1/settings/email"),
    api<EmailPreset[]>("/api/v1/settings/email/presets"),
    api<SendingPolicy>("/api/v1/settings/sending"),
  ]);

  return (
    <>
      <Nav />
      <main className="shell">
        <h1>Configurações</h1>
        <p className="lede">
          O que esta empresa precisa para operar. Chave e senha ficam cifradas no servidor e
          nunca voltam para a tela.
        </p>

        <AIForm estado={ia} />
        <EmailForm estado={email} presets={presets} />
        <SendingForm politica={volume} />
      </main>
    </>
  );
}
