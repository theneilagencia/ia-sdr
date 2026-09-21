"""Erros de domínio mapeados para respostas HTTP em app.api.errors."""


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_error"


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class LimitExceeded(AppError):
    status_code = 402
    code = "limit_exceeded"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class CrossTenantAccess(AppError):
    """Levantado quando um objeto de outro tenant entra no contexto.

    Nunca deve acontecer: o RLS é a primeira barreira, esta é a segunda.
    """

    status_code = 403
    code = "cross_tenant_access"


class TenantContextMissing(AppError):
    status_code = 500
    code = "tenant_context_missing"
