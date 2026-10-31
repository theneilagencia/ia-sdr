import {
  api,
  type AISettings,
  type CrmSettings,
  type EmailAccount,
  type EmailPreset,
  type Me,
  type RetentionPolicy,
  type RetentionPreview,
  type SendingPolicy,
} from "@/lib/api";

import Nav from "../nav";
import AIForm from "./ai-form";
import Exportar from "./exportar";
import CrmForm from "./crm-form";
import EmailForm from "./email-form";
import RetentionForm from "./retention-form";
import SendingForm from "./sending-form";

/**
 * Tudo que uma empresa precisa configurar antes de operar, numa página só.
 *
 * As seções são independentes: dá para conectar o email sem ter a chave da IA e
 * vice-versa, e a página diz, em cada uma, o que falta. O CRM é o único
 * realmente opcional — sem ele a plataforma funciona inteira, só não empurra o
 * lead para o RAVI.
 */
export default async function SettingsPage() {
  const [ia, email, presets, volume, crm, eu, retencao] = await Promise.all([
    api<AISettings>("/api/v1/settings/ai"),
    api<EmailAccount>("/api/v1/settings/email"),
    api<EmailPreset[]>("/api/v1/settings/email/presets"),
    api<SendingPolicy>("/api/v1/settings/sending"),
    api<CrmSettings>("/api/v1/settings/crm"),
    api<Me>("/api/v1/auth/me"),
    api<RetentionPolicy>("/api/v1/settings/retention"),
  ]);
  // A previsão só é buscada quando há prazo: sem prazo não há o que prever, e a
  // consulta varre tabela grande para devolver zeros.
  const previsao =
    retencao.days > 0
      ? await api<RetentionPreview>("/api/v1/settings/retention/preview")
      : null;

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
        <CrmForm estado={crm} />
        <RetentionForm politica={retencao} previsao={previsao} />
        <Exportar papel={eu.role} />
      </main>
    </>
  );
}
