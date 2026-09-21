"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { api, ApiError, type Resultado } from "@/lib/api";
import { clearToken, setToken } from "@/lib/session";

type TokenResponse = { access_token: string; expires_in_minutes: number };

export async function login(_: string | null, form: FormData): Promise<string | null> {
  const email = String(form.get("email") ?? "");
  const password = String(form.get("password") ?? "");
  try {
    const token = await api<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
      requireAuth: false,
    });
    await setToken(token.access_token, token.expires_in_minutes);
  } catch (erro) {
    // Mensagem única: não confirmar se o email existe é parte do desenho.
    if (erro instanceof ApiError) return "Email ou senha inválidos";
    throw erro;
  }
  redirect("/");
}

export async function logout() {
  await clearToken();
  redirect("/login");
}

export async function approveDraft(formData: FormData) {
  const id = String(formData.get("id"));
  await api(`/api/v1/messages/${id}/approve`, { method: "POST" });
  revalidatePath("/drafts");
}

export async function rejectDraft(formData: FormData) {
  const id = String(formData.get("id"));
  const reason = String(formData.get("reason") ?? "").trim();
  await api(`/api/v1/messages/${id}/reject`, {
    method: "POST",
    body: { reason: reason || null },
  });
  revalidatePath("/drafts");
}

/**
 * As ações de configuração devolvem uma frase, não um código.
 *
 * Quem está configurando não sabe o que é 400, nem precisa: a API já manda a
 * mensagem em português, e é ela que aparece embaixo do campo.
 */
async function executar(
  chamada: () => Promise<unknown>,
  sucesso: string,
): Promise<Resultado> {
  try {
    await chamada();
    return { ok: true, message: sucesso };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function testarChaveIA(_: Resultado, form: FormData): Promise<Resultado> {
  const api_key = String(form.get("api_key") ?? "");
  try {
    const r = await api<{ ok: boolean; message: string }>("/api/v1/settings/ai/test", {
      method: "POST",
      body: { api_key },
    });
    return r;
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function salvarChaveIA(_: Resultado, form: FormData): Promise<Resultado> {
  const api_key = String(form.get("api_key") ?? "");
  const resultado = await executar(
    () => api("/api/v1/settings/ai", { method: "PUT", body: { api_key } }),
    "Chave salva. Os agentes desta empresa já podem trabalhar.",
  );
  revalidatePath("/settings");
  return resultado;
}

export async function removerChaveIA(): Promise<void> {
  await api("/api/v1/settings/ai", { method: "DELETE" });
  revalidatePath("/settings");
}

function corpoEmail(form: FormData) {
  const porta = String(form.get("port") ?? "").trim();
  const portaImap = String(form.get("imap_port") ?? "").trim();
  return {
    provider: String(form.get("provider") ?? "gmail"),
    from_email: String(form.get("from_email") ?? ""),
    from_name: String(form.get("from_name") ?? "") || null,
    username: String(form.get("username") ?? "") || null,
    password: String(form.get("password") ?? ""),
    host: String(form.get("host") ?? "") || null,
    port: porta ? Number(porta) : null,
    imap_host: String(form.get("imap_host") ?? "") || null,
    imap_port: portaImap ? Number(portaImap) : null,
  };
}

export async function testarEmail(_: Resultado, form: FormData): Promise<Resultado> {
  try {
    return await api<{ ok: boolean; message: string }>("/api/v1/settings/email/test", {
      method: "POST",
      body: corpoEmail(form),
    });
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function salvarEmail(_: Resultado, form: FormData): Promise<Resultado> {
  const resultado = await executar(
    () => api("/api/v1/settings/email", { method: "PUT", body: corpoEmail(form) }),
    "Conta conectada. É deste endereço que as abordagens vão sair.",
  );
  revalidatePath("/settings");
  return resultado;
}

export async function salvarVolume(_: Resultado, form: FormData): Promise<Resultado> {
  const corpo = {
    daily_limit: Number(form.get("daily_limit") ?? 30),
    warmup_enabled: form.get("warmup_enabled") === "on",
    warmup_start: Number(form.get("warmup_start") ?? 10),
    warmup_daily_increment: Number(form.get("warmup_daily_increment") ?? 5),
    business_hours_only: form.get("business_hours_only") === "on",
    timezone: String(form.get("timezone") ?? "America/Sao_Paulo"),
  };
  const resultado = await executar(
    () => api("/api/v1/settings/sending", { method: "PUT", body: corpo }),
    "Limites salvos.",
  );
  revalidatePath("/settings");
  return resultado;
}

export async function enviarMensagem(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const resultado = await executar(
    () => api(`/api/v1/messages/${id}/send`, { method: "POST" }),
    "Enviada.",
  );
  revalidatePath("/drafts");
  return resultado;
}

export async function enviarFila(_: Resultado, _form: FormData): Promise<Resultado> {
  try {
    const r = await api<{ sent: number; blocked: { reason: string }[] }>(
      "/api/v1/messages/send-queued",
      { method: "POST" },
    );
    revalidatePath("/drafts");
    const parado = r.blocked[0]?.reason;
    return {
      ok: r.sent > 0,
      message:
        r.sent > 0
          ? `${r.sent} ${r.sent === 1 ? "enviada" : "enviadas"}.${parado ? ` Parou em: ${parado}` : ""}`
          : parado ?? "Nada foi enviado.",
    };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function reenviar(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const resultado = await executar(
    () => api(`/api/v1/messages/${id}/requeue`, { method: "POST" }),
    "De volta à fila. Tente enviar de novo.",
  );
  revalidatePath("/drafts");
  return resultado;
}

export async function buscarRespostas(_: Resultado, _form: FormData): Promise<Resultado> {
  try {
    const r = await api<{
      fetched: number;
      recorded: number;
      ignored: number;
      bounced: number;
    }>(
      "/api/v1/messages/fetch-inbox",
      { method: "POST" },
    );
    revalidatePath("/drafts");
    return {
      ok: true,
      message:
        r.fetched === 0
          ? "Nenhuma mensagem nova na caixa."
          : `${r.recorded} ${r.recorded === 1 ? "resposta ligada" : "respostas ligadas"} à conversa` +
            (r.bounced
              ? `, ${r.bounced} ${r.bounced === 1 ? "email voltou" : "emails voltaram"}`
              : "") +
            (r.ignored ? `, ${r.ignored} sem relação com campanha` : "") +
            ".",
    };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

// ------------------------------------------------------------- Company Brain
/**
 * Junta as linhas repetidas de um formulário num array de objetos.
 *
 * A tela repete N linhas existentes mais duas vazias, e o envio junta o que foi
 * preenchido. Sem botão "adicionar linha" e sem estado no cliente: linha vazia
 * simplesmente não entra, e quem precisa de mais salva e ganha duas novas.
 */
function linhas(form: FormData, prefixo: string, campos: string[]) {
  const colunas = campos.map((campo) => form.getAll(`${prefixo}_${campo}`).map(String));
  const total = Math.max(0, ...colunas.map((c) => c.length));
  const saida: Record<string, string>[] = [];
  for (let i = 0; i < total; i++) {
    const linha: Record<string, string> = {};
    campos.forEach((campo, c) => {
      const valor = (colunas[c][i] ?? "").trim();
      if (valor) linha[campo] = valor;
    });
    if (Object.keys(linha).length) saida.push(linha);
  }
  return saida;
}

function texto(form: FormData, nome: string): string | null {
  const valor = String(form.get(nome) ?? "").trim();
  return valor || null;
}

/** Objeto com só as chaves preenchidas — `{}` quando nada foi escrito. */
function objeto(form: FormData, campos: string[]) {
  const saida: Record<string, string> = {};
  for (const campo of campos) {
    const valor = texto(form, campo);
    if (valor) saida[campo] = valor;
  }
  return saida;
}

export async function salvarCerebro(_: Resultado, form: FormData): Promise<Resultado> {
  const corpo = {
    legal_name: texto(form, "legal_name"),
    website: texto(form, "website"),
    positioning: texto(form, "positioning"),
    products: linhas(form, "produto", ["nome", "descricao"]),
    cases: linhas(form, "caso", ["cliente", "resultado"]),
    objections: linhas(form, "objecao", ["objecao", "resposta"]),
    brand_voice: objeto(form, ["tom", "idioma", "evitar"]),
    sales_playbook: objeto(form, ["abertura", "proxima_etapa"]),
    ai_policies: objeto(form, ["nunca_prometer", "escalar_quando"]),
  };
  const resultado = await executar(
    () => api("/api/v1/company-brain", { method: "PUT", body: corpo }),
    "Salvo. Os agentes já usam isto na próxima execução.",
  );
  revalidatePath("/brain");
  return resultado;
}

// -------------------------------------------------------- base de conhecimento
export async function colarDocumento(_: Resultado, form: FormData): Promise<Resultado> {
  const resultado = await executar(
    () =>
      api("/api/v1/knowledge/documents", {
        method: "POST",
        body: {
          title: String(form.get("title") ?? "").trim(),
          content: String(form.get("content") ?? ""),
        },
      }),
    "Documento indexado. O agente de conversa já responde por ele.",
  );
  revalidatePath("/knowledge");
  return resultado;
}

export async function subirDocumento(_: Resultado, form: FormData): Promise<Resultado> {
  const arquivo = form.get("file");
  if (!(arquivo instanceof File) || arquivo.size === 0) {
    return { ok: false, message: "Escolha um arquivo." };
  }
  const envio = new FormData();
  envio.append("file", arquivo);
  const titulo = String(form.get("title") ?? "").trim();
  if (titulo) envio.append("title", titulo);

  const resultado = await executar(
    () => api("/api/v1/knowledge/documents/upload", { method: "POST", body: envio }),
    `"${arquivo.name}" indexado.`,
  );
  revalidatePath("/knowledge");
  return resultado;
}

export async function apagarDocumento(form: FormData) {
  const id = String(form.get("id"));
  await api(`/api/v1/knowledge/documents/${id}`, { method: "DELETE" });
  revalidatePath("/knowledge");
}

// ------------------------------------------------------------------- membros
export async function convidarMembro(_: Resultado, form: FormData): Promise<Resultado> {
  const email = String(form.get("email") ?? "").trim();
  const resultado = await executar(
    () =>
      api("/api/v1/tenants/me/members", {
        method: "POST",
        body: {
          email,
          password: String(form.get("password") ?? ""),
          full_name: String(form.get("full_name") ?? "").trim(),
          role: String(form.get("role") ?? "operator"),
        },
      }),
    `${email} entrou na equipe. Entregue a senha por um canal seguro — ela pode trocar depois.`,
  );
  revalidatePath("/team");
  return resultado;
}

export async function mudarPapel(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("user_id"));
  const resultado = await executar(
    () =>
      api(`/api/v1/tenants/me/members/${id}`, {
        method: "PATCH",
        body: { role: String(form.get("role")) },
      }),
    "Papel atualizado.",
  );
  revalidatePath("/team");
  return resultado;
}

export async function alternarMembro(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("user_id"));
  const ativar = String(form.get("is_active")) === "true";
  const resultado = await executar(
    () =>
      api(`/api/v1/tenants/me/members/${id}`, {
        method: "PATCH",
        body: { is_active: ativar },
      }),
    ativar ? "Acesso devolvido." : "Acesso suspenso. O histórico fica.",
  );
  revalidatePath("/team");
  return resultado;
}

export async function removerMembro(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("user_id"));
  const resultado = await executar(
    () => api(`/api/v1/tenants/me/members/${id}`, { method: "DELETE" }),
    "Removido desta empresa. A conta da pessoa continua existindo.",
  );
  revalidatePath("/team");
  return resultado;
}

export async function trocarSenha(_: Resultado, form: FormData): Promise<Resultado> {
  // A troca encerra as sessões abertas — inclusive esta. O token novo que a API
  // devolve tem de entrar no cookie aqui, senão quem acabou de trocar a senha
  // levaria 401 na página seguinte, o que pareceria bug e não segurança.
  try {
    const token = await api<TokenResponse>("/api/v1/auth/change-password", {
      method: "POST",
      body: {
        current_password: String(form.get("current_password") ?? ""),
        new_password: String(form.get("new_password") ?? ""),
      },
    });
    await setToken(token.access_token, token.expires_in_minutes);
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
  revalidatePath("/team");
  return {
    ok: true,
    message: "Senha trocada. As outras sessões abertas foram encerradas.",
  };
}
