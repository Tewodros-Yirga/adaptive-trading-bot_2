from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .. import crud
from ..config import settings
from ..db import get_db
from ..models import ParameterVersion
from ..schemas import WebhookPayload
from ..services.adaptation import run_adaptation
from ..services.bridge_client import bridge_client
from ..services.runtime_settings import get_learning_settings
from ..strategy.dtc import DEFAULT_PARAMS, compute_levels, resolve_params

router = APIRouter()


@router.post("/webhook")
def webhook(payload: WebhookPayload, db: Session = Depends(get_db)):
    if settings.webhook_secret and settings.webhook_secret != "changeme" and payload.secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    signal = payload.signal.upper()
    if signal not in {"BUY", "SELL", "CLOSE"}:
        raise HTTPException(status_code=400, detail=f"Unknown signal: {signal}")
    if signal == "CLOSE":
        return {"status": "CLOSE signal received — manual close not yet wired to broker"}

    current_params = crud.get_current_params(db)
    if not current_params:
        crud.save_params(db, DEFAULT_PARAMS.copy(), reason="Initial defaults", trigger="SYSTEM")
        current_params = DEFAULT_PARAMS.copy()
    params = resolve_params(current_params)

    price = payload.price or 1.0
    levels = compute_levels(signal, price, params)
    order = bridge_client.place_order(
        {
            "symbol": payload.symbol or settings.symbol,
            "direction": signal,
            "lot_size": params.lot_size,
            "stop_loss": levels["sl"],
            "take_profit": levels["tp1"],
            "price": price,
        }
    )

    latest = db.scalar(select(ParameterVersion).order_by(desc(ParameterVersion.version)).limit(1))
    version = latest.version if latest else 1
    trade = crud.log_trade(
        db,
        {
            "symbol": payload.symbol or settings.symbol,
            "direction": signal,
            "entry_price": price,
            "stop_loss": levels["sl"],
            "take_profit": levels["tp1"],
            "lot_size": params.lot_size,
            "result": "OPEN",
            "atr_at_entry": payload.atr,
            "ema_fast_at_entry": payload.ema_fast,
            "ema_slow_at_entry": payload.ema_slow,
            "params_version": version,
            "opened_at": datetime.utcnow(),
        },
    )

    learning = get_learning_settings(db)
    closed_count = len(crud.get_closed_trades(db, 100000))
    adaptation_result = None
    if closed_count > 0 and closed_count % learning["adaptation_interval"] == 0:
        adaptation_result = run_adaptation(db, window=learning["adaptation_min_closed_trades"])

    return {
        "status": "ok",
        "trade_id": trade.id,
        "signal": signal,
        "order": order,
        "adaptation_triggered": adaptation_result is not None,
        "adaptation": adaptation_result,
    }
