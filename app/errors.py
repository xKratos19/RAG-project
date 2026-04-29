from typing import NoReturn

from fastapi import HTTPException, status
from uuid import UUID

from app.models import ErrorBody, ErrorEnvelope


def raise_error(
    http_status: int,
    code: str,
    message: str,
    request_id: UUID,
    details: dict[str, object] | None = None,
) -> NoReturn:
    payload = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id, details=details))
    raise HTTPException(status_code=http_status, detail=payload.model_dump(mode="json"))


ERROR_HTTP_MAP: dict[str, int] = {
    "invalid_request": status.HTTP_400_BAD_REQUEST,
    "unauthorized": status.HTTP_401_UNAUTHORIZED,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "not_found": status.HTTP_404_NOT_FOUND,
    "namespace_not_found": status.HTTP_404_NOT_FOUND,
    "duplicate_job": status.HTTP_409_CONFLICT,
    "payload_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "unsupported_media_type": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "validation_error": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
    "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "upstream_error": status.HTTP_502_BAD_GATEWAY,
    "service_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
}
