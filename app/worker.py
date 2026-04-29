from __future__ import annotations

from arq.connections import RedisSettings

from app.config import settings
from app.logging_utils import configure_logging

configure_logging()


async def ingest_background(
    ctx: dict,
    job_id: str,
    tenant_id: str,
    payload_dict: dict,
    file_content_type: str | None = None,
) -> None:
    from app.services_ingest import run_ingest_task

    await run_ingest_task(job_id, tenant_id, payload_dict, file_content_type)


class WorkerSettings:
    functions = [ingest_background]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
    job_timeout = 600
    keep_result = 3600
    handle_signals = True
