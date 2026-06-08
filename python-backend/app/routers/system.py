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


@router.get("/services")
async def services_health(
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Aggregated reachability of every dependency (item 7).

    Each probe is isolated so one failing service never breaks the response.
    Returns a per-service {ok, detail} map plus an overall flag the frontend
    can render as a single health tile.
    """
    services: dict[str, dict] = {}

    # ── MongoDB ───────────────────────────────────────────────────────────
    try:
        db.command("ping")
        services["mongodb"] = {"ok": True, "detail": "ping ok"}
    except Exception as exc:
        services["mongodb"] = {"ok": False, "detail": str(exc)}

    # ── MT5 bridge (circuit-breaker state + cached account) ───────────────
    try:
        from ..services.bridge_client import bridge_client
        state = bridge_client.circuit_state
        try:
            acct = bridge_client.get_account()
        except Exception:
            acct = None
        services["mt5_bridge"] = {
            "ok": state != "OPEN",
            "circuit_state": state,
            "detail": "circuit OPEN — bridge unreachable" if state == "OPEN" else "reachable",
            "balance": (acct or {}).get("balance"),
        }
    except Exception as exc:
        services["mt5_bridge"] = {"ok": False, "detail": str(exc)}

    # ── Backtester service ────────────────────────────────────────────────
    try:
        from ..services.backtester_client import BacktesterClient, BacktesterUnavailable
        client = BacktesterClient()
        if not client.is_configured:
            services["backtester"] = {"ok": None, "detail": "not configured"}
        else:
            try:
                pong = await client.ping()
                services["backtester"] = {"ok": True, "detail": "ping ok",
                                          "uptime_seconds": (pong or {}).get("uptime_seconds")}
            except BacktesterUnavailable as exc:
                services["backtester"] = {"ok": None, "detail": str(exc)}
            except Exception as exc:
                services["backtester"] = {"ok": False, "detail": str(exc)}
    except Exception as exc:
        services["backtester"] = {"ok": False, "detail": str(exc)}

    # overall: True only if no service is explicitly down (None = n/a is tolerated)
    overall_ok = all(s.get("ok") is not False for s in services.values())
    return {
        "overall_ok": overall_ok,
        "services": services,
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }