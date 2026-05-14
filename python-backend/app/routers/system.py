"""
app/routers/system.py

System health and diagnostics endpoints.
"""
import os
import time
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..auth_deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# Process start time for uptime calculation
_START_TIME = time.time()


@router.get("/health")
async def health(
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """
    Returns startup check results plus live system stats.

    Shape:
    {
        "status": "OK" | "DEGRADED" | "ERROR",
        "uptime_seconds": float,
        "startup_checks": [...],
        "live": {
            "active_strategies": int,
            "open_trades": int,
            "db_ok": bool,
        }
    }
    """
    # Retrieve cached startup checks from app state (populated at startup)
    startup_checks: list[dict] = getattr(request.app.state, "startup_checks", [])

    # Derive overall status
    has_error = any(c.get("status") == "ERROR" for c in startup_checks)
    has_warn = any(c.get("status") == "WARN" for c in startup_checks)
    if has_error:
        overall = "ERROR"
    elif has_warn:
        overall = "DEGRADED"
    else:
        overall = "OK"

    # Live stats (best-effort — never raise)
    live: dict = {}
    try:
        from .. import crud
        active = crud.get_active_strategies(db)
        live["active_strategies"] = len(active)
    except Exception:
        live["active_strategies"] = None

    try:
        from sqlalchemy import select, func
        from ..models import Trade
        from sqlalchemy import text
        result = db.execute(
            text("SELECT COUNT(*) FROM trades WHERE result IS NULL")
        )
        live["open_trades"] = result.scalar() or 0
    except Exception:
        live["open_trades"] = None

    try:
        from sqlalchemy import text as _text
        db.execute(_text("SELECT 1"))
        live["db_ok"] = True
    except Exception:
        live["db_ok"] = False

    live["timestamp_utc"] = datetime.utcnow().isoformat() + "Z"

    return {
        "status": overall,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "startup_checks": startup_checks,
        "live": live,
    }