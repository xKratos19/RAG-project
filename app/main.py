from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable
from uuid import UUID, uuid4

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import require_common_headers, require_ingest_headers
from app.config import settings
from app.errors import raise_error
from app.logging_utils import configure_logging, log_json, redact_payload
from app.models import (
    DeleteNamespaceResponse,
    EvalBlock,
    EvalRequest,
    EvalResponse,
    HealthStatus,
    IngestJob,
    IngestRequest,
    NamespaceStats,
    QueryRequest,
    QueryResponse,
)
from app.observability import REQUEST_COUNT, REQUEST_DURATION, VENDOR_COST, VENDOR_TOKENS, metrics_response, query_timings
from app.services_ingest import _post_callback, create_or_reuse_job
from app.services_query import run_query
from app.store import store, utc_now

APP_STARTED_AT = datetime.now(timezone.utc)
OPENAPI_FILE = Path(__file__).resolve().parent.parent / "openapi.yaml"

configure_logging()


def _configure_otel(app: FastAPI) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument(engine=store.engine)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    _configure_otel(app)
    yield
    app.state.arq_pool.close()


app = FastAPI(title=settings.app_name, version=settings.app_version, openapi_url=None, docs_url="/docs", lifespan=lifespan)


@app.middleware("http")
async def observability_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    trace_id = f"trace-{uuid4().hex[:12]}"
    request_id = request.headers.get("X-Request-ID")
    body_repr: dict[str, Any] | None = None
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            raw = await request.body()
            if raw:
                body_repr = redact_payload(json.loads(raw.decode("utf-8")))
        except Exception:
            body_repr = {"_body_parse": "failed"}

    log_json(
        "request_received",
        method=request.method,
        path=request.url.path,
        request_id=request_id,
        trace_id=trace_id,
    )

    endpoint = request.url.path
    with REQUEST_DURATION.labels(endpoint=endpoint).time():
        response = await call_next(request)

    REQUEST_COUNT.labels(method=request.method, status=str(response.status_code), endpoint=endpoint).inc()

    # Echo X-Request-ID and emit vendor trace headers (§8)
    if request_id:
        response.headers["X-Request-ID"] = request_id
    response.headers["X-Vendor-Trace-ID"] = trace_id
    response.headers["X-Vendor-Retrieval-Strategy"] = "hybrid_rrf_v2"

    # Real Server-Timing from run_query via ContextVar (§8)
    timings = query_timings.get({})
    if timings:
        st = ", ".join(f"{k};dur={v}" for k, v in timings.items() if v > 0)
        response.headers["Server-Timing"] = st or "total;dur=0"
    else:
        response.headers["Server-Timing"] = "total;dur=0"

    # Advertise rate limits (§2, IETF draft RateLimit headers)
    response.headers["RateLimit-Limit"] = "600"
    response.headers["RateLimit-Policy"] = "600;w=60"

    log_json(
        "request_completed",
        method=request.method,
        path=request.url.path,
        request_id=request_id,
        trace_id=trace_id,
        status_code=response.status_code,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = request.headers.get("X-Request-ID")
    request_id = UUID(rid) if rid else uuid4()
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": str(exc), "request_id": str(request_id), "details": None}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "invalid_request", "message": str(exc.detail), "request_id": str(uuid4()), "details": None}},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    rid = request.headers.get("X-Request-ID", str(uuid4()))
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": "Request body/headers failed schema validation.", "request_id": rid, "details": {"validation": exc.errors()}}},
    )


@app.post("/v1/query", response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    auth_ctx: Annotated[tuple[UUID, str], Depends(require_common_headers)],
) -> QueryResponse:
    request_id, tenant_id = auth_ctx
    response = await run_query(request_id, tenant_id, payload)
    VENDOR_TOKENS.labels(direction="input").inc(response.usage.input_tokens)
    VENDOR_TOKENS.labels(direction="output").inc(response.usage.output_tokens)
    VENDOR_COST.inc(response.usage.cost_usd)
    return response


@app.post("/v1/ingest", response_model=IngestJob, status_code=202)
async def ingest_json(
    request: Request,
    auth_ctx: Annotated[tuple[UUID, str, UUID], Depends(require_ingest_headers)],
    file: UploadFile | None = File(default=None),
) -> IngestJob:
    request_id, tenant_id, idem_key = auth_ctx
    arq_pool = request.app.state.arq_pool
    ctype = request.headers.get("content-type", "")

    if "multipart/form-data" in ctype:
        form = await request.form()
        payload_str = form.get("payload")
        if payload_str is None:
            raise_error(400, "invalid_request", "Missing payload field in multipart request.", request_id)
        payload = IngestRequest.model_validate(json.loads(str(payload_str)))
        if file is None:
            raise_error(400, "invalid_request", "Missing file in multipart request.", request_id)
        file_bytes, file_content_type = await _read_and_validate_file(file, request_id)
    else:
        try:
            body = await request.json()
        except Exception:
            raise_error(400, "invalid_request", "Request body is missing or not valid JSON.", request_id)
        payload = IngestRequest.model_validate(body)
        file_bytes, file_content_type = None, None
        if file is not None:
            file_bytes, file_content_type = await _read_and_validate_file(file, request_id)

    return await create_or_reuse_job(
        arq_pool, tenant_id, request_id, idem_key, payload,
        file_bytes=file_bytes, file_content_type=file_content_type,
    )


async def _read_and_validate_file(
    upload: UploadFile, request_id: UUID
) -> tuple[bytes, str]:
    body = await upload.read()
    if len(body) > settings.max_file_size_mib * 1024 * 1024:
        raise_error(413, "payload_too_large", "Uploaded file exceeds 50 MiB limit.", request_id)
    mime = (upload.content_type or "").split(";")[0].strip()
    if mime not in settings.allowed_mime_set:
        raise_error(415, "unsupported_media_type", "Unsupported MIME type.", request_id)
    return body, mime


@app.get("/v1/ingest/{job_id}", response_model=IngestJob)
async def ingest_status(
    job_id: str,
    response: Response,
    _x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> IngestJob:
    if not authorization or not authorization.startswith("Bearer "):
        raise_error(401, "unauthorized", "Missing bearer token.", uuid4())
    job = store.get_job(job_id)
    if not job:
        raise_error(404, "not_found", "Ingest job not found.", uuid4(), {"job_id": job_id})
    if not store.is_job_owned_by_tenant(job_id, _x_tenant_id):
        raise_error(403, "forbidden", "Tenant mismatch for ingest job.", uuid4(), {"job_id": job_id})
    if job.status not in {"done", "failed", "cancelled"}:
        response.headers["Retry-After"] = "5" if job.status == "queued" else "10"
    return job


@app.delete("/v1/namespaces/{namespace_id}/sources/{source_id}", status_code=204)
async def delete_source(
    namespace_id: str,
    source_id: str,
    auth_ctx: Annotated[tuple[UUID, str], Depends(require_common_headers)],
) -> Response:
    request_id, tenant_id = auth_ctx
    deleted = store.delete_source(tenant_id, namespace_id, source_id)
    if deleted == 0:
        raise_error(404, "not_found", "Source not found.", request_id, {"namespace_id": namespace_id, "source_id": source_id})
    return Response(status_code=204)


@app.delete("/v1/namespaces/{namespace_id}", status_code=202, response_model=DeleteNamespaceResponse)
async def delete_namespace(
    namespace_id: str,
    auth_ctx: Annotated[tuple[UUID, str], Depends(require_common_headers)],
) -> DeleteNamespaceResponse:
    _, tenant_id = auth_ctx
    callback_urls = store.get_namespace_callback_urls(tenant_id, namespace_id)
    store.delete_namespace(tenant_id, namespace_id)
    job_id = f"del_{uuid4().hex[:10]}"
    store.mark_namespace_delete_job(job_id)

    # Fire namespace.deleted webhook to all callback URLs known for this namespace (§5)
    now_iso = utc_now().isoformat()
    for url in callback_urls:
        await _post_callback(url, {
            "event": "namespace.deleted",
            "job_id": job_id,
            "namespace_id": namespace_id,
            "status": "queued",
            "at": now_iso,
        })

    return DeleteNamespaceResponse(job_id=job_id, status="queued", sla="24h")


@app.get("/v1/namespaces/{namespace_id}/stats", response_model=NamespaceStats)
async def namespace_stats(
    namespace_id: str,
    auth_ctx: Annotated[tuple[UUID, str], Depends(require_common_headers)],
) -> NamespaceStats:
    request_id, tenant_id = auth_ctx
    stats = store.get_namespace_stats(tenant_id, namespace_id)
    if not stats:
        raise_error(404, "namespace_not_found", "Namespace has no indexed content.", request_id, {"namespace_id": namespace_id})
    return stats


@app.get("/v1/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    return HealthStatus(
        status="ok",
        version=settings.app_version,
        uptime_seconds=store.get_uptime_seconds(APP_STARTED_AT),
        dependencies={"vector_store": "ok", "llm": "ok", "object_store": "ok"},
    )


@app.get("/v1/openapi.json")
async def openapi_contract() -> Any:
    if not OPENAPI_FILE.exists():
        return {"error": "openapi file missing"}
    import yaml
    return yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))


@app.get("/metrics")
async def metrics() -> Response:
    return metrics_response()


@app.post("/v1/eval", response_model=EvalResponse)
async def eval_query(
    payload: EvalRequest,
    auth_ctx: Annotated[tuple[UUID, str], Depends(require_common_headers)],
) -> EvalResponse:
    base = await run_query(auth_ctx[0], auth_ctx[1], payload)
    actual_ids = [str(c.chunk.chunk_id) for c in base.citations]
    expected = set(payload.expected_citations)
    hit = sum(1 for cid in actual_ids if cid in expected)
    precision = hit / max(len(actual_ids), 1)
    text_lower = (base.answer or "").lower()
    kw_hit = sum(1 for k in payload.expected_answer_keywords if k.lower() in text_lower)
    kw_rate = kw_hit / max(len(payload.expected_answer_keywords), 1)
    return EvalResponse(**base.model_dump(), eval=EvalBlock(citation_precision_at_k=precision, keyword_match_rate=kw_rate))


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok"}
