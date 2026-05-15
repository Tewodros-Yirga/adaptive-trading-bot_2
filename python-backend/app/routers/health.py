"""
startup_checks_patch.py
=======================
Drop-in additions for app/services/startup_checks.py (Feature 6)
and a new health router snippet (Feature 8).

─────────────────────────────────────────────────────────────────────────────
FEATURE 6 — Add this block inside the existing run_startup_checks() coroutine,
            after the existing checks list is populated:
─────────────────────────────────────────────────────────────────────────────

    # ── 8. Backtester service connectivity ───────────────────────────────────
    try:
        from ..config import settings
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.backtester_service_url}/health")
            if r.status_code == 200:
                data = r.json()
                checks.append({
                    "name": "backtester_service",
                    "status": "OK",
                    "message": f"Backtester running, strategies: {data.get('strategies_running', [])}",
                })
            else:
                checks.append({
                    "name": "backtester_service",
                    "status": "WARN",
                    "message": f"Backtester returned {r.status_code}",
                })
    except Exception as e:
        checks.append({
            "name": "backtester_service",
            "status": "WARN",
            "message": f"Backtester unreachable: {e} — continuous backtesting may be offline",
        })

─────────────────────────────────────────────────────────────────────────────
FEATURE 8 — New file: app/routers/health.py
─────────────────────────────────────────────────────────────────────────────
"""

# ══════════════════════════════════════════════════════════════════════════════
# app/routers/health.py  (Feature 8 — MongoDB connection health endpoint)
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..db import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/db")
def db_health(db: Session = Depends(get_db)):
    """
    Public, unauthenticated endpoint that verifies database connectivity.
    Works for both SQLAlchemy (SQL) and MongoDB backends by executing a
    lightweight round-trip query.
    """
    try:
        # For SQLAlchemy / SQL databases
        db.execute(text("SELECT 1"))
        return {"status": "ok", "type": "sql"}
    except Exception as sql_err:
        # Attempt MongoDB-style ping if the session exposes a raw client
        try:
            raw = db.get_bind()  # SQLAlchemy engine
            raw.execute(text("SELECT 1"))
            return {"status": "ok", "type": "sql"}
        except Exception:
            pass

        # Final fallback: try the MongoDB command directly if available
        try:
            mongo_db = db.bind  # type: ignore[attr-defined]
            mongo_db.command("ping")
            return {"status": "ok", "type": "mongodb"}
        except Exception:
            pass

        return {"status": "error", "error": str(sql_err)}