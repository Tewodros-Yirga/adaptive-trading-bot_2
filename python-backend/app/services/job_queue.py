"""
DB-backed job queue (item 19).

A lightweight MongoDB-backed work queue that gives lower-latency, retry-capable
task handoff as an alternative to fixed-interval polling loops. It is ADDITIVE
infrastructure: the existing loops are untouched, and the worker is INERT until
``job_queue_enabled`` is truthy AND something enqueues jobs — so it changes
nothing in a running system by default.

Pattern:
    from .services.job_queue import enqueue_job, register_handler

    @register_handler("reconcile_ticket")
    def _handle(db, payload):
        ...                      # do the work; return a JSON-serialisable result

    enqueue_job(db, "reconcile_ticket", {"ticket": 12345}, dedup_key="recon:12345")

Claiming is atomic (find_one_and_update), so multiple workers/processes never
double-process a job. Failures retry with exponential backoff up to
``max_attempts`` and then land in status="failed" for inspection.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pymongo import ReturnDocument
from pymongo.database import Database

from .. import crud
from ..db import get_database

logger = logging.getLogger(__name__)

COLL_JOBS = "jobs"

# type -> handler(db, payload) -> result
_HANDLERS: dict[str, Callable[[Database, dict], Any]] = {}


def register_handler(job_type: str):
    """Decorator to register a handler for a job type."""
    def _wrap(fn: Callable[[Database, dict], Any]):
        _HANDLERS[job_type] = fn
        return fn
    return _wrap


def _truthy(v) -> bool:
    return str(v or "").lower() in ("1", "true", "yes", "on")


def enqueue_job(
    db: Database,
    job_type: str,
    payload: dict | None = None,
    *,
    dedup_key: str | None = None,
    max_attempts: int = 3,
    delay_seconds: float = 0.0,
):
    """Insert a pending job. If ``dedup_key`` matches an already pending/processing
    job, the existing job id is returned instead of creating a duplicate."""
    now = datetime.now(timezone.utc)
    if dedup_key:
        existing = db[COLL_JOBS].find_one(
            {"dedup_key": dedup_key, "status": {"$in": ["pending", "processing"]}}
        )
        if existing:
            return existing["_id"]
    doc = {
        "type": job_type,
        "payload": payload or {},
        "status": "pending",
        "dedup_key": dedup_key,
        "attempts": 0,
        "max_attempts": int(max_attempts),
        "next_run_at": now + timedelta(seconds=max(0.0, delay_seconds)),
        "created_at": now,
        "updated_at": now,
        "last_error": None,
        "result": None,
    }
    return db[COLL_JOBS].insert_one(doc).inserted_id


def claim_next_job(db: Database, worker_id: str) -> dict | None:
    """Atomically claim the next due pending job (or None)."""
    now = datetime.now(timezone.utc)
    return db[COLL_JOBS].find_one_and_update(
        {"status": "pending", "next_run_at": {"$lte": now}},
        {
            "$set": {"status": "processing", "worker_id": worker_id, "updated_at": now},
            "$inc": {"attempts": 1},
        },
        sort=[("next_run_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def complete_job(db: Database, job_id, result: Any = None) -> None:
    db[COLL_JOBS].update_one(
        {"_id": job_id},
        {"$set": {"status": "done", "result": result, "last_error": None,
                  "updated_at": datetime.now(timezone.utc)}},
    )


def fail_job(db: Database, job, error: str) -> None:
    """Reschedule with exponential backoff, or mark failed once attempts exhausted."""
    attempts = int(job.get("attempts", 1))
    max_attempts = int(job.get("max_attempts", 3))
    now = datetime.now(timezone.utc)
    if attempts < max_attempts:
        backoff = min(300.0, 2.0 ** attempts)
        db[COLL_JOBS].update_one(
            {"_id": job["_id"]},
            {"$set": {"status": "pending", "last_error": error,
                      "next_run_at": now + timedelta(seconds=backoff), "updated_at": now}},
        )
    else:
        db[COLL_JOBS].update_one(
            {"_id": job["_id"]},
            {"$set": {"status": "failed", "last_error": error, "updated_at": now}},
        )


async def _dispatch(handler: Callable, db: Database, payload: dict) -> Any:
    if inspect.iscoroutinefunction(handler):
        return await handler(db, payload)
    return await asyncio.to_thread(handler, db, payload)


async def start_job_worker(poll_seconds: float = 2.0) -> None:
    """Background worker loop. Inert until job_queue_enabled is truthy."""
    worker_id = f"{socket.gethostname()}:{id(object())}"
    logger.info("job_queue worker started (inert until job_queue_enabled=true).")
    while True:
        try:
            db = get_database()
            if not _truthy(crud.get_setting(db, "job_queue_enabled")):
                await asyncio.sleep(5.0)
                continue
            job = await asyncio.to_thread(claim_next_job, db, worker_id)
            if not job:
                await asyncio.sleep(poll_seconds)
                continue
            handler = _HANDLERS.get(job["type"])
            if handler is None:
                await asyncio.to_thread(fail_job, db, {**job, "attempts": job.get("max_attempts", 3)},
                                        f"no handler registered for type '{job['type']}'")
                continue
            try:
                result = await _dispatch(handler, db, job.get("payload") or {})
                await asyncio.to_thread(complete_job, db, job["_id"], result)
            except Exception as exc:
                logger.warning("job_queue: job %s (%s) failed: %s", job.get("_id"), job.get("type"), exc)
                await asyncio.to_thread(fail_job, db, job, str(exc))
        except asyncio.CancelledError:
            logger.info("job_queue worker cancelled.")
            return
        except Exception as exc:
            logger.debug("job_queue worker loop error: %s", exc)
            await asyncio.sleep(poll_seconds)
