"""
Bridge router — MT5 bridge account and position data.

GET /bridge/account   — account balance/equity (cached when bridge is down)
GET /bridge/positions — live positions (cached when bridge is down)
GET /bridge/status    — circuit breaker state + last-known position count
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from pymongo.database import Database

from ..services.bridge_client import bridge_client, BridgeUnavailableError
from ..auth_deps import require_write_access
from ..db import get_db
from .. import crud

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bridge", tags=["bridge"])


def _bridge_error_response(exc: Exception, endpoint: str) -> None:
    """Convert bridge errors to meaningful HTTP responses instead of 500."""
    msg = str(exc)
    if isinstance(exc, BridgeUnavailableError):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "bridge_unavailable",
                "message": "MT5 bridge is temporarily unreachable (circuit breaker open)",
                "circuit_state": bridge_client.circuit_state,
            },
        )
    if isinstance(exc, httpx.TimeoutException):
        raise HTTPException(
            status_code=504,
            detail={
                "error": "bridge_timeout",
                "message": f"MT5 bridge timed out on {endpoint}",
            },
        )
    raise HTTPException(
        status_code=502,
        detail={"error": "bridge_error", "message": msg},
    )


@router.get("/account")
def get_account(db: Database = Depends(get_db)):
    try:
        result = bridge_client.get_account()
        # Mirror the live login into AppSettings so trade/order data is scoped to
        # this account. Best-effort: never let sync break the account response.
        try:
            crud.sync_active_account(db, result.get("login"))
        except Exception:
            logger.warning("sync_active_account failed", exc_info=True)
        return result
    except Exception as exc:
        _bridge_error_response(exc, "/account")


@router.get("/positions")
def get_positions():
    try:
        return bridge_client.get_positions()
    except Exception as exc:
        _bridge_error_response(exc, "/positions")


@router.get("/status")
def get_bridge_status():
    """
    Returns circuit breaker state and last-known position snapshot.
    Always succeeds — useful for the frontend to show bridge health.
    """
    circuit = bridge_client.circuit_state
    account: dict = {}
    positions: list = []

    if bridge_client.is_available:
        try:
            account = bridge_client.get_account()
        except Exception:
            pass
        try:
            positions = bridge_client.get_positions()
        except Exception:
            pass
    else:
        # Return cached data from the client's internal cache
        try:
            account = bridge_client.get_account()   # returns cached on OPEN
        except Exception:
            pass
        try:
            positions = bridge_client.get_positions()  # returns cached on OPEN
        except Exception:
            pass

    return {
        "circuit_state": circuit,
        "bridge_available": circuit != "OPEN",
        "account": account,
        "open_positions": len(positions),
        "positions": positions,
    }


@router.post("/cancel-order")
def cancel_order(
    ticket: int,
    db: Database = Depends(get_db),
    _w=Depends(require_write_access),
):
    """Cancel a pending (limit/stop) order by ticket — item 16."""
    try:
        result = bridge_client.cancel_order(ticket)
        try:
            from ..services.alerts import notify_trade_operation
            notify_trade_operation(db, "cancel", ticket=ticket, extra={"source": "manual"})
        except Exception:
            pass
        return result
    except Exception as exc:
        _bridge_error_response(exc, "/order/cancel")


@router.get("/tick/{symbol}")
def get_tick(symbol: str):
    """Current bid/ask/spread for a symbol — item 11."""
    try:
        return bridge_client.get_tick(symbol)
    except Exception as exc:
        _bridge_error_response(exc, "/tick")


@router.get("/history")
def get_history(
    from_date: str = Query(..., description="ISO date e.g. 2024-01-01"),
    to_date: str = Query(..., description="ISO date e.g. 2024-12-31"),
    symbol: str | None = Query(default=None),
):
    """Closed deals within a date range (optionally by symbol) — item 16."""
    try:
        return {"deals": bridge_client.get_history(from_date, to_date, symbol)}
    except Exception as exc:
        _bridge_error_response(exc, "/history")