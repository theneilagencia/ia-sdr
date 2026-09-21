import { redirect } from "next/navigation";

import { getToken } from "./session";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

type Options = {
  method?: string;
  body?: unknown;
  /** Sem sessão, manda para o login em vez de estourar erro. */
  requireAuth?: boolean;
};

/**
 * Chamada à API feita **do servidor**, nunca do browser.
 *
 * O token não atravessa para o cliente, e nenhuma credencial aparece no
 * bundle. É a mesma regra do backend: segredo não vai para o frontend.
 */
export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", body, requireAuth = true } = options;
  const token = await getToken();
  if (requireAuth && !token) redirect("/login");

  // Upload de arquivo vai como multipart. O `content-type` precisa ficar de
  // fora: é o fetch que monta o boundary, e fixá-lo aqui quebraria o parse do
  // outro lado.
  const multipart = body instanceof FormData;

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      ...(multipart ? {} : { "content-type": "application/json" }),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    cache: "no-store",
  });

  if (response.status === 401 && requireAuth) redirect("/login");

  const texto = await response.text();

  // Nem toda resposta de erro é JSON: um 500 não tratado volta como texto
  // puro, e tentar interpretar isso como JSON transformaria um erro legível
  // numa tela de exceção.
  let dados: unknown = null;
  try {
    dados = texto ? JSON.parse(texto) : null;
  } catch {
    if (response.ok)
      throw new ApiError(
        response.status,
        "resposta_invalida",
        "A API devolveu uma resposta que não dá para interpretar.",
      );
    throw new ApiError(
      response.status,
      "erro_inesperado",
      `A API falhou (${response.status}). Tente de novo; se persistir, veja os logs.`,
    );
  }

  if (!response.ok) {
    const erro =
      (dados as { error?: { code?: string; message?: string } })?.error ?? {};
    throw new ApiError(
      response.status,
      erro.code ?? "erro",
      erro.message ?? `Falha na API (${response.status})`,
    );
  }
  return dados as T;
}

export type Funnel = {
  prospects: number;
  researched: number;
  scored: number;
  contacted: number;
  engaged: number;
  qualified: number;
  meetings: number;
  by_band: Record<string, number>;
};

export type Campaign = {
  id: string;
  name: string;
  slug: string;
  status: string;
  target_geography: string[];
  created_at: string;
};

export type Prospect = {
  id: string;
  campaign_id: string;
  status: string;
  source: string | null;
  created_at: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  direction: string;
  status: string;
  subject: string | null;
  body: string;
  metrics: Record<string, unknown>;
  created_at: string;
};

export type Usage = {
  ai_units_used: number;
  ai_units_limit: number;
  estimated_cost_usd: number;
  by_kind: Record<
    string,
    { units: number; quantity: number; cost_usd: number }
  >;
};

export type Me = {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  tenant_id: string;
  //: Quem opera a plataforma enxerga o painel; quem não, não vê o link.
  is_platform_admin: boolean;
  memberships: { tenant_id: string; tenant_name: string; role: string }[];
};

export type AISettings = {
  configured: boolean;
  key_hint: string | null;
  status: string;
  using_platform_key: boolean;
  updated_at: string | null;
};

export type EmailPreset = {
  provider: string;
  label: string;
  host: string;
  port: number;
  help: string;
};

export type EmailAccount = {
  configured: boolean;
  provider: string | null;
  from_email: string | null;
  from_name: string | null;
  host: string | null;
  port: number | null;
  imap_host: string | null;
  imap_port: number | null;
  status: string;
  last_error: string | null;
};

export type SendingPolicy = {
  daily_limit: number;
  warmup_enabled: boolean;
  warmup_start: number;
  warmup_daily_increment: number;
  business_hours_only: boolean;
  timezone: string;
};

/** O que as telas de configuração devolvem: deu certo, e o que dizer. */
export type Resultado = { ok: boolean; message: string } | null;

export type Allowance = {
  limit: number;
  used: number;
  remaining: number;
  reason: string;
  warmup_day: number | null;
  within_business_hours: boolean;
};

/**
 * O Company Brain, com os campos que os agentes de fato leem.
 *
 * A API guarda quinze campos; sete chegam ao contexto de algum agente. Os
 * outros existem no modelo e não são servidos por nenhuma tela — campo que
 * nada consome ensina a pessoa que preencher importa quando não importa.
 */
export type CompanyBrain = {
  legal_name: string | null;
  website: string | null;
  positioning: string | null;
  products: { nome?: string; descricao?: string }[];
  cases: { cliente?: string; resultado?: string }[];
  objections: { objecao?: string; resposta?: string }[];
  brand_voice: { tom?: string; idioma?: string; evitar?: string };
  sales_playbook: { abertura?: string; proxima_etapa?: string };
  ai_policies: { nunca_prometer?: string; escalar_quando?: string };
  updated_at: string;
};

export type KnowledgeDocument = {
  id: string;
  title: string;
  source_type: string;
  source_uri: string | null;
  status: string;
  campaign_ids: string[];
  char_count: number;
  chunk_count: number;
  error: string | null;
  created_at: string;
};

export type Member = {
  user_id: string;
  email: string;
  full_name: string;
  role: "owner" | "admin" | "operator" | "viewer";
  is_active: boolean;
};
