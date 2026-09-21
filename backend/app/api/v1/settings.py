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
from app.services import ai_credentials, audit, email_accounts
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
