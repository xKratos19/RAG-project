from __future__ import annotations

import contextvars

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "status", "endpoint"],
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["endpoint"],
)
VENDOR_COST = Counter("vendor_cost_usd_total", "Accumulated model cost USD")
VENDOR_TOKENS = Counter("vendor_tokens_total", "Accumulated token usage", ["direction"])
VENDOR_EXTERNAL_ERRORS = Counter(
    "vendor_external_api_errors_total",
    "External provider errors",
    ["dependency", "error_type"],
)

# Per-request timing populated by run_query; read by the Server-Timing middleware.
query_timings: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar(
    "query_timings", default={}
)


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
