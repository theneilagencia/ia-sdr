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

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });

  if (response.status === 401 && requireAuth) redirect("/login");

  const texto = await response.text();
  const dados = texto ? JSON.parse(texto) : null;

  if (!response.ok) {
    const erro = dados?.error ?? {};
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
  by_kind: Record<string, { units: number; quantity: number; cost_usd: number }>;
};

export type Me = {
  email: string;
  full_name: string;
  role: string;
  tenant_id: string;
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
