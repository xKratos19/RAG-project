# CityDock RAG Service

Implementation of the RAG service defined by `openapi.yaml` / `rag-api-spec.md` for Lex-Advisor / CityDock.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- A Google AI Studio API key (Gemini 2.5 Flash + text-embedding-004)

## Local run

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .[dev]

# Copy and fill in the required variables
cp .env.example .env
# Set GEMINI_API_KEY, DATABASE_URL (sqlite:///./rag.db works for local), REDIS_URL

# Apply DB migrations
python scripts/db_upgrade.py

# Start the API
uvicorn app.main:app --host 0.0.0.0 --port 8080

# In a second terminal, start the ingest worker
arq app.worker.WorkerSettings
```

## Environment variables

| Name | Default | Description |
|---|---|---|
| `API_KEY` | `dev-api-key` | Bearer token expected by every endpoint |
| `GEMINI_API_KEY` | — | **Required.** Google AI Studio key for embeddings + generation |
| `DEFAULT_TENANT` | `ph-balta-doamnei` | Default tenant slug |
| `MAX_FILE_SIZE_MIB` | `50` | Max multipart file size |
| `ALLOWED_MIME_TYPES` | `text/html,application/pdf,text/plain,text/markdown` | MIME allowlist |
| `WEBHOOK_SECRET` | `dev-webhook-secret` | HMAC-SHA256 secret for outbound webhook signatures |
| `DATABASE_URL` | `sqlite:///./rag.db` | SQLAlchemy DSN (PostgreSQL with pgvector for production) |
| `REDIS_URL` | `redis://localhost:6379/0` | ARQ job queue (required for background ingest) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | Optional OpenTelemetry OTLP gRPC endpoint; unset = no-op |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | OTLP transport protocol |
| `OTEL_SERVICE_NAME` | `citydock-rag-service` | Service name emitted in traces |
| `OTEL_RESOURCE_ATTRIBUTES` | `deployment.environment=local` | Extra OTel resource attributes |

## Running with Docker Compose

```bash
cp .env.example .env
# Fill in API_KEY, GEMINI_API_KEY, WEBHOOK_SECRET

docker network create lex-advisor
docker compose -f docker-compose.service.yml up --build
```

The compose file starts four containers: the API, the ARQ worker, PostgreSQL 16, and Redis 7.

## Smoke tests

```bash
# Health
curl http://localhost:8080/v1/health

# OpenAPI schema
curl http://localhost:8080/v1/openapi.json

# Ingest a URL
curl -sS -X POST http://localhost:8080/v1/ingest \
  -H "Authorization: Bearer dev-api-key" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: 22222222-2222-4222-8222-222222222222" \
  -H "X-Tenant-ID: ph-balta-doamnei" \
  -H "Idempotency-Key: 33333333-3333-4333-8333-333333333333" \
  -d '{"namespace_id":"legea_31_1990","source_id":"s_47381","source_type":"url","url":"https://legislatie.just.ro/Public/DetaliiDocument/47381","mime_type_hint":"text/html","metadata":{"source_title":"Legea 31/1990","language":"ro"}}'

# Query
curl -sS -X POST http://localhost:8080/v1/query \
  -H "Authorization: Bearer dev-api-key" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: 11111111-1111-4111-8111-111111111111" \
  -H "X-Tenant-ID: ph-balta-doamnei" \
  -d '{"question":"Ce spune articolul 15 din Legea 31/1990?","language":"ro","namespaces":["legea_31_1990"],"top_k":5,"hint_article_number":"15","rerank":true,"include_answer":true}'
```

## Tests

```bash
pytest --maxfail=1 -q
```

Tests run against SQLite with mocked Gemini calls — no API key required.

## Quality gate harness

```bash
# Load / latency test (50 concurrent queries, requires running service)
python scripts/load_test.py

# Functional eval (writes eval-report.json)
python scripts/eval_quality_gate.py
```

## Schema migrations

```bash
# Apply all pending migrations
python scripts/db_upgrade.py

# Alembic revision chain:
#   0001_initial_schema      — core tables
#   0002_pgvector_extension  — CREATE EXTENSION vector (Postgres only)
#   0003_vector_and_extras   — embedding_json column, HNSW index, FTS index,
#                              callback_url + file_bytes on rag_jobs
```

## Troubleshooting runbook

| Symptom | Resolution |
|---|---|
| `401 unauthorized` | Verify `Authorization: Bearer <API_KEY>` and `X-Request-ID` UUID header |
| `409 duplicate_job` | Same `Idempotency-Key` with a different request body |
| `422 validation_error` | Payload violates schema constraints (e.g. unsupported `language`, `namespaces` empty) |
| Empty query results (`answer: null, citations: []`) | Namespace has no indexed chunks yet — check ingest job status |
| Worker not processing jobs | Ensure `arq app.worker.WorkerSettings` is running and `REDIS_URL` matches the API service |
| High query latency | Check `Server-Timing` header to identify bottleneck (embed / retrieval / generation) |

## Operations

- **Graceful shutdown:** both the API (uvicorn) and the worker (arq) handle SIGTERM — in-flight requests drain before exit.
- **Rollback:** deploy the previous semver tag image; schema migrations are backward-compatible within minor versions.
- **API key rotation:** update `API_KEY` secret and redeploy; no downtime required.
- **GDPR namespace deletion:** `DELETE /v1/namespaces/{id}` returns 202 and completes within 24 h (SLA).
