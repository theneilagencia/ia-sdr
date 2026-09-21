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

// ------------------------------------------------------- disparo de agente
/**
 * Que entidade cada agente recebe.
 *
 * O envelope não pede "o prospect": pede a entidade daquele agente, e ela muda.
 * A pesquisa é sobre a **conta** (a empresa é o que se pesquisa na web); a
 * abordagem e a qualificação são sobre o **prospect**; a conversa é sobre a
 * **conversa**. Quem opera escolhe uma pessoa numa lista e não deveria precisar
 * saber disso — a tradução é aqui.
 */
const ALVO: Record<string, "company" | "prospect" | "conversation"> = {
  research: "company",
  outreach: "prospect",
  qualification: "prospect",
  conversation: "conversation",
};

/**
 * Agentes que vão para a fila em vez de rodar na requisição.
 *
 * A pesquisa faz busca na web e leva minutos. Nenhuma tela deve ficar
 * pendurada esperando — e nenhuma pessoa deveria ter que escolher entre "agora"
 * e "na fila" para descobrir isso na prática.
 */
const NA_FILA = new Set(["research"]);

type Alvo = {
  prospect?: string;
  company?: string | null;
  contact?: string;
  conversation?: string;
  campaign?: string | null;
};

/** O que aconteceu, dito pelo efeito e não pelo nome do agente. */
const DEPOIS: Record<string, string> = {
  outreach: "Abordagem escrita. Ela está em Revisão, esperando alguém ler antes de sair.",
  conversation:
    "Resposta escrita. Ela está em Revisão — se o agente não achou a resposta na base " +
    "de conhecimento, o rascunho vem marcado para um humano.",
  qualification: "Veredito registrado, critério a critério, no prospect.",
};

export async function dispararAgente(_: Resultado, form: FormData): Promise<Resultado> {
  const agente = String(form.get("agent") ?? "");
  const entidade = ALVO[agente];
  if (!entidade) {
    return {
      ok: false,
      message: agente
        ? `O agente "${agente}" não tem alvo definido nesta tela.`
        : "Escolha o agente.",
    };
  }

  const cru = String(form.get(entidade === "conversation" ? "conversation" : "prospect") ?? "");
  if (!cru) {
    return {
      ok: false,
      message:
        entidade === "conversation"
          ? "Escolha a conversa que o agente deve responder."
          : "Escolha para quem o agente vai trabalhar.",
    };
  }

  let alvo: Alvo;
  try {
    alvo = JSON.parse(cru) as Alvo;
  } catch {
    return { ok: false, message: "Alvo inválido. Recarregue a página e escolha de novo." };
  }

  // A pesquisa cai no contato quando o prospect entrou sem empresa: o executor
  // aceita os dois, e perder o disparo por causa de um campo vazio na
  // importação seria gratuito.
  const [entity_type, entity_id] =
    entidade === "conversation"
      ? ["conversation", alvo.conversation]
      : entidade === "company"
        ? alvo.company
          ? ["company", alvo.company]
          : ["contact", alvo.contact]
        : ["prospect", alvo.prospect];

  if (!entity_id) return { ok: false, message: "Este alvo não tem o que o agente precisa." };

  const corpo = {
    agent: agente,
    campaign_id: alvo.campaign ?? null,
    entity_type,
    entity_id,
    params: {},
  };

  try {
    if (NA_FILA.has(agente)) {
      await api("/api/v1/agents/jobs", { method: "POST", body: corpo });
      revalidatePath("/agents");
      return {
        ok: true,
        message:
          "Pesquisa na fila. Ela busca na web e leva alguns minutos; o resultado " +
          "aparece abaixo, em Execuções.",
      };
    }
    const run = await api<{ status: string; error: string | null }>("/api/v1/agents/runs", {
      method: "POST",
      body: corpo,
    });
    revalidatePath("/agents");
    if (run.status !== "succeeded") {
      return { ok: false, message: run.error ?? "O agente não concluiu." };
    }
    return { ok: true, message: DEPOIS[agente] ?? "Feito." };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

// ------------------------------------------------------------ import de lista
export async function importarLista(_: Resultado, form: FormData): Promise<Resultado> {
  const arquivo = form.get("file");
  if (!(arquivo instanceof File) || arquivo.size === 0) {
    return { ok: false, message: "Escolha o arquivo CSV com a lista." };
  }
  const campanha = String(form.get("campaign_id") ?? "");
  if (!campanha) return { ok: false, message: "Escolha a campanha que vai receber a lista." };

  const envio = new FormData();
  envio.append("file", arquivo);
  envio.append("campaign_id", campanha);
  const origem = String(form.get("source") ?? "").trim();
  if (origem) envio.append("source", origem);

  try {
    const r = await api<{
      imported: number;
      duplicates: number;
      rows_read: number;
      row_errors: { line: number; error: string }[];
      missing_email: number;
    }>("/api/v1/prospects/import/csv", { method: "POST", body: envio });
    revalidatePath("/prospects");

    // O relatório diz as quatro coisas juntas. Import que anuncia "98
    // importados" e cala sobre as oito linhas que ficaram de fora é pior do que
    // import que falha: ninguém vai atrás do que não sabe que perdeu.
    const partes = [
      `${r.rows_read} ${r.rows_read === 1 ? "linha lida" : "linhas lidas"}`,
      `${r.imported} ${r.imported === 1 ? "importado" : "importados"}`,
    ];
    if (r.duplicates) partes.push(`${r.duplicates} já estavam na campanha`);
    // Linha sem email não é erro — lista de LinkedIn é assim — mas o agente de
    // abordagem se recusa a escrever para quem não tem endereço. Melhor saber
    // aqui do que no disparo que não escreveu.
    if (r.missing_email)
      partes.push(
        `${r.missing_email} sem email: a abordagem não sai para ${r.missing_email === 1 ? "esse" : "esses"}`,
      );
    if (r.row_errors.length) {
      const detalhe = r.row_errors
        .slice(0, 5)
        .map((e) => `linha ${e.line}: ${e.error}`)
        .join("; ");
      const resto = r.row_errors.length - Math.min(5, r.row_errors.length);
      partes.push(
        `${r.row_errors.length} ${r.row_errors.length === 1 ? "linha ficou" : "linhas ficaram"} de fora — ${detalhe}${resto ? ` (e mais ${resto})` : ""}`,
      );
    }
    return { ok: r.imported > 0, message: `${partes.join(" · ")}.` };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

// -------------------------------------------------------------- campanhas
/**
 * O slug sai do nome.
 *
 * É identificador de URL, não conteúdo: pedir para quem está criando a
 * campanha inventar um seria pedir para resolver um problema nosso. Nome
 * repetido dá conflito na API, e a mensagem que volta diz isso.
 */
function slugificar(nome: string): string {
  return nome
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
}

/** Pares "chave: valor" vindos de linhas repetidas, viram um dicionário. */
function dicionario(form: FormData, prefixo: string) {
  const saida: Record<string, string> = {};
  for (const linha of linhas(form, prefixo, ["chave", "valor"])) {
    if (linha.chave && linha.valor) saida[linha.chave] = linha.valor;
  }
  return saida;
}

export async function criarCampanha(_: Resultado, form: FormData): Promise<Resultado> {
  const nome = String(form.get("name") ?? "").trim();
  if (nome.length < 2) return { ok: false, message: "Dê um nome à campanha." };
  const slug = slugificar(nome);
  if (!slug) {
    return { ok: false, message: "O nome precisa ter letras ou números." };
  }

  const resultado = await executar(
    () =>
      api("/api/v1/campaigns", {
        method: "POST",
        body: {
          name: nome,
          slug,
          objective: texto(form, "objective"),
          target_geography: String(form.get("target_geography") ?? "")
            .split(",")
            .map((g) => g.trim())
            .filter(Boolean),
        },
      }),
    `Campanha "${nome}" criada como rascunho. Preencha o que os agentes leem e ative.`,
  );
  revalidatePath("/campaigns");
  return resultado;
}

export async function salvarCampanha(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const resultado = await executar(
    () =>
      api(`/api/v1/campaigns/${id}`, {
        method: "PATCH",
        body: {
          objective: texto(form, "objective"),
          target_geography: String(form.get("target_geography") ?? "")
            .split(",")
            .map((g) => g.trim())
            .filter(Boolean),
          icp: dicionario(form, "icp"),
          personas: linhas(form, "persona", ["cargo", "dor"]),
          offer: objeto(form, ["o_que_vendemos", "resultado_esperado", "prova"]),
          messaging: objeto(form, ["angulo", "gancho", "chamada_para_acao", "evitar"]),
          qualification_criteria: dicionario(form, "criterio"),
        },
      }),
    "Salvo. Os agentes desta campanha já usam isto na próxima execução.",
  );
  revalidatePath("/campaigns");
  return resultado;
}

export async function mudarStatusCampanha(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const novo = String(form.get("status"));
  const resultado = await executar(
    () => api(`/api/v1/campaigns/${id}`, { method: "PATCH", body: { status: novo } }),
    novo === "active"
      ? "Campanha ativa. A cadência volta a andar nos prospects dela."
      : "Campanha pausada. Os toques agendados esperam; nada é perdido.",
  );
  revalidatePath("/campaigns");
  return resultado;
}

// --------------------------------------------------------------- conversas
export async function escreverResposta(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("conversation_id"));
  const corpo = String(form.get("body") ?? "").trim();
  if (!corpo) return { ok: false, message: "Escreva a resposta antes de salvar." };

  const resultado = await executar(
    () =>
      api(`/api/v1/conversations/${id}/messages`, {
        method: "POST",
        body: { body: corpo, subject: texto(form, "subject") },
      }),
    // Nasce rascunho de propósito: limite diário, aquecimento, horário e
    // descadastro estão todos depois da aprovação. Dizer isso aqui evita a
    // conclusão errada de que a mensagem já saiu.
    "Salva como rascunho. Ela sai pela fila de Revisão, com os mesmos freios de envio.",
  );
  revalidatePath(`/conversations/${id}`);
  revalidatePath("/conversations");
  return resultado;
}

export async function mudarConversa(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("conversation_id"));
  const novo = String(form.get("status") ?? "");
  const resultado = await executar(
    () => api(`/api/v1/conversations/${id}`, { method: "PATCH", body: { status: novo } }),
    novo === "closed" ? "Conversa fechada." : "Conversa reaberta.",
  );
  revalidatePath(`/conversations/${id}`);
  revalidatePath("/conversations");
  return resultado;
}

export async function passarParaPessoa(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("conversation_id"));
  const usuario = String(form.get("handoff_to_user_id") ?? "");
  const resultado = await executar(
    () =>
      api(`/api/v1/conversations/${id}`, {
        method: "PATCH",
        // Sem ninguém escolhido, a conversa volta para o agente.
        body: usuario
          ? { handoff_to_user_id: usuario }
          : { clear_handoff: true },
      }),
    usuario
      ? "Passada. Ela aparece como responsabilidade dessa pessoa."
      : "Devolvida ao agente.",
  );
  revalidatePath(`/conversations/${id}`);
  revalidatePath("/conversations");
  return resultado;
}

/**
 * Pedir ao agente de conversa que escreva a resposta.
 *
 * É o mesmo disparo da tela de Agentes, oferecido onde a pessoa já está: quem
 * abriu a conversa para responder não deveria ter que sair dela, procurar a
 * conversa numa lista e disparar de lá.
 */
export async function pedirRespostaAoAgente(_: Resultado, form: FormData): Promise<Resultado> {
  const envio = new FormData();
  envio.append("agent", "conversation");
  envio.append(
    "conversation",
    JSON.stringify({
      conversation: String(form.get("conversation_id")),
      campaign: String(form.get("campaign_id") ?? "") || null,
    }),
  );
  const resultado = await dispararAgente(null, envio);
  revalidatePath(`/conversations/${String(form.get("conversation_id"))}`);
  return resultado;
}

// ------------------------------------------------------- detalhe do prospect
export async function marcarReuniao(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("prospect_id"));
  const quando = String(form.get("scheduled_at") ?? "");
  if (!quando) return { ok: false, message: "Escolha a data e a hora." };

  const resultado = await executar(
    () =>
      api(`/api/v1/prospects/${id}/meetings`, {
        method: "POST",
        body: {
          // O input datetime-local manda hora local sem fuso; o backend quer
          // instante. A conversão é aqui, no servidor do Next, com o fuso de
          // quem preencheu — não no banco, adivinhando.
          scheduled_at: new Date(quando).toISOString(),
          duration_minutes: Number(form.get("duration_minutes") ?? 30),
          location: texto(form, "location"),
          notes: texto(form, "notes"),
        },
      }),
    "Reunião marcada. É a conversão que a plataforma existe para produzir.",
  );
  revalidatePath(`/prospects/${id}`);
  revalidatePath("/");
  return resultado;
}

export async function enviarParaRavi(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("prospect_id"));
  try {
    const r = await api<{ status: string; lead_id: string | null; reason: string | null }>(
      `/api/v1/prospects/${id}/sync-crm?force=true`,
      { method: "POST" },
    );
    revalidatePath(`/prospects/${id}`);
    const legivel: Record<string, string> = {
      created: "Lead criado no RAVI",
      updated: "Lead atualizado no RAVI",
      skipped: "Nada foi enviado",
      queued: "Na fila para o RAVI",
    };
    const cabeca = legivel[r.status] ?? r.status;
    return {
      ok: r.status !== "skipped",
      message: r.reason ? `${cabeca}: ${r.reason}.` : `${cabeca}.`,
    };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function registrarDescadastro(_: Resultado, form: FormData): Promise<Resultado> {
  const contato = String(form.get("contact_id"));
  const prospect = String(form.get("prospect_id"));
  const resultado = await executar(
    () => api(`/api/v1/contacts/${contato}`, { method: "PATCH", body: { opted_out: true } }),
    // Não há botão para desfazer, e é de propósito: quem pediu para não receber
    // mais contato não volta para a lista por um clique errado.
    "Descadastro registrado. Nenhum agente escreve para esta pessoa de novo.",
  );
  revalidatePath(`/prospects/${prospect}`);
  return resultado;
}

export async function registrarRespostaRecebida(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("prospect_id"));
  const corpo = String(form.get("body") ?? "").trim();
  if (!corpo) return { ok: false, message: "Cole o que a pessoa respondeu." };

  const resultado = await executar(
    () =>
      api(`/api/v1/prospects/${id}/messages/inbound`, {
        method: "POST",
        body: { body: corpo, subject: texto(form, "subject") },
      }),
    // O caminho normal é o IMAP trazer a resposta. Isto é para quando o lead
    // respondeu por outro canal — telefone, WhatsApp, um encontro — e o
    // histórico do agente ficaria sem o que a pessoa disse.
    "Resposta registrada. O prospect passou a engajado e o agente já lê isto no contexto.",
  );
  revalidatePath(`/prospects/${id}`);
  revalidatePath("/conversations");
  return resultado;
}

/**
 * Disparar um agente a partir do detalhe do prospect.
 *
 * Mesma tradução da tela de Agentes, oferecida onde a decisão acontece: a tela
 * que diz "sem nota ainda, dispare a pesquisa" não deveria mandar a pessoa
 * procurar este mesmo prospect numa lista em outra tela.
 */
export async function dispararNoProspect(_: Resultado, form: FormData): Promise<Resultado> {
  const envio = new FormData();
  envio.append("agent", String(form.get("agent") ?? ""));
  envio.append(
    "prospect",
    JSON.stringify({
      prospect: String(form.get("prospect_id")),
      company: String(form.get("company_id") ?? "") || null,
      contact: String(form.get("contact_id") ?? ""),
      campaign: String(form.get("campaign_id") ?? "") || null,
    }),
  );
  const resultado = await dispararAgente(null, envio);
  revalidatePath(`/prospects/${String(form.get("prospect_id"))}`);
  return resultado;
}
