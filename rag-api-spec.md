# RAG Service — External API Specification

**Version:** `v1.0` · **Status:** Public vendor-facing specification · **Date:** 2026-04-22

## 0. Purpose

This document defines the HTTP contract between the Lex-Advisor platform
(caller) and any external RAG (Retrieval-Augmented Generation) service
(provider). Any implementation — commercial vendor, OSS fork
(Haystack / RAGFlow / LlamaIndex / etc.), or a bespoke build — that fully
conforms to this specification is a drop-in replacement for the caller's
RAG layer.

The provider owns: chunking, embeddings, vector + full-text indices,
retrieval, reranking, and final answer composition.

The caller owns: source management (`sources`, `agents`, `categories`),
admin UI, audit log, consent, conversation memory, Telegram/WhatsApp
transports.

## 1. Conventions

- **Base URL:** configured per-deployment via `RAG_API_URL` env var
  (e.g. `https://rag.partner.example/api`).
- **Transport:** HTTPS only, TLS ≥ 1.2.
- **Content-Type:** `application/json; charset=utf-8` unless noted.
- **Versioning:** URL-path (`/v1/...`). Breaking changes bump to `/v2/...`
  with ≥ 6-month overlap where both versions are served.
- **Region:** EU-only data residency (GDPR). Implementers SHOULD
  co-locate with `europe-west3` (Frankfurt) if possible.
- **Timestamps:** ISO 8601 UTC with `Z` suffix
  (`2026-04-22T13:45:00Z`).
- **IDs:** UUID v4 unless the spec says otherwise.
- **Namespace:** a logical isolation unit. The caller passes our
  `agents.slug` as the namespace ID (e.g. `cod_civil`, `legea_31_1990`,
  `primaria_balta_doamnei_local`). One tenant (cityhall) owns many
  namespaces; the provider MUST enforce hard isolation.

## 2. Authentication & Security

Every request MUST carry:

| Header | Type | Required | Purpose |
|---|---|---|---|
| `Authorization: Bearer <api_key>` | str | yes | Static, per-tenant API key. 256-bit entropy. Rotatable via admin endpoint (out of scope v1). |
| `X-Request-ID` | UUID | yes | Echoed in response. Powers our audit trail. |
| `Idempotency-Key` | UUID | yes on `POST /v1/ingest` | Retries with the same key MUST be safe (return the same job). |
| `X-Tenant-ID` | str | yes | Our cityhall slug. Provider MUST scope all namespace operations to this tenant. |

**Rate limit:** provider SHOULD advertise limits via `RateLimit-*`
headers (IETF draft). Caller will back off on `429`.

**TLS pinning:** optional. If used, advertise via out-of-band channel.

## 3. Data Models

### 3.1 `Chunk`

```json
{
  "chunk_id": "c3b5f8c6-8b4a-4e9f-a1b8-2d5f9a1e4c5e",
  "content": "Articolul 15 prevede că ...",
  "article_number": "15",
  "section_title": "Capitolul II — Constituirea societăților",
  "point_number": "a",
  "page_number": 7,
  "source_id": "s_47381",
  "source_url": "https://legislatie.just.ro/Public/DetaliiDocument/47381",
  "source_title": "Legea 31/1990 privind societățile comerciale",
  "namespace_id": "legea_31_1990",
  "score": 0.873,
  "metadata": { "custom_key": "custom_value" }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `chunk_id` | UUID | yes | Provider-generated, stable across queries. |
| `content` | str | yes | Up to 4,000 chars. Romanian. Used directly as citation body in our UI. |
| `article_number` | str\|null | **yes if legal doc** | Legal domain needs exact-article match. Strings (may contain `^`) like `"15"`, `"15^1"`, `"II"`. |
| `section_title` | str\|null | yes if available | e.g. "Capitolul II". |
| `point_number` | str\|null | optional | Sub-point / letter. |
| `page_number` | int\|null | yes for PDFs | 1-indexed. |
| `source_id` | str | yes | Same ID we sent on ingest. |
| `source_url` | str\|null | yes if indexed from URL | For click-through. |
| `source_title` | str\|null | optional | Human-readable title. |
| `namespace_id` | str | yes | Must match a namespace provided on ingest. |
| `score` | float | yes | 0.0-1.0. Provider-defined semantics; higher is better. Caller uses for ranking display only. |
| `metadata` | object | optional | Free-form. Provider may pass anything; caller ignores unknown keys. |

### 3.2 `Usage`

```json
{
  "input_tokens": 1234,
  "output_tokens": 456,
  "cost_usd": 0.0012,
  "model_id": "gemini-2.5-flash"
}
```

| Field | Type | Required |
|---|---|---|
| `input_tokens` | int | yes |
| `output_tokens` | int | yes |
| `cost_usd` | float | yes (0 if unknown) |
| `model_id` | str | yes (provider's model identifier) |

### 3.3 `Error`

Every non-2xx response MUST have this shape:

```json
{
  "error": {
    "code": "namespace_not_found",
    "message": "Namespace 'cod_civil' has no indexed content.",
    "request_id": "a6f1c1c1-...",
    "details": { "namespace_id": "cod_civil" }
  }
}
```

**Standard error codes:**

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `invalid_request` | Malformed JSON, missing field, bad type. |
| 401 | `unauthorized` | Missing / bad API key. |
| 403 | `forbidden` | Tenant mismatch, namespace not owned. |
| 404 | `not_found` | Job, namespace, or source does not exist. |
| 404 | `namespace_not_found` | No indexed content for namespace. |
| 409 | `duplicate_job` | Idempotency-Key reused with different body. |
| 413 | `payload_too_large` | File or JSON body too big. |
| 415 | `unsupported_media_type` | MIME not in allowlist. |
| 422 | `validation_error` | Semantically invalid (e.g. negative `top_k`). |
| 429 | `rate_limited` | Backoff. Carries `Retry-After` header. |
| 500 | `internal_error` | Retry-safe if `Idempotency-Key` was sent. |
| 502 | `upstream_error` | Provider's LLM/vector backend unreachable. |
| 503 | `service_unavailable` | Temporary overload. |
| 504 | `timeout` | Caller should retry. |

## 4. Endpoints

### 4.1 `POST /v1/query` — Answer a Question

The hot path. Target p95 ≤ 4,000 ms end-to-end.

**Request:**

```http
POST /v1/query HTTP/1.1
Content-Type: application/json
Authorization: Bearer <key>
X-Request-ID: <uuid>
X-Tenant-ID: ph-balta-doamnei

{
  "question": "Ce spune articolul 15 din Legea 31/1990?",
  "language": "ro",
  "namespaces": ["legea_31_1990"],
  "top_k": 5,
  "hint_article_number": "15",
  "rerank": true,
  "include_answer": true,
  "conversation_history": [
    {"role": "user", "content": "anterior..."},
    {"role": "assistant", "content": "răspuns anterior..."}
  ],
  "style_hints": {
    "answer_max_chars": 1800,
    "cite_inline": true,
    "tone": "formal"
  }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `question` | str | yes | Max 2,000 chars. |
| `language` | str | yes | ISO 639-1. `"ro"` for Romanian (only supported in v1). |
| `namespaces` | string[] | yes | 1-10. Multi-namespace = retrieve from all, merge results. |
| `top_k` | int | optional | Default 10. Max 50. Final answer may cite fewer. |
| `hint_article_number` | str\|null | optional | If caller regex-extracted an article number from the question, pass it here for exact-match boost. CRITICAL for legal quality. |
| `rerank` | bool | optional | Default `true`. |
| `include_answer` | bool | optional | Default `true`. If `false`, return only citations (retrieval-only mode for apps that want to render their own answer). |
| `conversation_history` | object[] | optional | Max 15 turns. Each turn: `{role: "user"\|"assistant", content: str}`. |
| `style_hints` | object | optional | Provider may ignore. `answer_max_chars` (default 2,000), `cite_inline` (default `true` → inject `[1]`-style markers into answer), `tone` (`"formal"`\|`"casual"`, default `"formal"`). |

**Response (200):**

```json
{
  "request_id": "a6f1c1c1-...",
  "answer": "Articolul 15 din Legea 31/1990 prevede că [1] ...",
  "citations": [
    { "marker": "[1]", "chunk": { /* Chunk */ } },
    { "marker": "[2]", "chunk": { /* Chunk */ } }
  ],
  "usage": { /* Usage */ },
  "latency_ms": 1823,
  "model_version": "gemini-2.5-flash:2026-03",
  "retrieval_strategy": "hybrid_rrf_v2",
  "confidence": 0.91,
  "trace_id": "provider-internal-id"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `answer` | str\|null | yes if `include_answer=true` | Final Romanian prose. Plain text — NO Markdown (Telegram `parse_mode=Markdown` breaks on LLM output; we send as plain text). |
| `citations` | object[] | yes | 1:1 ordering with `marker` references in `answer`. Each has `marker` (`"[1]"`) + full `chunk`. |
| `usage` | Usage | yes | Tokens + cost for this request. |
| `latency_ms` | int | yes | Provider-side end-to-end. |
| `model_version` | str | yes | Human-readable (used in admin debug page). |
| `retrieval_strategy` | str | optional | Opaque label (for A/B analytics). |
| `confidence` | float | optional | 0.0-1.0. If provider can't compute, omit. Caller may gate low-confidence replies behind "I don't know" fallback. |
| `trace_id` | str | optional | Provider's internal trace for debugging. |

**Empty-result contract:** if no relevant chunks found, respond `200` with
`answer: null`, `citations: []`, and `confidence: 0.0`. Caller then sends
its "nu am găsit informații" fallback. Do NOT hallucinate.

**Errors:** `400`, `401`, `403`, `404`, `422`, `429`, `500`, `502`, `504`.

---

### 4.2 `POST /v1/ingest` — Index a Document

**Request (by URL):**

```http
POST /v1/ingest HTTP/1.1
Content-Type: application/json
Authorization: Bearer <key>
X-Request-ID: <uuid>
X-Tenant-ID: ph-balta-doamnei
Idempotency-Key: <uuid>

{
  "namespace_id": "legea_31_1990",
  "source_id": "s_47381",
  "source_type": "url",
  "url": "https://legislatie.just.ro/Public/DetaliiDocument/47381",
  "mime_type_hint": "text/html",
  "metadata": {
    "source_title": "Legea 31/1990 privind societățile comerciale",
    "language": "ro",
    "document_type": "lege",
    "published_at": "1990-11-16"
  },
  "callback_url": "https://api.lex-advisor.citydock.ro/webhooks/rag/ingest"
}
```

**Request (by file upload):** `multipart/form-data` with fields:
- `payload` (JSON — same shape minus `url`, with `source_type: "file"`)
- `file` (the bytes)

**Allowed `source_type`:** `url` | `file`.
**Allowed MIME:** `text/html`, `application/pdf`, `text/plain`, `text/markdown`.
**Max file size:** 50 MiB.
**URL fetch timeout:** 60 s. Provider SHOULD respect robots.txt only for public-internet sources.

**Response (202 Accepted):**

```json
{
  "job_id": "j_9f8e7d6c...",
  "status": "queued",
  "submitted_at": "2026-04-22T13:45:00Z",
  "estimated_completion_at": "2026-04-22T13:50:00Z"
}
```

**Idempotency:** resubmitting with same `Idempotency-Key` MUST return
the existing `job_id` with its current status. Different body + same key
= `409 duplicate_job`.

**Errors:** `400`, `401`, `403`, `413`, `415`, `422`, `429`, `500`.

---

### 4.3 `GET /v1/ingest/{job_id}` — Poll Ingest Status

```http
GET /v1/ingest/j_9f8e7d6c... HTTP/1.1
Authorization: Bearer <key>
X-Tenant-ID: ph-balta-doamnei
```

**Response (200):**

```json
{
  "job_id": "j_9f8e7d6c...",
  "namespace_id": "legea_31_1990",
  "source_id": "s_47381",
  "status": "done",
  "progress": {
    "stage": "embedding",
    "percent": 100,
    "chunks_created": 327
  },
  "submitted_at": "2026-04-22T13:45:00Z",
  "completed_at": "2026-04-22T13:49:12Z",
  "error": null
}
```

**Status values:** `queued` → `fetching` → `extracting` → `chunking` →
`embedding` → `indexing` → `done`. Terminal: `done` | `failed` | `cancelled`.

**Failed example:**

```json
{
  "job_id": "...",
  "status": "failed",
  "error": {
    "code": "fetch_failed",
    "message": "URL returned 404",
    "retryable": false
  },
  "progress": { "stage": "fetching", "percent": 0, "chunks_created": 0 }
}
```

**Polling contract:** provider SHOULD advertise `Retry-After` header on
200 responses with non-terminal status so caller knows when to poll again.
Default: 5 s for `queued`, 10 s for processing stages.

---

### 4.4 `DELETE /v1/namespaces/{namespace_id}/sources/{source_id}` — Remove Single Source

Used when a source is deleted or re-uploaded in admin. Provider MUST
remove all chunks indexed under that source.

**Response (204 No Content).** `404` if not found. SLA: ≤ 5 minutes.

---

### 4.5 `DELETE /v1/namespaces/{namespace_id}` — Hard Delete Namespace (GDPR)

Used when a cityhall is removed or an agent is retired. Deletes **all**
chunks + embeddings + derived data. Irreversible.

**Response (202 Accepted):**
```json
{ "job_id": "del_...", "status": "queued", "sla": "24h" }
```

**Propagation SLA:** ≤ 24 hours (GDPR requirement). Provider MUST NOT
retain derived data (fine-tuned indices, embedding caches) past this
window.

---

### 4.6 `GET /v1/namespaces/{namespace_id}/stats` — Namespace Stats

Used for admin dashboard badges ("327 chunks indexed, last updated 2 days
ago").

**Response (200):**

```json
{
  "namespace_id": "legea_31_1990",
  "chunk_count": 327,
  "source_count": 1,
  "total_tokens_indexed": 48231,
  "last_ingested_at": "2026-04-22T13:49:12Z",
  "embedding_model": "text-embedding-3-large",
  "embedding_dim": 3072
}
```

---

### 4.7 `GET /v1/health` — Liveness

Fast, unauthenticated. Used by our healthcheck + admin status dashboard.

```json
{
  "status": "ok",
  "version": "1.2.3",
  "uptime_seconds": 123456,
  "dependencies": {
    "vector_store": "ok",
    "llm": "ok",
    "object_store": "ok"
  }
}
```

**`status`:** `"ok"` | `"degraded"` | `"down"`. Provider SHOULD return
`200` on ok/degraded, `503` on down.

---

### 4.8 `POST /v1/eval` — (Optional) Evaluation Mode

Used by our eval harness to A/B compare providers. Same shape as
`/v1/query` but with extra hints:

```json
{
  "question": "...",
  "namespaces": [...],
  "expected_citations": ["chunk_id_1", "chunk_id_2"],
  "expected_answer_keywords": ["articol", "15"]
}
```

Response adds `eval` block:
```json
{
  "answer": "...",
  "citations": [...],
  "eval": {
    "citation_precision_at_k": 0.80,
    "keyword_match_rate": 1.0
  }
}
```

Optional endpoint — if provider doesn't implement it, caller falls back
to running the eval client-side.

---

## 5. Webhooks (Optional but Recommended)

Lets provider push ingestion completion instead of caller polling.

**Callback URL:** supplied in each `POST /v1/ingest` via `callback_url`
field.

**Delivery:** `POST` with JSON body + `X-Vendor-Signature: sha256=<hmac>`
(HMAC-SHA256 of raw body using a shared secret — exchanged out of band).

**Body:**
```json
{
  "event": "ingest.completed",
  "job_id": "j_...",
  "namespace_id": "legea_31_1990",
  "source_id": "s_47381",
  "status": "done",
  "chunks_created": 327,
  "at": "2026-04-22T13:49:12Z"
}
```

**Events:** `ingest.completed`, `ingest.failed`, `namespace.deleted`.

**Retry policy:** exponential backoff up to 24 h. Caller returns `200`
on success, non-2xx to trigger retry.

---

## 6. Non-Functional Requirements

### 6.1 Performance

| Metric | Target (SLO) | Hard floor |
|---|---|---|
| `POST /v1/query` p50 | ≤ 1,500 ms | — |
| `POST /v1/query` p95 | ≤ 4,000 ms | ≤ 6,000 ms |
| `POST /v1/query` p99 | ≤ 6,000 ms | ≤ 10,000 ms |
| `GET /v1/health` p95 | ≤ 200 ms | ≤ 500 ms |
| `POST /v1/ingest` accept | ≤ 500 ms (202) | — |
| `DELETE /v1/namespaces/{id}/sources/{id}` | ≤ 5 min end-to-end | ≤ 15 min |
| `DELETE /v1/namespaces/{id}` | ≤ 24 h end-to-end | ≤ 48 h |
| Uptime (monthly) | 99.5 % | 99.0 % |

### 6.2 Concurrency

- **Burst:** provider MUST handle 50 concurrent queries per tenant.
- **Sustained:** 10 QPS per tenant.

### 6.3 Data Residency & GDPR

- All data stored + processed in EU (preferably `europe-west3`).
- No training of base models on tenant data unless explicitly contracted.
- Right-to-delete SLA: ≤ 24 h (see 4.5).
- Audit log of data accesses retained for 90 days, exportable on request.

### 6.4 Romanian-Language Quality Gate

Before go-live, provider MUST pass caller's eval set:

- ≥ 85 % answer correctness (human-graded on 100 Q&As)
- ≥ 80 % citation precision@3
- ≥ 90 % exact-article-hit rate when `hint_article_number` is provided
- p95 latency within 20 % of our local baseline

---

## 7. Security

- TLS 1.2+, HSTS preload recommended.
- API keys MUST be ≥ 256-bit entropy, rotatable without downtime.
- Request bodies MUST be validated against schema; extra fields MAY be
  ignored or rejected (`400`) — provider SHOULD document.
- No chunk content leaks across tenants or namespaces. Cross-tenant leak
  = P0 bug.
- All logs MUST redact `question`, `conversation_history`, and returned
  chunks by default. Full capture only behind per-tenant opt-in.

---

## 8. Observability

Each response SHOULD include:

```
X-Request-ID: <echo of caller's>
X-Vendor-Trace-ID: <provider's>
X-Vendor-Retrieval-Strategy: <opaque label>
Server-Timing: retrieval;dur=412, rerank;dur=88, generation;dur=1320
```

See §13.6 for the full Prometheus + OpenTelemetry emission requirements.

---

## 9. Versioning & Deprecation

- **Semver on the API path.** `/v1/*` stable. `/v1beta/*` for experimental.
- **Breaking change = new major.** Both served for ≥ 6 months.
- **Deprecation header:** `Sunset: Wed, 22 Oct 2026 00:00:00 GMT` announces
  removal date.

---

## 10. Open Questions

Please answer these before starting implementation. They drive scoping,
cost modelling, and the onboarding timeline.

1. Which **embedding model** (family + dimension) will you use, and is it
   swappable per tenant? This directly affects namespace stats display
   and GDPR re-embed cost when models roll.
2. Which **LLM(s)** will you use for answer generation? Do you support
   per-tenant model selection, and can tenants pin to a specific
   generation date/version for regression-test stability?
3. How accurate is `usage.cost_usd` against your invoice? We need it
   within ±5 % for in-product cost display. If you cannot hit that, what
   monthly reconciliation export will you provide?
4. Will you implement the optional `POST /v1/eval` endpoint (§4.8) and
   webhooks (§5), or should the caller poll + run eval client-side?
5. For namespace deletion, do you support a soft-delete with grace window
   or only hard-delete? GDPR permits either; we prefer soft.
6. What framework / stack are you building on (FastAPI, LangChain,
   Haystack, LlamaIndex, bespoke)? Which vector store?
7. What is your pricing model — per request, per token, per
   storage-GB-month, or a blended platform fee? Please itemize so the
   caller can project monthly spend.
8. What timeline can you commit to for reaching the §6 SLOs + §6.4
   Romanian quality gate on the caller's eval set?

---

## 11. Reserved

Intentionally left blank in v1 so subsequent majors can slot new
sections without renumbering.

---

## 12. Reserved

Intentionally left blank in v1.

---

## 13. Packaging & Delivery

### 13.1 What you deliver to us

- **Source code** in a Bitbucket repo under our organization (we'll
  create and grant you developer access). Python 3.12 is our preferred
  stack; if you propose a different language, raise it up front.
- **Semver git tags** on releases (`vX.Y.Z`). Follow Conventional
  Commits (`feat:` → minor, `fix:` → patch, `BREAKING CHANGE:` → major).
  Tags on `main` trigger our CI.
- **A Dockerfile** at the repo root. Requirements:
  - Multi-stage build (builder + runtime).
  - Non-root runtime user (uid 1000, named `appuser`).
  - Pinned base image by SHA-256 digest (never `latest`).
  - Service listens on port **8080**.
  - `HEALTHCHECK` calling `GET /v1/health`.
  - Logs to stdout (structured JSON recommended).
  - Handles SIGTERM gracefully — drain in-flight requests before exit.
  - Final image size under 500 MB unless justified.
- **A `docker-compose.service.yml` fragment** showing how the service
  should be run alongside our existing stack. Exactly one service (plus
  any DB/Redis it needs). No host ports exposed. Joins an external
  network named `lex-advisor`. Env-var driven (no hardcoded values).
- **An OpenAPI document** (`openapi.yaml`) in the repo root and
  served at `GET /v1/openapi.json` on the running service.
- **A `README.md`** with: prerequisites, `docker compose up` local run,
  env var table, smoke test commands, troubleshooting runbook.

### 13.2 How we build and deploy your image

You do NOT need any Google Cloud credentials. The flow is:

1. You push code + a semver tag to the Bitbucket repo we provide.
2. Our **self-hosted Bitbucket Pipelines runner** (running on our
   infrastructure) picks up the push.
3. The runner builds your Docker image from your Dockerfile, runs
   lint + tests + Trivy security scan, and — on tagged releases —
   pushes the image to our private GCP Artifact Registry.
4. We deploy the tagged image into our stack using the compose
   fragment you supplied.

So your `bitbucket-pipelines.yml` only needs to define the build + test
+ scan steps. The push target is our runner's concern, not yours.

### 13.3 CI pipeline you provide

Your `bitbucket-pipelines.yml` should include, at minimum:

- **On every pull request:**
  1. Lint (`ruff`, `mypy`, or equivalents for your language).
  2. Unit tests (aim for ≥ 80 % coverage).
  3. Integration tests against your own containerised DB/Redis.
  4. Build the Docker image.
  5. Run a CVE scan (Trivy or equivalent) — fail on any CRITICAL or
     HIGH finding.

- **On semver tag push (`v*.*.*`):**
  6. Re-run the above end to end. Our runner handles the image push
     after this completes green.

### 13.4 Database and storage

If your service needs persistence (Postgres, Redis, object storage),
declare it inside `docker-compose.service.yml`. You own its schema,
volume, credentials, and backups. Do NOT rely on our shared Postgres
or Redis. Do NOT share secrets outside your own containers.

### 13.5 Authentication you implement

- Inbound requests to your service carry
  `Authorization: Bearer <api_key>` (§2). You validate and reject
  mismatches with `401 unauthorized`.
- Outbound webhooks you send us MUST be HMAC-SHA256-signed over the
  raw body, header `X-Vendor-Signature: sha256=<hex>`. We exchange the
  webhook shared secret out of band on provisioning.
- No mTLS required for v1. No TLS required on internal Docker-network
  traffic between our stack and your service (we handle the TLS edge
  on our public endpoints).

### 13.6 Observability you implement

Mandatory from day 1, regardless of whether we have consumers ready:

| Requirement | Details |
|---|---|
| `GET /metrics` | Prometheus exposition format. Minimum metrics: `http_requests_total{method,status,endpoint}`, `http_request_duration_seconds`, `vendor_cost_usd_total`, `vendor_tokens_total{direction}`, `vendor_external_api_errors_total{dependency,error_type}`. |
| OpenTelemetry | Use `opentelemetry-distro` (or language equivalent) with auto-instrumentation for your HTTP server, DB client, and HTTP client. Env-driven: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`. Unset endpoint → no-op (safe for local dev). |
| Trace-log correlation | Every log line includes `trace_id` + `span_id` from the current OTel span context, plus the `X-Request-ID` value from the inbound header. |
| Response headers | Echo `X-Request-ID`; emit `X-Vendor-Trace-ID`; emit `Server-Timing` header or equivalent JSON body fields. |

---

## 14. Testing the Implementation

This section tells you how to smoke-test the service you have built, what
reference Romanian data we will probe it with, how our contract-test suite
works, and what we will do before we accept delivery.

### 14.1 Quick-start smoke test

Drop-in curl commands for the hot endpoints. Replace `<api_key>` with the
Bearer token we gave you, keep `X-Tenant-ID: ph-balta-doamnei` for the canary
cityhall, and generate a fresh UUID for every `X-Request-ID` /
`Idempotency-Key`.

#### `POST /v1/query`

```bash
curl -sS -X POST http://localhost:8080/v1/query \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: 11111111-1111-4111-8111-111111111111" \
  -H "X-Tenant-ID: ph-balta-doamnei" \
  -d '{
    "question": "Ce spune articolul 15 din Legea 31/1990?",
    "language": "ro",
    "namespaces": ["legea_31_1990"],
    "top_k": 5,
    "hint_article_number": "15",
    "rerank": true,
    "include_answer": true
  }'
```

Expected response shape (200):

```json
{
  "request_id": "11111111-1111-4111-8111-111111111111",
  "answer": "Articolul 15 din Legea 31/1990 prevede că [1] ...",
  "citations": [
    {
      "marker": "[1]",
      "chunk": {
        "chunk_id": "c3b5f8c6-8b4a-4e9f-a1b8-2d5f9a1e4c5e",
        "content": "Articolul 15. — Aporturile în numerar sunt obligatorii ...",
        "article_number": "15",
        "namespace_id": "legea_31_1990",
        "source_id": "s_47381",
        "source_url": "https://legislatie.just.ro/Public/DetaliiDocument/47381",
        "score": 0.91
      }
    }
  ],
  "usage": { "input_tokens": 842, "output_tokens": 312, "cost_usd": 0.0009, "model_id": "gemini-2.5-flash" },
  "latency_ms": 1823,
  "model_version": "gemini-2.5-flash:2026-03",
  "confidence": 0.91
}
```

#### `POST /v1/ingest`

```bash
curl -sS -X POST http://localhost:8080/v1/ingest \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: 22222222-2222-4222-8222-222222222222" \
  -H "X-Tenant-ID: ph-balta-doamnei" \
  -H "Idempotency-Key: 33333333-3333-4333-8333-333333333333" \
  -d '{
    "namespace_id": "legea_31_1990",
    "source_id": "s_47381",
    "source_type": "url",
    "url": "https://legislatie.just.ro/Public/DetaliiDocument/47381",
    "mime_type_hint": "text/html",
    "metadata": {
      "source_title": "Legea 31/1990 privind societățile comerciale",
      "language": "ro",
      "document_type": "lege",
      "published_at": "1990-11-16"
    }
  }'
```

Expected response (202):

```json
{
  "job_id": "j_9f8e7d6c5b4a",
  "status": "queued",
  "submitted_at": "2026-04-22T13:45:00Z",
  "estimated_completion_at": "2026-04-22T13:50:00Z"
}
```

#### `GET /v1/ingest/{job_id}`

```bash
curl -sS http://localhost:8080/v1/ingest/j_9f8e7d6c5b4a \
  -H "Authorization: Bearer <api_key>" \
  -H "X-Tenant-ID: ph-balta-doamnei"
```

Expected response (200):

```json
{
  "job_id": "j_9f8e7d6c5b4a",
  "namespace_id": "legea_31_1990",
  "source_id": "s_47381",
  "status": "done",
  "progress": { "stage": "indexing", "percent": 100, "chunks_created": 327 },
  "submitted_at": "2026-04-22T13:45:00Z",
  "completed_at": "2026-04-22T13:49:12Z",
  "error": null
}
```

#### `GET /v1/health`

```bash
curl -sS http://localhost:8080/v1/health
```

Expected response (200):

```json
{
  "status": "ok",
  "version": "1.2.3",
  "uptime_seconds": 123456,
  "dependencies": { "vector_store": "ok", "llm": "ok", "object_store": "ok" }
}
```

#### `DELETE /v1/namespaces/{id}` (GDPR hard delete)

```bash
curl -sS -X DELETE http://localhost:8080/v1/namespaces/legea_31_1990 \
  -H "Authorization: Bearer <api_key>" \
  -H "X-Request-ID: 44444444-4444-4444-8444-444444444444" \
  -H "X-Tenant-ID: ph-balta-doamnei"
```

Expected response (202):

```json
{ "job_id": "del_abc123", "status": "queued", "sla": "24h" }
```

### 14.2 Reference test data

We probe every candidate implementation with the following Romanian legal
cases. Use them as your own integration fixtures — they are the same shape we
use on our side.

**Case 1 — Exact-article hint (Legea 31/1990).**

- Question: `"Ce spune articolul 15 din Legea 31/1990?"`
- Namespaces: `["legea_31_1990"]`
- `hint_article_number: "15"`
- Expected citation `chunk.article_number == "15"`,
  `chunk.namespace_id == "legea_31_1990"`, Romanian content containing
  "aporturile în numerar" (note diacritics preserved).
- Expected `answer` is plain Romanian prose citing `[1]`, ends with a full
  stop, contains no Markdown.

**Case 2 — Multi-namespace retrieval (Codul Civil + Legea 31/1990).**

- Question: `"Cum se constituie o societate cu răspundere limitată și ce responsabilități au asociații?"`
- Namespaces: `["legea_31_1990", "cod_civil"]`
- No `hint_article_number` — provider must retrieve broadly.
- Expected at least one citation from each namespace (ideally),
  citations mention "societate cu răspundere limitată" and reference
  distinct `source_id` values. Diacritics (`ă`, `ș`, `ț`, `î`, `â`) are
  preserved verbatim in `content`.

**Case 3 — "No information found" (out-of-domain).**

- Question: `"Care este programul primăriei Bălta Doamnei?"`
- Namespaces: `["legea_31_1990"]` (intentionally wrong domain for this
  question).
- Expected response: `200` with `answer: null`, `citations: []`,
  `confidence: 0.0`. Provider MUST NOT hallucinate a fake answer.

### 14.3 Contract tests

**Contract tests:** we will provide a Schemathesis-based test harness + a
set of Romanian-language domain tests as part of the onboarding package.
They run via `pytest` against your local/staging deployment. The suite
exercises the full OpenAPI surface plus hand-written tests for
Romanian-specific behaviour (see §6 quality gates). Your implementation
must pass the suite before we accept delivery.

The suite combines:

1. **Schemathesis property-based tests** derived from the committed OpenAPI
   YAML (`docs/external/rag-api-spec.yaml`) — fuzzes every endpoint,
   checks status codes, required fields, idempotency, header echo.
2. **Hand-written domain-semantic tests** — Romanian language handling,
   exact-article match when `hint_article_number` is provided, empty-result
   contract (no hallucination), GDPR delete propagation, cross-tenant
   isolation, multi-namespace merging.

Failed test IDs + a reproduction fixture ship with every release so you can
rerun locally.

### 14.4 Our acceptance process

Before cutover, we run the contract test suite plus an independent eval set
(100 Romanian legal Q&As covering Legea 31/1990, Codul Civil, Codul Fiscal,
and municipal regulations) against your deployed service. Quality gates are
defined in §6 of this spec — we will not cut over unless every numeric target
is met. We also run a period of manual exploratory testing on a canary
cityhall (responses generated by both services and logged side-by-side,
never shown to users) before flipping traffic.

---

## 15. Deliverable Bundle & Handoff

On "delivery day" we expect a single handoff email linking to everything
below. Anything missing is a blocker — we will not accept delivery until the
bundle is complete. Think of this as our Definition of Done.

- [ ] **Bitbucket repo URL** at `bitbucket.org/<our-org>/lex-advisor-rag`
      (or agreed variant) with read + push access granted to our team
      accounts.
- [ ] **Semver git tag (`vX.Y.Z`)** pushed to the Bitbucket repo; our
      self-hosted runner handles the image build + push after your tagged
      pipeline passes. Confirm the tag pipeline completed green.
- [ ] **`openapi.yaml`** committed at repo root and exposed live at
      `GET /v1/openapi.json` on the running service — byte-identical to the
      committed file.
- [ ] **`README.md`** with install, deploy, env-var reference, and an ops
      runbook (common failure modes, how to drain traffic, how to roll back,
      how to rotate API keys).
- [ ] **CI logs** showing the full contract-test suite GREEN on the release
      tag commit.
- [ ] **Eval run results** meeting every §6 quality gate (≥ 85 % answer
      correctness, ≥ 80 % citation precision@3, ≥ 90 % exact-article hit
      rate, p95 within 20 % of baseline) on our eval set — raw JSON + PDF
      summary.
- [ ] **Security scan report** (Trivy or equivalent) on the release image,
      clean on CRITICAL + HIGH.
- [ ] **`LICENSE`** file in the repo (Apache-2.0 default, or pre-approved
      alternative).
- [ ] **Signed IP assignment clause** (for any custom work) + signed DPA +
      EU data residency attestation (`europe-west3` or other approved EU
      region).
- [ ] **30-day post-delivery support SOW** covering bug fixes, SLO breaches,
      and at least one named engineer.
- [ ] **Status page URL** + named 24/7 on-call contact (phone + email +
      escalation chain).

Fail any of the above and we do not accept delivery until fixed — no hard
cutover.

---

## 16. Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-04-22 | Initial public spec for external implementers. |
