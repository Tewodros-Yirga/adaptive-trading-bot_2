"""
Webhook router — receives trading signals from TradingView / external sources.
"""
import logging
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pymongo.database import Database

from ..config import settings
from ..db import get_db
from ..schemas import WebhookPayload
from ..crud import log_trade, get_current_params, close_trade as crud_close_trade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

# ── In-memory rate limiter ─────────────────────────────────────────────────
_signal_timestamps: deque = deque()
_symbol_timestamps: dict[str, deque] = {}

_GLOBAL_LIMIT = 60
_SYMBOL_LIMIT = 10
_WINDOW_SECS  = 60


def _check_rate_limit(symbol: str) -> tuple[bool, str]:
    now = time.monotonic()
    cutoff = now - _WINDOW_SECS

    while _signal_timestamps and _signal_timestamps[0] < cutoff:
        _signal_timestamps.popleft()

    if symbol not in _symbol_timestamps:
        _symbol_timestamps[symbol] = deque()
    sym_dq = _symbol_timestamps[symbol]
    while sym_dq and sym_dq[0] < cutoff:
        sym_dq.popleft()

    if len(_signal_timestamps) >= _GLOBAL_LIMIT:
        return False, f"Global rate limit: max {_GLOBAL_LIMIT} signals/{_WINDOW_SECS}s"
    if len(sym_dq) >= _SYMBOL_LIMIT:
        return False, f"Symbol rate limit: max {_SYMBOL_LIMIT} signals/{_WINDOW_SECS}s for {symbol}"

    _signal_timestamps.append(now)
    sym_dq.append(now)
    return True, ""


@router.post("/signal")
def receive_signal(payload: WebhookPayload, db: Database = Depends(get_db)):
    if settings.webhook_secret != "changeme" and payload.secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    symbol = payload.symbol or settings.symbol

    allowed, reason = _check_rate_limit(symbol)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": reason, "retry_after": _WINDOW_SECS},
            headers={"Retry-After": str(_WINDOW_SECS)},
        )

    logger.info(f"Webhook signal received: {payload.signal} for {symbol}")

    signal_upper = payload.signal.strip().upper()
    if signal_upper in ("BUY", "LONG"):
        direction = "BUY"
    elif signal_upper in ("SELL", "SHORT"):
        direction = "SELL"
    elif signal_upper in ("CLOSE", "EXIT", "FLATTEN"):
        return {"status": "ok", "action": "close_signal_received", "signal": signal_upper}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown signal: {payload.signal}")

    params = get_current_params(db) or {}
    lot_size = params.get("lot_size", 0.01)

    trade_fields = {
        "symbol": symbol,
        "direction": direction,
        "entry_price": payload.price,
        "lot_size": lot_size,
        "atr_at_entry": payload.atr,
        "ema_fast_at_entry": payload.ema_fast,
        "ema_slow_at_entry": payload.ema_slow,
    }

    if payload.atr and payload.atr > 0:
        sl_mult = params.get("sl_atr_multiplier", 1.5)
        tp_mult = params.get("tp_atr_multiplier", 2.0)
        if direction == "BUY":
            trade_fields["stop_loss"] = round(payload.price - payload.atr * sl_mult, 5)
            trade_fields["take_profit"] = round(payload.price + payload.atr * tp_mult, 5)
        else:
            trade_fields["stop_loss"] = round(payload.price + payload.atr * sl_mult, 5)
            trade_fields["take_profit"] = round(payload.price - payload.atr * tp_mult, 5)

    trade = log_trade(db, trade_fields)
    logger.info(f"Trade #{trade.id} opened: {direction} {symbol} @ {payload.price}")

    return {
        "status": "ok",
        "trade_id": trade.id,
        "direction": direction,
        "symbol": symbol,
        "entry_price": payload.price,
    }


@router.post("/close")
def close_trade_webhook(payload: dict, db: Database = Depends(get_db)):
    if settings.webhook_secret != "changeme" and payload.get("secret") != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    trade_id: int | None = payload.get("trade_id")
    exit_price: float | None = payload.get("exit_price")
    pnl: float | None = payload.get("pnl")
    result: str | None = (payload.get("result") or "").upper()

    if not trade_id:
        raise HTTPException(status_code=400, detail="trade_id is required")
    if exit_price is None:
        raise HTTPException(status_code=400, detail="exit_price is required")
    if pnl is None:
        raise HTTPException(status_code=400, detail="pnl is required")
    if result not in ("WIN", "LOSS"):
        raise HTTPException(status_code=400, detail="result must be WIN or LOSS")

    trade = crud_close_trade(db, trade_id, exit_price, pnl, result)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade #{trade_id} not found")

    # Trade-close learning: strategy score feedback + news intelligence.
    try:
        from ..services.score_feedback import run_trade_close_hooks
        run_trade_close_hooks(db, trade)
    except Exception as exc:
        logger.warning(f"Trade-close hooks failed for trade #{trade_id}: {exc}")

    return {"status": "ok", "trade_id": trade.id, "result": result, "pnl": pnl}