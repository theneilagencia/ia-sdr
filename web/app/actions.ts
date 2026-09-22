"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { api, ApiError, type Resultado, type ResultadoDoConvite } from "@/lib/api";
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
      failed: number;
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
            // Mensagem que deu defeito continua não lida na caixa e será
            // tentada de novo. Omitir o número aqui esconderia justamente o
            // caso em que alguém precisa ir olhar a caixa com os próprios olhos.
            (r.failed
              ? `, ${r.failed} com defeito (segue não lida na caixa, para nova tentativa)`
              : "") +
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
/**
 * Convidar é emitir um link, não criar senha para outra pessoa.
 *
 * O resultado carrega `link` porque ele existe **uma vez**: a API devolve o
 * token nesta resposta e guarda só o hash. Recarregar a tela não traz o link de
 * volta — e é isso que faz dele credencial de uso único em vez de dado de
 * cadastro.
 */
export async function convidarMembro(
  _: ResultadoDoConvite,
  form: FormData,
): Promise<ResultadoDoConvite> {
  const email = String(form.get("email") ?? "").trim();
  try {
    const convite = await api<{ accept_url: string }>("/api/v1/tenants/me/invitations", {
      method: "POST",
      body: { email, role: String(form.get("role") ?? "operator") },
    });
    revalidatePath("/team");
    return {
      ok: true,
      message: `Convite para ${email}. Entregue este link por um canal seguro: ele vale 7 dias, serve uma vez, e a senha é escolhida por quem entra.`,
      link: convite.accept_url,
    };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function revogarConvite(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id") ?? "");
  const email = String(form.get("email") ?? "");
  const resultado = await executar(
    () => api(`/api/v1/tenants/me/invitations/${id}`, { method: "DELETE" }),
    `Convite de ${email} revogado. O link para de funcionar agora, e a vaga do plano volta a ficar livre.`,
  );
  revalidatePath("/team");
  return resultado;
}

/**
 * Aceitar o convite: a pessoa prova quem é e já entra.
 *
 * Quando o email é novo na plataforma, a senha digitada aqui nasce com a conta.
 * Quando já existe conta, a senha tem de ser a dela — o convite anexa o vínculo,
 * não abre a conta de ninguém. A recusa é uma frase só para os dois casos, de
 * propósito: distinguir contaria a quem convidou quem já usa a plataforma.
 */
export async function aceitarConvite(_: string | null, form: FormData): Promise<string | null> {
  try {
    const token = await api<TokenResponse>("/api/v1/auth/invitations/accept", {
      method: "POST",
      body: {
        token: String(form.get("token") ?? ""),
        password: String(form.get("password") ?? ""),
        full_name: String(form.get("full_name") ?? "").trim(),
      },
      requireAuth: false,
    });
    await setToken(token.access_token, token.expires_in_minutes);
  } catch (erro) {
    if (erro instanceof ApiError) return erro.message;
    throw erro;
  }
  redirect("/");
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

// ---------------------------------------------------- painel da plataforma
/**
 * Os limites que um override pode mexer.
 *
 * São exatamente as chaves que o plano define: `effective_limits` ignora chave
 * que não existe no plano, então oferecer outras seria oferecer um campo que não
 * faz nada. `-1` é ilimitado, e é o valor que o Enterprise já usa.
 */
const LIMITES = [
  "campaigns",
  "prospects_per_month",
  "users",
  "email_accounts",
  "ai_units_per_month",
  "ai_cost_usd_per_month",
  "knowledge_documents",
] as const;

export async function salvarEmpresaDaPlataforma(
  _: Resultado,
  form: FormData,
): Promise<Resultado> {
  const id = String(form.get("tenant_id"));

  // O PATCH substitui o dicionário inteiro, então a tela manda todos os
  // campos: mandar só o alterado apagaria os outros overrides em silêncio.
  const overrides: Record<string, number> = {};
  for (const chave of LIMITES) {
    const bruto = String(form.get(`limite_${chave}`) ?? "").trim();
    if (bruto === "") continue;
    const valor = Number(bruto);
    if (!Number.isInteger(valor)) {
      return { ok: false, message: `O limite de ${chave} precisa ser um número inteiro.` };
    }
    overrides[chave] = valor;
  }

  const resultado = await executar(
    () =>
      api(`/api/v1/admin/tenants/${id}`, {
        method: "PATCH",
        body: {
          plan: String(form.get("plan") ?? "") || null,
          subscription_status: String(form.get("subscription_status") ?? "") || null,
          limit_overrides: overrides,
        },
      }),
    "Salvo. O plano e os limites valem na próxima verificação de cota.",
  );
  revalidatePath("/platform");
  return resultado;
}

export async function alternarEmpresaDaPlataforma(
  _: Resultado,
  form: FormData,
): Promise<Resultado> {
  const id = String(form.get("tenant_id"));
  const ativar = String(form.get("is_active")) === "true";
  const resultado = await executar(
    () => api(`/api/v1/admin/tenants/${id}`, { method: "PATCH", body: { is_active: ativar } }),
    ativar
      ? "Empresa reativada. Todo mundo dela volta a entrar."
      : "Empresa suspensa. Ninguém dela entra, e o histórico fica inteiro.",
  );
  revalidatePath("/platform");
  return resultado;
}

// ------------------------------------------------------- conexão com o RAVI
function corpoCrm(form: FormData) {
  return {
    base_url: String(form.get("base_url") ?? "").trim(),
    token: String(form.get("token") ?? ""),
    ravi_tenant_id: String(form.get("ravi_tenant_id") ?? "").trim(),
    default_stage: texto(form, "default_stage"),
  };
}

export async function testarRavi(_: Resultado, form: FormData): Promise<Resultado> {
  try {
    return await api<{ ok: boolean; message: string }>("/api/v1/settings/crm/test", {
      method: "POST",
      body: corpoCrm(form),
    });
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function salvarRavi(_: Resultado, form: FormData): Promise<Resultado> {
  // A API testa antes de salvar e recusa credencial que não funciona: salvar sem
  // testar deixaria a empresa achando que o CRM está ligado enquanto a fila
  // acumula falha em silêncio.
  const resultado = await executar(
    () => api("/api/v1/settings/crm", { method: "PUT", body: corpoCrm(form) }),
    "Conectado. Os leads qualificados passam a subir para o RAVI.",
  );
  revalidatePath("/settings");
  return resultado;
}

export async function desligarRavi(): Promise<void> {
  await api("/api/v1/settings/crm", { method: "DELETE" });
  revalidatePath("/settings");
}

// --------------------------------------------------------------- cadências
/**
 * Os passos de uma cadência, a partir das linhas repetidas do formulário.
 *
 * A espera do primeiro passo é ignorada pela API por definição — ele é a
 * abordagem inicial, e agendar o primeiro contato para daqui a três dias só
 * confundiria quem montou a cadência. Passo sem instrução não entra: o segundo
 * email igual ao primeiro é pior que nenhum.
 */
function passos(form: FormData) {
  const instrucoes = form.getAll("passo_instruction").map(String);
  const esperas = form.getAll("passo_wait_days").map(String);
  const saida: { instruction: string; wait_days: number }[] = [];
  instrucoes.forEach((instrucao, i) => {
    const texto = instrucao.trim();
    if (!texto) return;
    const dias = Number((esperas[i] ?? "").trim() || 3);
    saida.push({ instruction: texto, wait_days: saida.length === 0 ? 0 : dias });
  });
  return saida;
}

export async function criarCadencia(_: Resultado, form: FormData): Promise<Resultado> {
  const etapas = passos(form);
  if (etapas.length === 0) {
    return { ok: false, message: "Escreva pelo menos o primeiro toque." };
  }
  const campanha = String(form.get("campaign_id") ?? "");
  if (!campanha) return { ok: false, message: "Escolha a campanha desta cadência." };

  const resultado = await executar(
    () =>
      api("/api/v1/sequences", {
        method: "POST",
        body: {
          campaign_id: campanha,
          name: String(form.get("name") ?? "").trim(),
          steps: etapas,
          // Nasce desativada: quem acabou de escrever os toques ainda vai
          // reler. Ativar é um clique, e é um clique consciente.
          is_active: false,
        },
      }),
    `Cadência criada com ${etapas.length} ${etapas.length === 1 ? "toque" : "toques"}, desativada. Revise e ative.`,
  );
  revalidatePath("/sequences");
  return resultado;
}

export async function salvarCadencia(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("sequence_id"));
  const etapas = passos(form);
  if (etapas.length === 0) {
    return { ok: false, message: "Uma cadência precisa de pelo menos um toque." };
  }
  const resultado = await executar(
    () =>
      api(`/api/v1/sequences/${id}`, {
        method: "PATCH",
        body: { name: String(form.get("name") ?? "").trim(), steps: etapas },
      }),
    // Editar passo vale para quem ainda não chegou nele: a inscrição guarda em
    // que passo está, não o texto que já saiu.
    "Salvo. Vale para quem ainda não chegou nestes toques.",
  );
  revalidatePath("/sequences");
  return resultado;
}

export async function alternarCadencia(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("sequence_id"));
  const ativar = String(form.get("is_active")) === "true";
  const resultado = await executar(
    () => api(`/api/v1/sequences/${id}`, { method: "PATCH", body: { is_active: ativar } }),
    ativar
      ? "Cadência ativa. Os toques vencidos passam a virar rascunho."
      : "Cadência desativada. Ninguém recebe o próximo toque; o histórico fica.",
  );
  revalidatePath("/sequences");
  return resultado;
}

export async function apagarCadencia(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("sequence_id"));
  const resultado = await executar(
    () => api(`/api/v1/sequences/${id}`, { method: "DELETE" }),
    "Cadência apagada.",
  );
  revalidatePath("/sequences");
  return resultado;
}

export async function inscreverNaCadencia(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("sequence_id"));
  const escolhidos = form.getAll("prospect_ids").map(String).filter(Boolean);
  if (escolhidos.length === 0) {
    return { ok: false, message: "Escolha quem entra na cadência." };
  }

  try {
    const r = await api<{
      enrolled: unknown[];
      skipped: { reason?: string }[];
    }>(`/api/v1/sequences/${id}/enroll`, {
      method: "POST",
      body: { prospect_ids: escolhidos },
    });
    revalidatePath("/sequences");

    // Quem não entrou e por quê, junto: selecionar cem leads e perder a
    // operação inteira porque três já estavam na cadência seria hostil.
    const partes = [
      `${r.enrolled.length} ${r.enrolled.length === 1 ? "inscrito" : "inscritos"}`,
    ];
    if (r.skipped.length) {
      const motivos = [...new Set(r.skipped.map((s) => s.reason ?? "sem motivo"))];
      partes.push(
        `${r.skipped.length} de fora (${motivos.join("; ")})`,
      );
    }
    return { ok: r.enrolled.length > 0, message: `${partes.join(" · ")}.` };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

export async function pararInscricao(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("enrollment_id"));
  const resultado = await executar(
    () => api(`/api/v1/sequences/enrollments/${id}/stop`, { method: "POST" }),
    "Fora da cadência. Os outros inscritos seguem.",
  );
  revalidatePath("/sequences");
  return resultado;
}

export async function avancarCadencias(_: Resultado, _form: FormData): Promise<Resultado> {
  try {
    const r = await api<{
      gerados: number;
      parados: number;
      concluidos: number;
      adiados: number;
    }>("/api/v1/sequences/tick", { method: "POST" });
    revalidatePath("/sequences");
    revalidatePath("/drafts");

    // O tick é o que o worker faz sozinho; aqui serve para não esperar o
    // relógio enquanto se monta a cadência, e para ter onde olhar quando
    // alguém pergunta por que o follow-up não saiu.
    // `gerados` conta job enfileirado, não rascunho escrito: o texto aparece em
    // Revisão depois que o trabalhador roda o agente de abordagem. Dizer
    // "rascunho em Revisão" aqui mandaria a pessoa olhar uma fila que ainda
    // está vazia.
    const partes: string[] = [];
    if (r.gerados)
      partes.push(
        `${r.gerados} ${r.gerados === 1 ? "toque foi" : "toques foram"} para a fila; o agente de abordagem escreve e o rascunho aparece em Revisão`,
      );
    if (r.parados) partes.push(`${r.parados} ${r.parados === 1 ? "parada" : "paradas"} pelas regras`);
    if (r.concluidos) partes.push(`${r.concluidos} ${r.concluidos === 1 ? "concluída" : "concluídas"}`);
    if (r.adiados) partes.push(`${r.adiados} ${r.adiados === 1 ? "adiada" : "adiadas"} (campanha pausada)`);
    return {
      ok: r.gerados > 0,
      message: partes.length
        ? `${partes.join(" · ")}.`
        : "Nada vencido agora — nenhum toque estava na hora.",
    };
  } catch (erro) {
    if (erro instanceof ApiError) return { ok: false, message: erro.message };
    throw erro;
  }
}

// ---------------------------------------------------- contas-alvo e contatos
/**
 * As duas tabelas que só o import escrevia.
 *
 * Domínio trocado é o erro mais caro de uma planilha: o agente pesquisa a
 * empresa errada, escreve com os fatos dela e ninguém percebe até o lead
 * responder confuso. Corrigir isso exigia ir ao banco.
 */
export async function criarConta(_: Resultado, form: FormData): Promise<Resultado> {
  const corpo: Record<string, unknown> = {
    name: String(form.get("name") ?? "").trim(),
    domain: texto(form, "domain"),
    industry: texto(form, "industry"),
    country: texto(form, "country"),
    description: texto(form, "description"),
  };
  const porte = String(form.get("employee_count") ?? "").trim();
  if (porte) corpo.employee_count = Number(porte);

  const resultado = await executar(
    () => api("/api/v1/companies", { method: "POST", body: corpo }),
    "Conta criada. Já serve de alvo para a pesquisa.",
  );
  revalidatePath("/accounts");
  return resultado;
}

export async function salvarConta(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const corpo: Record<string, unknown> = {
    name: String(form.get("name") ?? "").trim(),
    domain: texto(form, "domain"),
    industry: texto(form, "industry"),
    country: texto(form, "country"),
    description: texto(form, "description"),
  };
  const porte = String(form.get("employee_count") ?? "").trim();
  corpo.employee_count = porte ? Number(porte) : null;

  const resultado = await executar(
    () => api(`/api/v1/companies/${id}`, { method: "PATCH", body: corpo }),
    "Conta corrigida. A próxima pesquisa usa o dado novo.",
  );
  revalidatePath("/accounts");
  return resultado;
}

export async function criarContato(_: Resultado, form: FormData): Promise<Resultado> {
  const corpo: Record<string, unknown> = {
    full_name: String(form.get("full_name") ?? "").trim(),
    email: texto(form, "email"),
    phone: texto(form, "phone"),
    title: texto(form, "title"),
    persona: texto(form, "persona"),
  };
  const empresa = String(form.get("company_id") ?? "").trim();
  if (empresa) corpo.company_id = empresa;

  const resultado = await executar(
    () => api("/api/v1/contacts", { method: "POST", body: corpo }),
    "Contato criado.",
  );
  revalidatePath("/contacts");
  return resultado;
}

export async function salvarContato(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const corpo: Record<string, unknown> = {
    full_name: String(form.get("full_name") ?? "").trim(),
    email: texto(form, "email"),
    phone: texto(form, "phone"),
    title: texto(form, "title"),
    persona: texto(form, "persona"),
  };
  const empresa = String(form.get("company_id") ?? "").trim();
  corpo.company_id = empresa || null;

  const resultado = await executar(
    () => api(`/api/v1/contacts/${id}`, { method: "PATCH", body: corpo }),
    "Contato atualizado.",
  );
  revalidatePath("/contacts");
  return resultado;
}

/**
 * Descadastro pedido por fora do email — telefone, WhatsApp, resposta a uma
 * pessoa. Só vai numa direção: a API não desmarca, e a tela não oferece.
 */
export async function descadastrarContato(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const resultado = await executar(
    () => api(`/api/v1/contacts/${id}`, { method: "PATCH", body: { opted_out: true } }),
    "Descadastro registrado. Esta pessoa não recebe mais nada, nem por cadência.",
  );
  revalidatePath("/contacts");
  return resultado;
}

/**
 * Apagar contato só existe para o que nunca foi usado.
 *
 * A API recusa apagar quem tem histórico — e isso é proteção, não limitação:
 * apagar levaria embora a prova de que um descadastro foi pedido, e o próximo
 * import traria a pessoa de volta.
 */
export async function apagarContato(_: Resultado, form: FormData): Promise<Resultado> {
  const id = String(form.get("id"));
  const resultado = await executar(
    () => api(`/api/v1/contacts/${id}`, { method: "DELETE" }),
    "Contato apagado.",
  );
  revalidatePath("/contacts");
  return resultado;
}

// ---------------------------------------------------- configuração de agente
/**
 * Modelo, instruções e teto de saída por agente.
 *
 * Existia no banco desde o primeiro sprint e era lido pelo orquestrador em toda
 * execução — só não tinha porta: trocar o modelo de um cliente exigia `INSERT`.
 * O formulário vai inteiro de propósito; mandar só o campo alterado faria a
 * segunda edição parecer apagar a primeira.
 */
export async function salvarConfigAgente(_: Resultado, form: FormData): Promise<Resultado> {
  const kind = String(form.get("kind"));
  const corpo: Record<string, unknown> = {
    model: String(form.get("model") ?? ""),
    instructions: String(form.get("instructions") ?? "").trim(),
    is_active: String(form.get("is_active")) === "true",
  };
  const teto = String(form.get("max_output_tokens") ?? "").trim();
  if (teto) corpo.max_output_tokens = Number(teto);

  const resultado = await executar(
    () => api(`/api/v1/agents/config/${kind}`, { method: "PUT", body: corpo }),
    "Configuração salva. Vale a partir da próxima execução deste agente.",
  );
  revalidatePath("/agents");
  return resultado;
}
