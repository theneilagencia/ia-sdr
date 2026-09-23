"""Contratos de entrada e saída da API.

Nada que seja segredo de cliente é serializado aqui — credenciais de
integração não têm campo de leitura, de propósito.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models.platform import Plan
from app.rbac.roles import Role


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=200)
    tenant_slug: str | None = Field(default=None, pattern=r"^[a-z0-9]([a-z0-9-]{0,58}[a-z0-9])?$")
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: str | None = None
    #: O código do aplicativo autenticador, ou um de recuperação. Opcional no
    #: esquema porque a maioria das contas não tem segundo fator; quando tem, a
    #: ausência é recusada com `mfa_required` — depois de a senha ser conferida.
    code: str | None = Field(default=None, max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class MemberUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    tenant_id: uuid.UUID | None
    role: str | None


class MfaState(BaseModel):
    """O estado do segundo fator. Nunca o segredo."""

    enabled: bool
    enabled_at: datetime | None = None
    #: Segredo gerado e ainda não confirmado — a tela usa para oferecer "conclua
    #: a configuração" em vez de começar de novo e trocar o segredo por acidente.
    pending: bool = False
    recovery_codes_left: int = 0


class MfaSetupResponse(BaseModel):
    """O segredo e o link, mostrados **uma vez**.

    Depois disto o banco só tem a versão cifrada, e a listagem de estado não
    devolve nenhum dos dois. Quem perdeu a tela desativa e configura de novo.
    """

    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=4, max_length=64)


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=4, max_length=64)


class MfaRecoveryCodes(BaseModel):
    """Os códigos de recuperação, mostrados **uma vez**, em hash no banco."""

    recovery_codes: list[str]


class MembershipInfo(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    role: Role


class MeResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    is_platform_admin: bool
    tenant_id: uuid.UUID
    role: Role
    permissions: list[str]
    memberships: list[MembershipInfo]


class SwitchTenantRequest(BaseModel):
    tenant_id: uuid.UUID


# ---------------------------------------------------------------- tenant
class TenantResponse(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: Plan
    subscription_status: str
    trial_ends_at: datetime | None
    settings: dict
    created_at: datetime


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    settings: dict | None = None


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: Role
    is_active: bool


class InvitationCreate(BaseModel):
    email: EmailStr
    role: Role = Role.OPERATOR


class InvitationResponse(BaseModel):
    """Um convite pendente, como a tela de equipe o mostra.

    Não há campo dizendo se aquele email já tem conta na plataforma — era
    justamente o que a criação direta de membro devolvia, e era enumeração da
    base de usuários entregue a quem convida.
    """

    id: uuid.UUID
    email: EmailStr
    role: Role
    expires_at: datetime
    created_at: datetime


class InvitationCreated(InvitationResponse):
    #: O link aparece **uma vez**, na resposta da criação. Guardamos o hash do
    #: token, não o token: um convite pendente lido no banco não abre porta.
    #: Quem convida copia e entrega pelo canal que quiser.
    accept_url: str


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    #: Sem mínimo aqui de propósito. Conta nova exige dez caracteres, e isso é
    #: verificado no serviço — que devolve a **mesma** recusa de token inválido.
    #: Um 422 de tamanho diferenciaria "este email já tem conta" de "não tem", e
    #: seria a enumeração voltando pela porta de trás.
    password: str = Field(min_length=1, max_length=128)
    full_name: str = Field(default="", max_length=200)
    #: Aceitar um convite emite sessão, então precisa do mesmo segundo fator que
    #: o login. Sem isto, o convite seria a porta que contorna o MFA: bastava
    #: convidar o email de alguém, saber a senha e entrar sem o código.
    code: str | None = Field(default=None, max_length=64)


# ------------------------------------------------------- base de conhecimento
class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    source_type: str = Field(default="paste", max_length=40)
    source_uri: str | None = Field(default=None, max_length=1000)
    #: Vazio significa "vale para a empresa inteira". Com lista, o documento só
    #: entra no contexto das campanhas listadas.
    campaign_ids: list[uuid.UUID] = Field(default_factory=list)


class KnowledgeDocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    source_type: str
    source_uri: str | None
    mime_type: str | None
    status: str
    campaign_ids: list[uuid.UUID]
    char_count: int
    chunk_count: int
    error: str | None
    created_at: datetime


class KnowledgeChunkResponse(BaseModel):
    id: uuid.UUID
    ordinal: int
    content: str
    token_count: int


class KnowledgeDocumentDetail(KnowledgeDocumentResponse):
    chunks: list[KnowledgeChunkResponse]


class KnowledgeSearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    ordinal: int
    content: str
    relevance: float
    excerpt: str | None


# ---------------------------------------------------------------- campaign
class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,118}[a-z0-9])?$")
    objective: str | None = None
    icp: dict = Field(default_factory=dict)
    target_geography: list[str] = Field(default_factory=list)
    personas: list[dict] = Field(default_factory=list)
    offer: dict = Field(default_factory=dict)
    messaging: dict = Field(default_factory=dict)
    qualification_criteria: dict = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=lambda: ["email"])
    daily_limits: dict = Field(default_factory=dict)


class CampaignUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    objective: str | None = None
    icp: dict | None = None
    target_geography: list[str] | None = None
    personas: list[dict] | None = None
    offer: dict | None = None
    messaging: dict | None = None
    qualification_criteria: dict | None = None
    channels: list[str] | None = None
    daily_limits: dict | None = None


class CampaignResponse(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    status: str
    objective: str | None
    icp: dict
    target_geography: list
    personas: list
    offer: dict
    messaging: dict
    qualification_criteria: dict
    channels: list
    daily_limits: dict
    created_at: datetime


# ---------------------------------------------------------------- contas-alvo
class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    domain: str | None = Field(default=None, max_length=255)
    industry: str | None = None
    country: str | None = None
    region: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    revenue_band: str | None = None
    linkedin_url: str | None = None
    description: str | None = None
    attributes: dict = Field(default_factory=dict)


class CompanyResponse(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    domain: str | None
    industry: str | None
    country: str | None
    region: str | None
    employee_count: int | None
    revenue_band: str | None
    description: str | None
    attributes: dict
    created_at: datetime


class CompanyUpdate(BaseModel):
    """Correção de conta-alvo. Tudo opcional: só o que vem é alterado.

    Não existe apagar conta-alvo, de propósito: a empresa está amarrada a
    prospects, pesquisa e conversas, e apagá-la levaria o histórico embora. O que
    se corrige é o dado errado — nome, domínio, porte —, que é o que realmente
    acontece depois de um import de planilha.
    """

    name: str | None = Field(default=None, min_length=1, max_length=300)
    domain: str | None = Field(default=None, max_length=255)
    industry: str | None = None
    country: str | None = None
    region: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    revenue_band: str | None = None
    linkedin_url: str | None = None
    description: str | None = None
    attributes: dict | None = None


class ResearchResponse(ORMModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    campaign_id: uuid.UUID | None
    depth: str
    summary: str | None
    findings: dict
    sources: list
    created_at: datetime


# ---------------------------------------------------------------- pessoas e funil
class ContactCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    company_id: uuid.UUID | None = None
    email: EmailStr | None = None
    phone: str | None = None
    title: str | None = None
    seniority: str | None = None
    persona: str | None = None
    linkedin_url: str | None = None
    timezone: str | None = None
    attributes: dict = Field(default_factory=dict)


class ContactResponse(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    full_name: str
    email: str | None
    title: str | None
    persona: str | None
    opted_out: bool
    created_at: datetime


class ContactUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    company_id: uuid.UUID | None = None
    email: EmailStr | None = None
    phone: str | None = None
    title: str | None = None
    seniority: str | None = None
    persona: str | None = None
    linkedin_url: str | None = None
    timezone: str | None = None
    attributes: dict | None = None
    #: Só pode virar `true`. Desmarcar descadastro pela API seria a forma mais
    #: fácil de voltar a escrever para quem pediu para não receber mais — e é a
    #: única coisa aqui que gera multa.
    opted_out: Literal[True] | None = None


class ContactDetail(ContactResponse):
    """O contato inteiro. A resposta de lista é enxuta de propósito."""

    phone: str | None
    seniority: str | None
    linkedin_url: str | None
    timezone: str | None
    attributes: dict


class ProspectImportItem(BaseModel):
    """Uma linha do import: empresa e pessoa juntas, como vem de uma lista."""

    company_name: str = Field(min_length=1, max_length=300)
    company_domain: str | None = None
    industry: str | None = None
    country: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    title: str | None = None
    persona: str | None = None
    linkedin_url: str | None = None


class ProspectImportRequest(BaseModel):
    campaign_id: uuid.UUID
    items: list[ProspectImportItem] = Field(min_length=1, max_length=500)
    source: str = "manual"


class ProspectImportResult(BaseModel):
    imported: int
    duplicates: int
    prospect_ids: list[uuid.UUID]


class ProspectCsvResult(ProspectImportResult):
    rows_read: int
    #: Linhas que ficaram de fora, com o número da linha no arquivo. Import que
    #: diz "42 importados" e engole oito linhas é pior do que import que falha.
    row_errors: list[dict]
    #: Linhas que entraram sem email. Não é erro — lista de LinkedIn é assim —
    #: mas o agente de abordagem se recusa a escrever para quem não tem
    #: endereço, e é melhor saber disso no import do que no disparo.
    missing_email: int = 0


class ScoreResponse(ORMModel):
    id: uuid.UUID
    prospect_id: uuid.UUID
    value: float
    band: str | None
    rationale: str | None
    signals: dict
    created_at: datetime


class ProspectResponse(ORMModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    contact_id: uuid.UUID
    company_id: uuid.UUID | None
    status: str
    source: str | None
    last_activity_at: datetime | None
    created_at: datetime


class ProspectListItem(ProspectResponse):
    """O prospect com nome de gente.

    A listagem crua devolve três UUIDs — contato, empresa, campanha — e nenhuma
    tela consegue pedir "pesquise este aqui" com isso. Os nomes vêm resolvidos
    em uma consulta para a lista inteira, como na caixa de entrada.
    """

    contact_name: str | None = None
    contact_email: str | None = None
    company_name: str | None = None
    campaign_name: str | None = None


class InboundMessageCreate(BaseModel):
    """Resposta recebida do lead.

    Hoje entra por aqui; no Sprint 3 entra pelo webhook do provedor de email,
    que chama exatamente este caminho.
    """

    body: str = Field(min_length=1)
    subject: str | None = None
    external_message_id: str | None = None


class MessageResponse(ORMModel):
    """Rascunho ou mensagem trocada. `metrics` guarda os ganchos usados."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    direction: str
    status: str
    channel: str
    subject: str | None
    body: str
    sent_at: datetime | None
    metrics: dict
    created_at: datetime


# ------------------------------------------------------------------ conversa
class ConversationResponse(BaseModel):
    id: uuid.UUID
    prospect_id: uuid.UUID
    campaign_id: uuid.UUID | None
    channel: str
    subject: str | None
    status: str
    last_message_at: datetime | None
    handoff_to_user_id: uuid.UUID | None
    created_at: datetime
    #: Quem é o lead, resolvido aqui: a tela de conversas sem nome do contato
    #: obrigaria uma chamada por linha.
    contact_name: str | None = None
    contact_email: str | None = None
    company_name: str | None = None
    message_count: int = 0
    awaiting_reply: bool = False


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse]


class ConversationUpdate(BaseModel):
    status: Literal["open", "replied", "closed", "handed_off"] | None = None
    #: `null` devolve a conversa para o agente; um id passa para a pessoa.
    handoff_to_user_id: uuid.UUID | None = None
    clear_handoff: bool = False


class ConversationReplyCreate(BaseModel):
    body: str = Field(min_length=1)
    subject: str | None = Field(default=None, max_length=500)


# ----------------------------------------------------------------- cadência
class SequenceStep(BaseModel):
    """Um toque. `wait_days` conta a partir do toque anterior."""

    instruction: str = Field(min_length=1, max_length=2000)
    wait_days: int = Field(default=3, ge=0, le=90)
    order: int | None = None


class SequenceCreate(BaseModel):
    campaign_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    steps: list[SequenceStep] = Field(min_length=1)
    is_active: bool = True


class SequenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    steps: list[SequenceStep] | None = None
    is_active: bool | None = None


class SequenceResponse(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    name: str
    steps: list[dict]
    is_active: bool
    created_at: datetime
    active_enrollments: int = 0


class EnrollRequest(BaseModel):
    prospect_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence_id: uuid.UUID
    prospect_id: uuid.UUID
    status: str
    current_step: int
    next_run_at: datetime | None
    last_step_at: datetime | None
    stop_reason: str | None
    created_at: datetime


class EnrollResult(BaseModel):
    enrolled: list[EnrollmentResponse]
    #: Quem não entrou e por quê. Selecionar cem leads e perder a operação
    #: inteira porque três já estavam na cadência seria hostil.
    skipped: list[dict]


class SequenceTickResult(BaseModel):
    gerados: int
    parados: int
    concluidos: int
    adiados: int


class MeetingCreate(BaseModel):
    scheduled_at: datetime
    duration_minutes: int = Field(default=30, ge=5, le=480)
    location: str | None = None
    owner_user_id: uuid.UUID | None = None
    notes: str | None = None
    #: Manda o convite de calendário (`.ics`) para o lead. Desligado por padrão:
    #: quem registra uma reunião já combinada por telefone não quer disparar um
    #: convite que o outro lado não está esperando.
    send_invite: bool = False


class MeetingResponse(ORMModel):
    id: uuid.UUID
    prospect_id: uuid.UUID
    campaign_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    scheduled_at: datetime
    duration_minutes: int
    status: str
    location: str | None
    notes: str | None
    created_at: datetime
    #: Nulo significa "marcado aqui, e o outro lado não sabe" — que é a diferença
    #: entre reunião combinada e reunião anotada.
    invite_sent_at: datetime | None = None
    #: Preenchido quando a reunião foi criada mas o convite não saiu (conta de
    #: email não configurada, servidor fora, contato sem endereço). A reunião
    #: fica: perder o registro por causa do email seria trocar o que importa pelo
    #: acessório.
    invite_error: str | None = None


class QualificationResponse(ORMModel):
    id: uuid.UUID
    prospect_id: uuid.UUID
    conversation_id: uuid.UUID | None
    outcome: str
    criteria_results: dict
    rationale: str | None
    confidence: int
    created_at: datetime


class AllowanceResponse(BaseModel):
    """A cota do dia, com o motivo — para a tela explicar, não só recusar."""

    limit: int
    used: int
    remaining: int
    reason: str
    warmup_day: int | None
    within_business_hours: bool


class CrmSettingsResponse(BaseModel):
    """O que a tela mostra da conexão com o RAVI. Sem o token, nunca."""

    configured: bool
    base_url: str | None
    ravi_tenant_id: str | None
    token_hint: str | None
    status: str
    last_error: str | None


class CrmSettingsUpdate(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=2000)
    #: Vai no cabeçalho `x-tenant-id`: é como o RAVI sabe de quem é o lead.
    ravi_tenant_id: str = Field(min_length=1, max_length=200)
    #: Estágio inicial do funil do RAVI. Vazio deixa o RAVI decidir.
    default_stage: str | None = Field(default=None, max_length=200)


class CrmSyncResult(BaseModel):
    status: str
    lead_id: str | None = None
    reason: str | None = None


class FetchInboxResult(BaseModel):
    fetched: int
    recorded: int
    ignored: int
    #: Avisos de não entrega. Contá-los junto de "ignorados" esconderia o
    #: número que a operação precisa vigiar: lista comprada tem taxa de
    #: retorno alta, e é ela que queima o domínio.
    bounced: int = 0
    #: Mensagens que deram defeito no processamento. Continuam **não lidas** na
    #: caixa, para a próxima leitura tentar de novo — e aparecem aqui para não
    #: sumirem em silêncio.
    failed: int = 0


class SendQueuedResult(BaseModel):
    sent: int
    blocked: list[dict]
    allowance: dict


class MessageReject(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class FunnelResponse(BaseModel):
    """Os números da tela inicial: quantos entraram e até onde chegaram."""

    campaign_id: uuid.UUID | None
    prospects: int
    researched: int
    scored: int
    contacted: int
    engaged: int
    qualified: int
    meetings: int
    #: As saídas, no estado atual. Email que voltou é o número que queima o
    #: domínio de quem envia; sem ele na tela, ninguém vigia o que mais importa
    #: vigiar numa operação de email frio.
    bounced: int = 0
    #: Descadastro pelo link público, veredito do agente de qualificação ou
    #: encerramento pelo de conversa — os três param a cadência.
    disqualified: int = 0
    by_band: dict[str, int]


# ---------------------------------------------------------------- company brain
class CompanyBrainUpdate(BaseModel):
    legal_name: str | None = None
    website: str | None = None
    positioning: str | None = None
    products: list[dict] | None = None
    services: list[dict] | None = None
    icp: dict | None = None
    personas: list[dict] | None = None
    pricing: dict | None = None
    cases: list[dict] | None = None
    faqs: list[dict] | None = None
    objections: list[dict] | None = None
    competitors: list[dict] | None = None
    sales_playbook: dict | None = None
    brand_voice: dict | None = None
    ai_policies: dict | None = None


class CompanyBrainResponse(ORMModel):
    tenant_id: uuid.UUID
    legal_name: str | None
    website: str | None
    positioning: str | None
    products: list
    services: list
    icp: dict
    personas: list
    pricing: dict
    cases: list
    faqs: list
    objections: list
    competitors: list
    sales_playbook: dict
    brand_voice: dict
    ai_policies: dict
    updated_at: datetime


# ---------------------------------------------------------------- agentes
class AgentRunRequest(BaseModel):
    agent: str
    campaign_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    params: dict = Field(default_factory=dict)


class JobResponse(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    payload: dict
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: str | None
    created_at: datetime


class AgentRunResponse(ORMModel):
    id: uuid.UUID
    job_id: str
    agent_kind: str
    status: str
    campaign_id: uuid.UUID | None
    entity_id: uuid.UUID | None
    context_digest: str | None
    units: int
    output: dict
    error: str | None
    created_at: datetime


# ---------------------------------------------------------------- configurações
class AIKeyUpdate(BaseModel):
    api_key: str = Field(min_length=8, max_length=300)


class AISettingsResponse(BaseModel):
    """Estado da chave, sem a chave."""

    configured: bool
    key_hint: str | None
    status: str
    using_platform_key: bool
    updated_at: str | None


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str


class EmailPreset(BaseModel):
    provider: str
    label: str
    host: str
    port: int
    help: str


class EmailAccountUpdate(BaseModel):
    provider: Literal["gmail", "outlook", "smtp"] = "gmail"
    from_email: EmailStr
    from_name: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=300)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    #: Leitura das respostas. Gmail e Outlook têm padrão; servidor próprio não.
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)


class EmailAccountResponse(BaseModel):
    """Estado da conta, sem a senha."""

    configured: bool
    provider: str | None
    from_email: str | None
    from_name: str | None
    host: str | None
    port: int | None
    imap_host: str | None
    imap_port: int | None
    status: str
    last_error: str | None


class SendingPolicy(BaseModel):
    """Os freios de volume, na linguagem de quem opera.

    Existem para proteger a reputação do domínio de quem envia: provedor de
    email que vê volume novo e alto trata como spam, e recuperar reputação é
    muito mais caro do que subir devagar.
    """

    daily_limit: int = Field(default=30, ge=1, le=2000)
    warmup_enabled: bool = True
    warmup_start: int = Field(default=10, ge=1, le=500)
    warmup_daily_increment: int = Field(default=5, ge=1, le=100)
    business_hours_only: bool = True
    timezone: str = Field(default="America/Sao_Paulo", max_length=60)


# ---------------------------------------------------------------- integrações
class IntegrationCreate(BaseModel):
    provider: str
    account_ref: str
    display_name: str | None = None
    credentials: dict[str, Any]
    config: dict = Field(default_factory=dict)


class IntegrationResponse(BaseModel):
    """Sem campo de credencial. Nunca."""

    id: uuid.UUID
    provider: str
    account_ref: str
    display_name: str | None
    status: str
    config: dict
    created_at: datetime


# ---------------------------------------------------------------- auditoria e uso
class AuditLogResponse(ORMModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_role: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    source: str
    payload: dict
    created_at: datetime


# ---------------------------------------------------------------- configuração de agente
class AgentConfigResponse(BaseModel):
    """O que vale hoje para um agente, e de onde vem.

    `configured` distingue as duas coisas que a tela precisa dizer: "este é o
    padrão da plataforma" e "isto foi você quem escolheu". Sem essa diferença, o
    campo preenchido com o default parece decisão de alguém.
    """

    kind: str
    name: str
    model: str
    instructions: str
    max_output_tokens: int
    is_active: bool
    configured: bool
    units_per_run: int
    default_name: str
    default_model: str
    default_instructions: str


class AgentConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    #: Substitui as instruções base do agente. Vazio volta para o padrão — é o
    #: caminho de saída de quem escreveu uma instrução ruim e quer desfazer.
    instructions: str | None = Field(default=None, max_length=8_000)
    max_output_tokens: int | None = Field(default=None, ge=500, le=32_000)
    is_active: bool | None = None

    @field_validator("model")
    @classmethod
    def _modelo_conhecido(cls, valor: str | None) -> str | None:
        """Modelo fora da tabela de preços custaria pelo preço mais alto.

        A tabela é o que permite medir margem. Aceitar um nome qualquer aqui
        significaria gasto real medido pelo teto — ou, pior, uma chamada que a
        Anthropic recusa, descoberta no primeiro disparo do cliente.
        """
        if valor is None:
            return None
        from app.ai.pricing import PRICES

        if valor not in PRICES:
            aceitos = ", ".join(sorted(PRICES))
            raise ValueError(f"modelo desconhecido; os disponíveis são: {aceitos}")
        return valor


# ---------------------------------------------------------------- platform admin
class InvoiceResponse(BaseModel):
    """Uma fatura fechada, com o consumo que a sustenta."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str = ""
    period_year: int
    period_month: int
    status: str
    currency: str
    subscription_cents: int
    ai_units: int
    ai_units_included: int
    ai_units_over: int
    overage_cents: int
    #: A margem: o que a Anthropic cobrou de verdade. Não entra no total do
    #: cliente — é o número que diz se o contrato faz sentido.
    ai_cost_usd: float
    total_cents: int
    issued_at: datetime | None = None
    paid_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


class InvoiceCloseRequest(BaseModel):
    """Qual mês fechar, e para quem."""

    year: int = Field(ge=2024, le=2100)
    month: int = Field(ge=1, le=12)
    #: Vazio fecha todas as empresas ativas. É o uso normal: fechamento é
    #: operação de virada de mês, não de cliente em cliente.
    tenant_id: uuid.UUID | None = None


class InvoiceUpdate(BaseModel):
    #: `issued`, `paid` ou `void`. A ordem é verificada no serviço: pagar sem
    #: emitir seria cobrança que o cliente nunca recebeu aparecendo como quitada.
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AdminTenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    subscription_status: str
    is_active: bool
    users: int
    campaigns: int
    ai_units_this_month: int
    estimated_cost_usd: float
    created_at: datetime
    #: Os overrides contratuais, como estão guardados. Sem devolvê-los, um
    #: painel que edita limites edita no escuro — e o PATCH substitui o
    #: dicionário inteiro, então o que não fosse reenviado seria apagado em
    #: silêncio.
    limit_overrides: dict = Field(default_factory=dict)
    #: O que vale hoje: o limite do plano com os overrides aplicados. É o número
    #: que a cota usa, e é ele que responde "por que este cliente travou".
    effective_limits: dict = Field(default_factory=dict)
    #: O contrato. Zerado significa "ainda não precificado": o fechamento do mês
    #: continua fechando e mostra o consumo, só não cobra nada.
    contract_monthly_cents: int = 0
    contract_currency: str = "BRL"
    overage_cents_per_unit: int = 0


class AdminTenantUpdate(BaseModel):
    plan: Plan | None = None
    subscription_status: str | None = None
    is_active: bool | None = None
    limit_overrides: dict | None = None
    #: Em centavos, para não usar float com dinheiro. `ge=0` porque cobrança
    #: negativa é crédito, e crédito é outro documento — não um valor mensal
    #: com sinal invertido.
    contract_monthly_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    contract_currency: str | None = Field(default=None, min_length=3, max_length=3)
    overage_cents_per_unit: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("limit_overrides")
    @classmethod
    def _limites_sao_inteiros(cls, valor: dict | None) -> dict | None:
        """Override é número inteiro, e é aqui que isso se verifica.

        Não é preciosismo de tipo: o override entra direto na comparação de cota.
        Um `"muito"` salvo aqui não erra no painel — erra depois, em toda execução
        de agente daquela empresa, como erro 500 que ninguém liga a um campo
        digitado no suporte três dias antes. `-1` é ilimitado, e é o menor valor
        que faz sentido.
        """
        if valor is None:
            return None
        for chave, bruto in valor.items():
            if isinstance(bruto, bool) or not isinstance(bruto, int):
                raise ValueError(f"o limite de {chave} precisa ser um número inteiro")
            if bruto < -1:
                raise ValueError(f"o limite de {chave} não pode ser menor que -1")
        return valor
