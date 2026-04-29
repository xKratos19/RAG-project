from uuid import UUID

from fastapi import Header

from app.config import settings
from app.errors import raise_error


def _validate_auth_header(authorization: str | None, request_id: UUID) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise_error(401, "unauthorized", "Missing or invalid bearer token.", request_id)
    provided = authorization.replace("Bearer ", "", 1).strip()
    if provided != settings.api_key:
        raise_error(401, "unauthorized", "Invalid API key.", request_id)


def require_common_headers(
    x_request_id: UUID = Header(..., alias="X-Request-ID"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> tuple[UUID, str]:
    _validate_auth_header(authorization, x_request_id)
    return x_request_id, x_tenant_id


def require_ingest_headers(
    x_request_id: UUID = Header(..., alias="X-Request-ID"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> tuple[UUID, str, UUID]:
    _validate_auth_header(authorization, x_request_id)
    return x_request_id, x_tenant_id, idempotency_key
