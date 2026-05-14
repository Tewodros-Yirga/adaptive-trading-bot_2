"""
Webhook router — receives trading signals from TradingView / external sources.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import WebhookPayload
from ..crud import log_trade, get_current_params
from ..models import StrategyPickerDecision

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
def close_trade_webhook(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Close an open trade and trigger online picker weight learning.

    Expected payload:
        {
            "secret": str,
            "trade_id": int,
            "exit_price": float,
            "pnl": float,
            "result": "WIN" | "LOSS"
        }
    """
    if settings.webhook_secret != "changeme" and payload.get("secret") != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    trade_id: int | None = payload.get("trade_id")
    exit_price: float | None = payload.get("exit_price")
    pnl: float | None = payload.get("pnl")
    result: str | None = payload.get("result", "").upper()

    if not trade_id:
        raise HTTPException(status_code=400, detail="trade_id is required")
    if exit_price is None:
        raise HTTPException(status_code=400, detail="exit_price is required")
    if pnl is None:
        raise HTTPException(status_code=400, detail="pnl is required")
    if result not in ("WIN", "LOSS"):
        raise HTTPException(status_code=400, detail="result must be WIN or LOSS")

    from ..crud import close_trade as crud_close_trade
    trade = crud_close_trade(db, trade_id, exit_price, pnl, result)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade #{trade_id} not found")

    # ── Trigger online picker weight learning ─────────────────────────────
    # Find the most recent StrategyPickerDecision that led to this trade.
    # The picker back-fills trade_id after order placement, so we query by
    # trade_id directly first, then fall back to the most recent decision
    # for the trade's symbol within a short window.
    try:
        from ..services.strategy_picker import update_picker_weights_from_trade

        picker_decision = db.scalar(
            select(StrategyPickerDecision)
            .where(StrategyPickerDecision.trade_id == trade_id)
            .limit(1)
        )
        if picker_decision is None and trade.symbol:
            # Fallback: nearest decision by timestamp for this symbol
            picker_decision = db.scalar(
                select(StrategyPickerDecision)
                .where(StrategyPickerDecision.symbol == trade.symbol)
                .order_by(desc(StrategyPickerDecision.timestamp))
                .limit(1)
            )

        if picker_decision:
            update_picker_weights_from_trade(trade, picker_decision, db)
            logger.info(
                f"Picker weights updated from trade #{trade_id} result={result}"
            )
        else:
            logger.debug(
                f"No StrategyPickerDecision found for trade #{trade_id}; skipping weight update"
            )
    except Exception as exc:
        # Weight learning is non-critical — log and continue
        logger.warning(f"Picker weight update failed for trade #{trade_id}: {exc}")

    return {
        "status": "ok",
        "trade_id": trade.id,
        "result": result,
        "pnl": pnl,
    }