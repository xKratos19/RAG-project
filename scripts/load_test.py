from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

import httpx


BASE_URL = "http://localhost:8080"
HEADERS = {
    "Authorization": "Bearer dev-api-key",
    "X-Tenant-ID": "ph-balta-doamnei",
}


async def run_single(client: httpx.AsyncClient, idx: int) -> float:
    request_id = f"00000000-0000-4000-8000-{idx:012d}"
    headers = {**HEADERS, "X-Request-ID": request_id}
    payload: dict[str, Any] = {
        "question": "Ce spune articolul 15 din Legea 31/1990?",
        "language": "ro",
        "namespaces": ["legea_31_1990"],
        "top_k": 5,
        "hint_article_number": "15",
    }
    started = time.perf_counter()
    response = await client.post("/v1/query", headers=headers, json=payload)
    response.raise_for_status()
    return (time.perf_counter() - started) * 1000


async def main() -> None:
    total = 200
    concurrency = 50
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20.0) as client:
        async def guarded(i: int) -> float:
            async with semaphore:
                return await run_single(client, i)

        latencies = await asyncio.gather(*[guarded(i) for i in range(total)])

    p50 = statistics.quantiles(latencies, n=100)[49]
    p95 = statistics.quantiles(latencies, n=100)[94]
    p99 = statistics.quantiles(latencies, n=100)[98]
    print(f"requests={total} concurrency={concurrency}")
    print(f"p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
