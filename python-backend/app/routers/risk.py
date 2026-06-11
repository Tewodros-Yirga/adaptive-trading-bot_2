from fastapi import APIRouter, Depends, Query
from ..auth_deps import require_write_access
from pymongo.database import Database
from typing import Optional

from ..db import get_db
from ..services.risk_manager import get_risk_settings, get_risk_status, update_risk_settings

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/settings")
def get_settings(db: Database = Depends(get_db)):
    return get_risk_settings(db)


@router.post("/settings")
def update_settings(body: dict, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    return update_risk_settings(db, body)


@router.get("/status")
def get_status(db: Database = Depends(get_db)):
    return get_risk_status(db)


@router.post("/halt")
def halt_trading(db: Database = Depends(get_db), _w=Depends(require_write_access)):
    update_risk_settings(db, {"trading_halt": True})
    return {"status": "halted", "trading_halt": True}


@router.post("/resume")
def resume_trading(db: Database = Depends(get_db), _w=Depends(require_write_access)):
    update_risk_settings(db, {"trading_halt": False})
    return {"status": "resumed", "trading_halt": False}


# ── Feature 4: Position sizing preview ────────────────────────────────────

@router.get("/lot-size-preview")
def lot_size_preview(
    symbol: str = Query(...),
    entry: float = Query(..., gt=0),
    stop_loss: float = Query(..., gt=0),
    db: Database = Depends(get_db),
):
    """
    Returns what lot sizes would be computed for a given entry+SL without placing any order.
    """
    settings = get_risk_settings(db)
    status = get_risk_status(db)

    sl_pips = abs(round((entry - stop_loss) * 10000, 1))

    fixed_lot = settings.get("fixed_lot_size", 0.01)

    balance = status.get("account_balance") or 10.0
    risk_pct = settings.get("risk_per_trade_pct", 1.0)
    pip_value = settings.get("pip_value_per_lot", 10.0)

    risk_amount_usd = balance * (risk_pct / 100.0)
    dynamic_lot = round(risk_amount_usd / (sl_pips * pip_value), 2) if sl_pips > 0 else fixed_lot
    dynamic_lot = max(0.01, min(dynamic_lot, settings.get("max_lot_size", 10.0)))

    lot_size_mode = settings.get("lot_size_mode", "FIXED")
    chosen_lot = dynamic_lot if lot_size_mode == "DYNAMIC" else fixed_lot

    would_be_blocked = False
    block_reason = None

    if status.get("trading_halt"):
        would_be_blocked = True
        block_reason = "Trading is halted"
    elif (status.get("open_trades_count", 0) or 0) >= (settings.get("max_open_trades", 5) or 5):
        would_be_blocked = True
        block_reason = "Maximum open trades reached"
    elif (status.get("daily_loss_pct", 0) or 0) >= 100:
        would_be_blocked = True
        block_reason = "Daily loss limit reached"

    return {
        "fixed_lot": fixed_lot,
        "dynamic_lot": dynamic_lot,
        "risk_amount_usd": round(risk_amount_usd, 2),
        "sl_pips": sl_pips,
        "lot_size_mode": lot_size_mode,
        "chosen_lot": chosen_lot,
        "would_be_blocked": would_be_blocked,
        "block_reason": block_reason,
    }