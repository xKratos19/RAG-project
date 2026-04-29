from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


BASE_URL = "http://localhost:8080"
HEADERS = {
    "Authorization": "Bearer dev-api-key",
    "X-Tenant-ID": "ph-balta-doamnei",
}


@dataclass
class EvalResult:
    total: int
    empty_result_pass: int
    article_hit_pass: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "empty_result_rate": self.empty_result_pass / max(self.total, 1),
            "exact_article_hit_rate": self.article_hit_pass / max(self.total, 1),
        }


def cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "case1",
            "question": "Ce spune articolul 15 din Legea 31/1990?",
            "language": "ro",
            "namespaces": ["legea_31_1990"],
            "hint_article_number": "15",
            "expected_article": "15",
        },
        {
            "id": "case2",
            "question": "Cum se constituie o societate cu răspundere limitată?",
            "language": "ro",
            "namespaces": ["legea_31_1990", "cod_civil"],
        },
        {
            "id": "case3",
            "question": "Care este programul primăriei Bălta Doamnei?",
            "language": "ro",
            "namespaces": ["legea_31_1990"],
            "expect_empty": True,
        },
    ]


def main() -> None:
    result = EvalResult(total=0, empty_result_pass=0, article_hit_pass=0)
    output: list[dict[str, Any]] = []
    with httpx.Client(base_url=BASE_URL, timeout=20.0) as client:
        for idx, case in enumerate(cases(), start=1):
            request_id = f"10000000-0000-4000-8000-{idx:012d}"
            headers = {**HEADERS, "X-Request-ID": request_id}
            payload = {k: v for k, v in case.items() if k not in {"id", "expect_empty", "expected_article"}}
            response = client.post("/v1/query", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            result.total += 1
            if case.get("expect_empty"):
                if body.get("answer") is None and body.get("citations") == [] and body.get("confidence") == 0.0:
                    result.empty_result_pass += 1
            if case.get("expected_article"):
                citations = body.get("citations", [])
                if any(c.get("chunk", {}).get("article_number") == case["expected_article"] for c in citations):
                    result.article_hit_pass += 1
            output.append({"case": case["id"], "response": body})

    report = {"summary": result.as_dict(), "details": output}
    report_path = Path("eval-report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {report_path}")


if __name__ == "__main__":
    main()
