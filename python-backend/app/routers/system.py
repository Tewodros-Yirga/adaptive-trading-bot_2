"""
app/routers/system.py — System health and diagnostics endpoints.
"""
import time
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pymongo.database import Database

from ..db import get_db, COLL_TRADES
from ..auth_deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

_START_TIME = time.time()


@router.get("/health")
async def health(
    request: Request,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    startup_checks: list[dict] = getattr(request.app.state, "startup_checks", [])

    has_error = any(c.get("status") == "ERROR" for c in startup_checks)
    has_warn = any(c.get("status") == "WARN" for c in startup_checks)
    if has_error:
        overall = "ERROR"
    elif has_warn:
        overall = "DEGRADED"
    else:
        overall = "OK"

    live: dict = {}
    try:
        from .. import crud
        active = crud.get_active_strategies(db)
        live["active_strategies"] = len(active)
    except Exception:
        live["active_strategies"] = None

    try:
        live["open_trades"] = db[COLL_TRADES].count_documents({"result": None})
    except Exception:
        live["open_trades"] = None

    try:
        db.command("ping")
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