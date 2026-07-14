from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from datetime import date
from typing import Literal

from scraper import scheduler_client


JobKind = Literal["grade", "agenda"]


def parse_franchises(value: str) -> list[int]:
    if not value.strip():
        raise ValueError("At least one scheduled franchise is required")
    try:
        franchises = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise ValueError("Scheduled franchises must be comma-separated integers") from exc
    if any(franchise_id <= 0 for franchise_id in franchises):
        raise ValueError("Scheduled franchise IDs must be positive")
    if len(franchises) != len(set(franchises)):
        raise ValueError("Scheduled franchise IDs must be unique")
    return franchises


def parse_kinds(value: str) -> list[JobKind]:
    kinds = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not kinds or any(kind not in {"grade", "agenda"} for kind in kinds):
        raise ValueError("Scheduled job kinds must be grade and/or agenda")
    if len(kinds) != len(set(kinds)):
        raise ValueError("Scheduled job kinds must be unique")
    return kinds  # type: ignore[return-value]


def daily_job_key(day: date, franchise_id: int, kind: JobKind) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"grade-scheduler:{day.isoformat()}:{franchise_id}:{kind}",
        )
    )


async def drain_worker() -> int:
    from scraper.runner import run_worker_once

    completed = 0
    while await run_worker_once():
        completed += 1
    return completed


async def run_pipeline(
    franchises: list[int],
    kinds: list[JobKind],
    *,
    reconcile: bool,
    enqueue: bool,
    drain: bool,
    target_worker_id: str | None,
) -> None:
    if reconcile:
        scheduler_client.reconcile_students()
        print("[pipeline] canonical student reconciliation complete", flush=True)
    if enqueue:
        if not target_worker_id or target_worker_id.strip() != target_worker_id:
            raise ValueError("target_worker_id must be nonblank and unpadded when enqueueing")
        today = date.today()
        for franchise_id in franchises:
            for kind in kinds:
                scheduler_client.enqueue_job(
                    franchise_id=franchise_id,
                    kind=kind,
                    idempotency_key=daily_job_key(today, franchise_id, kind),
                    target_worker_id=target_worker_id,
                )
                print(
                    f"[pipeline] queued {kind} job for franchise {franchise_id}",
                    flush=True,
                )
    if drain:
        completed = await drain_worker()
        print(f"[pipeline] drained {completed} job(s)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the API-only Windows grade pipeline")
    parser.add_argument("--franchise-id", type=int, action="append")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--target-worker", default=os.getenv("WINDOWS_TARGET_WORKER_ID"))
    args = parser.parse_args()

    franchises = args.franchise_id or parse_franchises(
        os.getenv("WINDOWS_SCHEDULED_FRANCHISES", "")
    )
    if any(value <= 0 for value in franchises) or len(franchises) != len(set(franchises)):
        raise ValueError("--franchise-id values must be unique and positive")
    kinds = parse_kinds(os.getenv("WINDOWS_SCHEDULED_JOB_KINDS", "grade,agenda"))
    selected = args.reconcile or args.enqueue or args.drain
    enqueue = args.enqueue or not selected
    if enqueue and not args.target_worker:
        parser.error(
            "--target-worker or WINDOWS_TARGET_WORKER_ID is required when enqueueing"
        )
    if enqueue and args.target_worker.strip() != args.target_worker:
        parser.error("--target-worker must be nonblank and unpadded")
    asyncio.run(
        run_pipeline(
            franchises,
            kinds,
            reconcile=args.reconcile or not selected,
            enqueue=enqueue,
            drain=args.drain or not selected,
            target_worker_id=args.target_worker,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
