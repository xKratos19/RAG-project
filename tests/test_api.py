from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TENANT = "ph-balta-doamnei"


def headers(with_idem: bool = False, idem_key: str = "22222222-2222-4222-8222-222222222222") -> dict[str, str]:
    base = {
        "Authorization": "Bearer dev-api-key",
        "X-Request-ID": "11111111-1111-4111-8111-111111111111",
        "X-Tenant-ID": TENANT,
    }
    if with_idem:
        base["Idempotency-Key"] = idem_key
    return base


# ── Health ───────────────────────────────────────────────────────────────────

def test_health() -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded", "down"}
    assert "version" in body
    assert "uptime_seconds" in body
    assert "dependencies" in body


def test_health_response_headers() -> None:
    r = client.get("/v1/health")
    assert "X-Vendor-Trace-ID" in r.headers
    assert "RateLimit-Limit" in r.headers


# ── Ingest + Query contract ──────────────────────────────────────────────────

def test_ingest_then_query_contract() -> None:
    ingest_payload = {
        "namespace_id": "legea_31_1990",
        "source_id": "s_47381",
        "source_type": "url",
        "url": "https://example.com/Articolul 15 prevede ca aporturile in numerar sunt obligatorii.",
        "mime_type_hint": "text/html",
        "metadata": {"source_title": "Legea 31/1990"},
    }
    r = client.post("/v1/ingest", headers=headers(with_idem=True), json=ingest_payload)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] in {"queued", "done", "fetching", "extracting", "chunking", "embedding", "indexing"}
    assert body["job_id"].startswith("j_")

    query_payload = {
        "question": "Ce spune articolul 15 din Legea 31/1990?",
        "language": "ro",
        "namespaces": ["legea_31_1990"],
        "top_k": 5,
        "hint_article_number": "15",
        "rerank": True,
        "include_answer": True,
    }
    r2 = client.post("/v1/query", headers=headers(), json=query_payload)
    assert r2.status_code == 200
    body2 = r2.json()
    assert "citations" in body2
    assert "usage" in body2
    assert body2["request_id"] == headers()["X-Request-ID"]
    assert body2["retrieval_strategy"] == "hybrid_rrf_v2"
    assert body2["model_version"].startswith("gemini-2.5-flash")


def test_query_response_headers() -> None:
    """Server-Timing and X-Request-ID must be echoed on query responses."""
    ingest_payload = {
        "namespace_id": "ns_headers_test",
        "source_id": "s_h1",
        "source_type": "url",
        "url": "https://example.com/content header test",
    }
    client.post("/v1/ingest", headers=headers(with_idem=True, idem_key="aa111111-1111-4111-8111-111111111111"), json=ingest_payload)

    r = client.post("/v1/query", headers=headers(), json={
        "question": "test question header",
        "language": "ro",
        "namespaces": ["ns_headers_test"],
    })
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == "11111111-1111-4111-8111-111111111111"
    assert "Server-Timing" in r.headers


# ── Empty result contract ────────────────────────────────────────────────────

def test_empty_result_contract() -> None:
    r = client.post("/v1/query", headers=headers(), json={
        "question": "Care este programul primariei Balta Doamnei?",
        "language": "ro",
        "namespaces": ["namespace_care_nu_exista_sigur"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] is None
    assert body["citations"] == []
    assert body["confidence"] == 0.0


# ── Retrieval-only mode ──────────────────────────────────────────────────────

def test_retrieval_only_mode_answer_null() -> None:
    r = client.post("/v1/query", headers=headers(), json={
        "question": "Ce spune articolul 15 din Legea 31/1990?",
        "language": "ro",
        "namespaces": ["legea_31_1990"],
        "include_answer": False,
    })
    assert r.status_code == 200
    assert r.json()["answer"] is None


# ── Validation ───────────────────────────────────────────────────────────────

def test_validation_error_shape() -> None:
    r = client.post("/v1/query", headers=headers(), json={"question": "x", "language": "en", "namespaces": []})
    assert r.status_code == 422
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "validation_error"


def test_missing_auth_header_returns_401() -> None:
    r = client.post("/v1/query", headers={"X-Request-ID": "11111111-1111-4111-8111-111111111111", "X-Tenant-ID": TENANT}, json={
        "question": "test",
        "language": "ro",
        "namespaces": ["ns"],
    })
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_wrong_api_key_returns_401() -> None:
    h = headers()
    h["Authorization"] = "Bearer wrong-key"
    r = client.post("/v1/query", headers=h, json={"question": "x", "language": "ro", "namespaces": ["ns"]})
    assert r.status_code == 401


# ── Ingest job polling ───────────────────────────────────────────────────────

def test_ingest_poll_returns_job() -> None:
    ingest_payload = {
        "namespace_id": "ns_poll",
        "source_id": "s_poll",
        "source_type": "url",
        "url": "https://example.com/poll test content",
    }
    r = client.post("/v1/ingest", headers=headers(with_idem=True, idem_key="bb222222-2222-4222-8222-222222222222"), json=ingest_payload)
    job_id = r.json()["job_id"]

    poll = client.get(f"/v1/ingest/{job_id}", headers=headers())
    assert poll.status_code == 200
    body = poll.json()
    assert body["job_id"] == job_id
    assert body["namespace_id"] == "ns_poll"
    assert body["source_id"] == "s_poll"


def test_ingest_poll_missing_job_returns_404() -> None:
    r = client.get("/v1/ingest/j_nonexistent999", headers=headers())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# ── Tenant isolation ─────────────────────────────────────────────────────────

def test_ingest_status_tenant_isolation() -> None:
    ingest_payload = {
        "namespace_id": "tenant_scope_ns",
        "source_id": "s_x",
        "source_type": "url",
        "url": "https://example.com/Articolul 15 continut de test.",
    }
    r = client.post("/v1/ingest", headers=headers(with_idem=True, idem_key="33333333-3333-4333-8333-333333333333"), json=ingest_payload)
    job_id = r.json()["job_id"]

    wrong = {"Authorization": "Bearer dev-api-key", "X-Tenant-ID": "other-tenant"}
    poll = client.get(f"/v1/ingest/{job_id}", headers=wrong)
    assert poll.status_code == 403
    assert poll.json()["error"]["code"] == "forbidden"


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_idempotency_conflict_same_key_different_body() -> None:
    key = "44444444-4444-4444-8444-444444444444"
    first = {"namespace_id": "ns_idem", "source_id": "s1", "source_type": "url", "url": "https://example.com/varianta unu"}
    second = {"namespace_id": "ns_idem", "source_id": "s2", "source_type": "url", "url": "https://example.com/varianta doi"}

    r1 = client.post("/v1/ingest", headers=headers(with_idem=True, idem_key=key), json=first)
    assert r1.status_code == 202

    r2 = client.post("/v1/ingest", headers=headers(with_idem=True, idem_key=key), json=second)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "duplicate_job"


def test_idempotency_same_key_same_body_returns_existing_job() -> None:
    key = "55555555-5555-4555-8555-555555555555"
    payload = {"namespace_id": "ns_idem2", "source_id": "s_same", "source_type": "url", "url": "https://example.com/same content body"}

    r1 = client.post("/v1/ingest", headers=headers(with_idem=True, idem_key=key), json=payload)
    assert r1.status_code == 202
    job_id_1 = r1.json()["job_id"]

    r2 = client.post("/v1/ingest", headers=headers(with_idem=True, idem_key=key), json=payload)
    assert r2.status_code == 202
    assert r2.json()["job_id"] == job_id_1


# ── Namespace stats & delete ──────────────────────────────────────────────────

def test_namespace_stats_after_ingest() -> None:
    ingest_payload = {
        "namespace_id": "ns_stats_test",
        "source_id": "s_stats",
        "source_type": "url",
        "url": "https://example.com/Articolul 1 prevede ceva important. Articolul 2 prevede altceva.",
    }
    client.post("/v1/ingest", headers=headers(with_idem=True, idem_key="66666666-6666-4666-8666-666666666666"), json=ingest_payload)

    r = client.get("/v1/namespaces/ns_stats_test/stats", headers=headers())
    assert r.status_code == 200
    body = r.json()
    assert body["namespace_id"] == "ns_stats_test"
    assert body["chunk_count"] >= 1
    assert body["source_count"] >= 1
    assert "embedding_model" in body
    assert "embedding_dim" in body


def test_namespace_stats_not_found() -> None:
    r = client.get("/v1/namespaces/ns_care_nu_exista_deloc/stats", headers=headers())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "namespace_not_found"


def test_delete_source_not_found() -> None:
    r = client.delete("/v1/namespaces/ns_x/sources/s_nonexistent", headers=headers())
    assert r.status_code == 404


def test_delete_namespace_returns_202() -> None:
    # Ingest first so the namespace exists
    client.post("/v1/ingest", headers=headers(with_idem=True, idem_key="77777777-7777-4777-8777-777777777777"), json={
        "namespace_id": "ns_to_delete",
        "source_id": "s_del",
        "source_type": "url",
        "url": "https://example.com/content to delete",
    })
    r = client.delete("/v1/namespaces/ns_to_delete", headers=headers())
    assert r.status_code == 202
    body = r.json()
    assert body["sla"] == "24h"
    assert body["job_id"].startswith("del_")


# ── Multi-namespace ───────────────────────────────────────────────────────────

def test_multi_namespace_query() -> None:
    for ns, sid, ikey in [
        ("ns_multi_a", "s_ma", "88888888-8888-4888-8888-888888888888"),
        ("ns_multi_b", "s_mb", "99999999-9999-4999-8999-999999999999"),
    ]:
        client.post("/v1/ingest", headers=headers(with_idem=True, idem_key=ikey), json={
            "namespace_id": ns,
            "source_id": sid,
            "source_type": "url",
            "url": f"https://example.com/content for {ns}",
        })

    r = client.post("/v1/query", headers=headers(), json={
        "question": "content for multi namespace",
        "language": "ro",
        "namespaces": ["ns_multi_a", "ns_multi_b"],
        "top_k": 10,
    })
    assert r.status_code == 200
    body = r.json()
    ns_ids = {c["chunk"]["namespace_id"] for c in body["citations"]}
    assert len(ns_ids) >= 1  # at least one namespace has matching chunks


# ── Eval endpoint ─────────────────────────────────────────────────────────────

def test_eval_endpoint() -> None:
    client.post("/v1/ingest", headers=headers(with_idem=True, idem_key="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), json={
        "namespace_id": "ns_eval",
        "source_id": "s_eval",
        "source_type": "url",
        "url": "https://example.com/Articolul 15 prevede ca aporturile in numerar sunt obligatorii.",
    })

    r = client.post("/v1/eval", headers=headers(), json={
        "question": "Ce prevede articolul 15?",
        "language": "ro",
        "namespaces": ["ns_eval"],
        "expected_citations": [],
        "expected_answer_keywords": ["articol"],
    })
    assert r.status_code == 200
    body = r.json()
    assert "eval" in body
    assert "citation_precision_at_k" in body["eval"]
    assert "keyword_match_rate" in body["eval"]


# ── OpenAPI contract ──────────────────────────────────────────────────────────

def test_openapi_json_endpoint() -> None:
    r = client.get("/v1/openapi.json")
    assert r.status_code == 200
