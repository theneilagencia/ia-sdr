"""Configurações da empresa — a tela que um leigo precisa conseguir usar.

Duas coisas se configuram aqui, e as duas são segredo do cliente: a chave da
Anthropic e a conta de email de onde sai a prospecção. As duas seguem a mesma
regra: entram cifradas, saem mascaradas, e existe um botão de testar para a
pessoa saber que funcionou antes de descobrir do jeito ruim.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.client import validate_key
from app.api.deps import get_db, require
from app.api.v1 import schemas
from app.db.models.platform import Tenant
from app.rbac.roles import Permission
from app.services import ai_credentials, audit, email_accounts, ravi, retencao
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/ai", response_model=schemas.AISettingsResponse)
def get_ai_settings(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_READ)),
    db: Session = Depends(get_db),
):
    return ai_credentials.describe(db, ctx.tenant_id)


@router.put("/ai", response_model=schemas.AISettingsResponse)
def set_ai_key(
    payload: schemas.AIKeyUpdate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Salva a chave da empresa, testando antes.

    Testar antes de salvar é o que transforma "não funcionou e não sei por quê"
    em uma frase que a pessoa entende, no momento em que ela ainda está olhando
    para o campo.
    """
    valida, erro = validate_key(payload.api_key)
    if not valida:
        raise ai_credentials.AIKeyInvalid(erro or "Chave inválida")

    integracao = ai_credentials.store(
        db, ctx.tenant_id, api_key=payload.api_key, created_by=ctx.user_id
    )
    audit.record(
        db,
        action="ai_key.configured",
        resource_type="integration",
        resource_id=integracao.id,
        # Só a dica; a chave nunca entra no log de auditoria.
        payload={"key_hint": integracao.config.get("key_hint")},
        context=ctx,
    )
    return ai_credentials.describe(db, ctx.tenant_id)


@router.post("/ai/test", response_model=schemas.ConnectionTestResult)
def test_ai_key(
    payload: schemas.AIKeyUpdate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
):
    """Testa sem salvar — a checagem custa zero token."""
    valida, erro = validate_key(payload.api_key)
    return schemas.ConnectionTestResult(
        ok=valida,
        message=(
            "Chave válida. A Anthropic respondeu."
            if valida
            else erro or "Não foi possível validar a chave."
        ),
    )


@router.delete("/ai", response_model=schemas.AISettingsResponse)
def delete_ai_key(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    ai_credentials.remove(db, ctx.tenant_id)
    audit.record(
        db,
        action="ai_key.removed",
        resource_type="integration",
        context=ctx,
    )
    return ai_credentials.describe(db, ctx.tenant_id)


# ------------------------------------------------------------------ email
# ------------------------------------------------------------------ CRM (RAVI)
@router.get("/crm", response_model=schemas.CrmSettingsResponse)
def get_crm_settings(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_READ)),
    db: Session = Depends(get_db),
):
    return ravi.describe(db, ctx.tenant_id)


@router.post("/crm/test", response_model=schemas.ConnectionTestResult)
def test_crm(
    payload: schemas.CrmSettingsUpdate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Bate no RAVI com os dados da tela, sem salvar e sem escrever nada lá."""
    ok, mensagem = ravi.test_connection(
        base_url=payload.base_url,
        token=payload.token,
        ravi_tenant_id=payload.ravi_tenant_id,
    )
    return schemas.ConnectionTestResult(ok=ok, message=mensagem)


@router.put("/crm", response_model=schemas.CrmSettingsResponse)
def set_crm_settings(
    payload: schemas.CrmSettingsUpdate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Salva a conexão com o RAVI, testando antes.

    Salvar sem testar deixaria a empresa achando que o CRM está ligado enquanto
    a fila acumula falha em silêncio.
    """
    ok, mensagem = ravi.test_connection(
        base_url=payload.base_url,
        token=payload.token,
        ravi_tenant_id=payload.ravi_tenant_id,
    )
    if not ok:
        raise ravi.RaviConfigInvalid(mensagem)

    integracao = ravi.store(
        db,
        ctx.tenant_id,
        base_url=payload.base_url,
        token=payload.token,
        ravi_tenant_id=payload.ravi_tenant_id,
        default_stage=payload.default_stage,
        created_by=ctx.user_id,
    )
    audit.record(
        db,
        action="crm.configured",
        resource_type="integration",
        resource_id=integracao.id,
        # A dica do token, nunca o token.
        payload={"token_hint": integracao.config.get("token_hint")},
        context=ctx,
    )
    return ravi.describe(db, ctx.tenant_id)


@router.delete("/crm", response_model=schemas.CrmSettingsResponse)
def delete_crm_settings(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Desliga a integração. Devolve o estado vazio, como os outros deletes
    desta tela, para a tela redesenhar sem uma segunda chamada."""
    ravi.remove(db, ctx.tenant_id)
    audit.record(db, action="crm.removed", resource_type="integration", context=ctx)
    return ravi.describe(db, ctx.tenant_id)


@router.get("/email/presets", response_model=list[schemas.EmailPreset])
def email_presets(ctx: TenantContext = Depends(require(Permission.INTEGRATION_READ))):
    """Os provedores que a tela oferece, com host, porta e o que fazer antes."""
    return email_accounts.presets_payload()


@router.get("/email", response_model=schemas.EmailAccountResponse)
def get_email_account(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_READ)),
    db: Session = Depends(get_db),
):
    return email_accounts.describe(db, ctx.tenant_id)


@router.post("/email/test", response_model=schemas.ConnectionTestResult)
def test_email_account(
    payload: schemas.EmailAccountUpdate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
):
    """Conecta e autentica sem enviar nada para ninguém."""
    host, port = email_accounts.resolve(payload.provider, payload.host, payload.port)
    ok, mensagem = email_accounts.test_connection(
        host=host,
        port=port,
        username=payload.username or str(payload.from_email),
        password=payload.password,
    )
    return schemas.ConnectionTestResult(ok=ok, message=mensagem)


@router.put("/email", response_model=schemas.EmailAccountResponse)
def set_email_account(
    payload: schemas.EmailAccountUpdate,
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    """Salva a conta, testando antes.

    Salvar uma configuração que não conecta só adia a descoberta do erro para
    o momento em que uma campanha já está rodando.
    """
    host, port = email_accounts.resolve(payload.provider, payload.host, payload.port)
    ok, mensagem = email_accounts.test_connection(
        host=host,
        port=port,
        username=payload.username or str(payload.from_email),
        password=payload.password,
    )
    if not ok:
        raise email_accounts.EmailConfigInvalid(mensagem)

    integracao = email_accounts.store(
        db,
        ctx.tenant_id,
        provider=payload.provider,
        from_email=str(payload.from_email),
        from_name=payload.from_name,
        username=payload.username,
        password=payload.password,
        host=payload.host,
        port=payload.port,
        imap_host=payload.imap_host,
        imap_port=payload.imap_port,
        created_by=ctx.user_id,
    )
    audit.record(
        db,
        action="email_account.configured",
        resource_type="integration",
        resource_id=integracao.id,
        # Provedor e remetente; a senha nunca entra no log.
        payload={"provider": payload.provider, "from_email": str(payload.from_email)},
        context=ctx,
    )
    return email_accounts.describe(db, ctx.tenant_id)


@router.delete("/email", response_model=schemas.EmailAccountResponse)
def delete_email_account(
    ctx: TenantContext = Depends(require(Permission.INTEGRATION_WRITE)),
    db: Session = Depends(get_db),
):
    email_accounts.remove(db, ctx.tenant_id)
    audit.record(db, action="email_account.removed", resource_type="integration", context=ctx)
    return email_accounts.describe(db, ctx.tenant_id)


# ------------------------------------------------------------------ volume
POLICY_KEY = "sending_policy"


@router.get("/sending", response_model=schemas.SendingPolicy)
def get_sending_policy(
    ctx: TenantContext = Depends(require(Permission.TENANT_READ)),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, ctx.tenant_id)
    return schemas.SendingPolicy(**((tenant.settings or {}).get(POLICY_KEY) or {}))


@router.put("/sending", response_model=schemas.SendingPolicy)
def set_sending_policy(
    payload: schemas.SendingPolicy,
    ctx: TenantContext = Depends(require(Permission.TENANT_WRITE)),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, ctx.tenant_id)
    tenant.settings = {**(tenant.settings or {}), POLICY_KEY: payload.model_dump()}
    audit.record(
        db,
        action="sending_policy.updated",
        resource_type="tenant",
        resource_id=ctx.tenant_id,
        payload=payload.model_dump(),
        context=ctx,
    )
    return payload


# --------------------------------------------------------------- retenção
#
# Fica nesta tela, e não no painel da plataforma, porque o dado é do cliente: é
# a empresa que decide por quanto tempo guarda o rastro do próprio trabalho.


@router.get("/retention", response_model=schemas.RetentionPolicy)
def get_retention_policy(
    ctx: TenantContext = Depends(require(Permission.TENANT_READ)),
    db: Session = Depends(get_db),
):
    pol = retencao.politica(db.get(Tenant, ctx.tenant_id))
    return schemas.RetentionPolicy(days=pol.dias, include_cold_prospects=pol.leads_frios)


@router.get("/retention/preview", response_model=schemas.RetentionPreview)
def preview_retention(
    days: int | None = None,
    include_cold_prospects: bool | None = None,
    ctx: TenantContext = Depends(require(Permission.TENANT_READ)),
    db: Session = Depends(get_db),
):
    """O que sairia hoje, por classe, sem apagar nada.

    Aceita prazo por parâmetro para a tela poder responder "e se eu puser 90
    dias?" **antes** de salvar. Apagar não tem desfazer, e ver "5.312 mensagens"
    é o que faz alguém reler o número antes de confirmar.
    """
    tenant = db.get(Tenant, ctx.tenant_id)
    atual = retencao.politica(tenant)
    hipotetica = retencao.Politica(
        dias=atual.dias if days is None else days,
        leads_frios=(
            atual.leads_frios if include_cold_prospects is None else include_cold_prospects
        ),
    )
    # A simulação não pode gravar a política: quem pediu uma previsão não pediu
    # para mudar nada. Guarda o que estava, calcula, devolve ao lugar.
    guardado = tenant.settings
    try:
        retencao.salvar(tenant, hipotetica)
        db.flush()
        contagens = retencao.previsao(db, ctx.tenant_id)
    finally:
        tenant.settings = guardado
        db.flush()
    return schemas.RetentionPreview(
        days=hipotetica.dias,
        include_cold_prospects=hipotetica.leads_frios,
        counts=contagens,
    )


@router.put("/retention", response_model=schemas.RetentionPolicy)
def set_retention_policy(
    payload: schemas.RetentionPolicy,
    ctx: TenantContext = Depends(require(Permission.TENANT_WRITE)),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, ctx.tenant_id)
    retencao.salvar(
        tenant,
        retencao.Politica(dias=payload.days, leads_frios=payload.include_cold_prospects),
    )
    # Auditar é especialmente importante aqui: é a configuração que faz dado
    # desaparecer sozinho, e "desde quando está assim?" vira pergunta um dia.
    audit.record(
        db,
        action="retention_policy.updated",
        resource_type="tenant",
        resource_id=ctx.tenant_id,
        payload=payload.model_dump(),
        context=ctx,
    )
    return payload
