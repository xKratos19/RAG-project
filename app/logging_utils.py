from __future__ import annotations

import json
import logging
from typing import Any

LOGGER = logging.getLogger("citydock.rag")
SENSITIVE_KEYS = {"question", "conversation_history", "citations", "content"}


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _otel_ids() -> tuple[str, str]:
    """Return (trace_id, span_id) from the current OpenTelemetry span, or zeros."""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        pass
    return "0" * 32, "0" * 16


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if key in SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


def log_json(event: str, **fields: Any) -> None:
    trace_id, span_id = _otel_ids()
    body = {
        "event": event,
        "trace_id": fields.pop("trace_id", trace_id),
        "span_id": fields.pop("span_id", span_id),
        **fields,
    }
    LOGGER.info(json.dumps(body, ensure_ascii=False))
