"""
Webhook router — receives trading signals from TradingView / external sources.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import WebhookPayload
from ..crud import log_trade, get_current_params

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/signal")
def receive_signal(payload: WebhookPayload, db: Session = Depends(get_db)):
    """
    Ingest a trading signal from TradingView or another alert source.
    Validates the shared secret, then logs the signal as a new trade.
    """
    # ── Validate secret ───────────────────────────────────────────────────
    if settings.webhook_secret != "changeme" and payload.secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    symbol = payload.symbol or settings.symbol
    logger.info(f"Webhook signal received: {payload.signal} for {symbol}")

    # ── Map signal to direction ───────────────────────────────────────────
    signal_upper = payload.signal.strip().upper()
    if signal_upper in ("BUY", "LONG"):
        direction = "BUY"
    elif signal_upper in ("SELL", "SHORT"):
        direction = "SELL"
    elif signal_upper in ("CLOSE", "EXIT", "FLATTEN"):
        # Close signals are acknowledged but don't open a new trade
        return {"status": "ok", "action": "close_signal_received", "signal": signal_upper}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown signal: {payload.signal}")

    # ── Build trade record ────────────────────────────────────────────────
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

    # Compute stop-loss / take-profit if ATR is available
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
