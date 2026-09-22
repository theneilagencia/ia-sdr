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
  //: As saídas. O retorno de email é o número que queima o domínio de quem
  //: envia; sem ele na tela, ninguém vigia o que mais importa vigiar.
  bounced: number;
  disqualified: number;
  by_band: Record<string, number>;
};

export type Campaign = {
  id: string;
  name: string;
  slug: string;
  status: string;
  objective: string | null;
  //: Tudo daqui para baixo entra no contexto dos agentes desta campanha. É o
  //: que diferencia duas campanhas da mesma empresa: mesma marca, ICP e
  //: critérios diferentes.
  //:
  //: São dicionários livres: a API aceita qualquer JSON, e uma campanha criada
  //: por API pode ter lista ou objeto no valor. O tipo diz `unknown` porque é o
  //: que é — quem exibe converte para texto de propósito, e não por acidente.
  icp: Record<string, unknown>;
  target_geography: string[];
  personas: Record<string, unknown>[];
  offer: Record<string, unknown>;
  messaging: Record<string, unknown>;
  qualification_criteria: Record<string, unknown>;
  created_at: string;
};

export type Prospect = {
  id: string;
  campaign_id: string;
  contact_id: string;
  company_id: string | null;
  status: string;
  source: string | null;
  last_activity_at: string | null;
  created_at: string;
  //: Resolvidos pela API. Sem eles a lista é uma coluna de UUIDs, e ninguém
  //: consegue escolher para quem disparar um agente.
  contact_name: string | null;
  contact_email: string | null;
  company_name: string | null;
  campaign_name: string | null;
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
  /** Teto de custo real do mês, em dólares. `-1` é sem teto. */
  estimated_cost_limit_usd: number;
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

/** Um agente do catálogo, com o que ele custa por execução. */
export type AgentCatalogItem = {
  kind: string;
  name: string;
  model: string;
  tools: string[];
  units_per_run: number;
};

export type AgentRun = {
  id: string;
  job_id: string;
  agent_kind: string;
  status: string;
  campaign_id: string | null;
  entity_id: string | null;
  context_digest: string | null;
  units: number;
  output: Record<string, unknown>;
  error: string | null;
  created_at: string;
};

export type Job = {
  id: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  attempts: number;
  max_attempts: number;
  run_at: string;
  last_error: string | null;
  created_at: string;
};

export type Conversation = {
  id: string;
  prospect_id: string;
  campaign_id: string | null;
  channel: string;
  subject: string | null;
  status: string;
  last_message_at: string | null;
  handoff_to_user_id: string | null;
  created_at: string;
  contact_name: string | null;
  contact_email: string | null;
  company_name: string | null;
  message_count: number;
  awaiting_reply: boolean;
};

export type ConversationDetail = Conversation & { messages: Message[] };

export type Score = {
  id: string;
  prospect_id: string;
  value: number;
  band: string | null;
  rationale: string | null;
  signals: Record<string, unknown>;
  created_at: string;
};

export type Qualification = {
  id: string;
  prospect_id: string;
  conversation_id: string | null;
  outcome: string;
  //: O agente escreve `{"criteria": [{criterion, status, evidence}]}`. É a forma
  //: que o produtor usa; a tela lê essa, não uma inventada.
  criteria_results: { criteria?: Criterion[] } & Record<string, unknown>;
  rationale: string | null;
  confidence: number;
  created_at: string;
};

export type Criterion = {
  criterion: string;
  status: string;
  evidence?: string | null;
};

export type Meeting = {
  id: string;
  prospect_id: string;
  scheduled_at: string;
  duration_minutes: number;
  status: string;
  location: string | null;
  notes: string | null;
  created_at: string;
};

export type Contact = {
  id: string;
  company_id: string | null;
  full_name: string;
  email: string | null;
  phone: string | null;
  title: string | null;
  persona: string | null;
  seniority: string | null;
  linkedin_url: string | null;
  opted_out: boolean;
  attributes: Record<string, unknown>;
  created_at: string;
};

export type Company = {
  id: string;
  name: string;
  domain: string | null;
  industry: string | null;
  country: string | null;
  region: string | null;
  employee_count: number | null;
  revenue_band: string | null;
  description: string | null;
  attributes: Record<string, unknown>;
  created_at: string;
};

export type Research = {
  id: string;
  entity_type: string;
  entity_id: string;
  campaign_id: string | null;
  depth: string;
  summary: string | null;
  findings: Record<string, unknown>;
  sources: string[];
  created_at: string;
};

export type AuditEntry = {
  id: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  actor_user_id: string | null;
  actor_role: string | null;
  source: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type AgentConfig = {
  kind: string;
  name: string;
  model: string;
  instructions: string;
  max_output_tokens: number;
  is_active: boolean;
  /** `false` quando o que se vê é o padrão da plataforma, não uma escolha. */
  configured: boolean;
  units_per_run: number;
  default_name: string;
  default_model: string;
  default_instructions: string;
};

// --------------------------------------------------------- painel da plataforma
export type AdminTenant = {
  id: string;
  name: string;
  slug: string;
  plan: string;
  subscription_status: string;
  is_active: boolean;
  users: number;
  campaigns: number;
  ai_units_this_month: number;
  estimated_cost_usd: number;
  created_at: string;
  //: Os overrides como estão guardados, e o que vale hoje com eles aplicados.
  //: O PATCH substitui o dicionário inteiro, então a tela precisa mostrar o que
  //: já existe para não apagar em silêncio o que não reenviou.
  limit_overrides: Record<string, number>;
  effective_limits: Record<string, number | string[]>;
};

export type PlatformUsage = {
  period_start: string;
  tenants: number;
  active_tenants: number;
  total_units: number;
  estimated_cost_usd: number;
  by_kind: Record<string, { units: number; cost_usd: number }>;
};

export type SystemHealth = {
  agent_runs: Record<string, number>;
  recent_failures: {
    id: string;
    tenant_id: string;
    agent: string;
    status: string;
    error: string | null;
    created_at: string;
  }[];
};

/** A conexão com o RAVI, sem o token — nunca o token. */
export type CrmSettings = {
  configured: boolean;
  base_url: string | null;
  ravi_tenant_id: string | null;
  token_hint: string | null;
  status: string;
  last_error: string | null;
};

export type Sequence = {
  id: string;
  campaign_id: string;
  name: string;
  steps: { order?: number; wait_days?: number; instruction?: string }[];
  is_active: boolean;
  created_at: string;
  active_enrollments: number;
};

export type Enrollment = {
  id: string;
  sequence_id: string;
  prospect_id: string;
  status: string;
  current_step: number;
  next_run_at: string | null;
  last_step_at: string | null;
  stop_reason: string | null;
  created_at: string;
};
