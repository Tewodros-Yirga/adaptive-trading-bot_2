import random

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..models import Trade

router = APIRouter()


def _serialize_trade(t: Trade) -> dict:
    return {
        "id": t.id,
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "lot_size": t.lot_size,
        "pnl": t.pnl,
        "result": t.result,
        "duration_mins": t.duration_mins,
        "atr_at_entry": t.atr_at_entry,
        "ema_fast_at_entry": t.ema_fast_at_entry,
        "ema_slow_at_entry": t.ema_slow_at_entry,
        "params_version": t.params_version,
        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


@router.get("/trades")
def get_trades(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    return [_serialize_trade(t) for t in crud.get_recent_trades(db, limit)]


@router.get("/trades/stats")
def get_stats(db: Session = Depends(get_db)):
    return crud.get_stats(db)


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, db: Session = Depends(get_db)):
    trade = db.get(Trade, trade_id)
    if not trade:
        return {"error": "Trade not found"}
    if trade.result != "OPEN":
        return {"error": "Trade already closed"}
    hit_tp = random.random() > 0.45
    exit_price = trade.take_profit if hit_tp else trade.stop_loss
    pnl = (abs((exit_price or trade.entry_price) - trade.entry_price) * trade.lot_size * 100000) * (1 if hit_tp else -1)
    result = "WIN" if hit_tp else "LOSS"
    updated = crud.close_trade(db, trade_id, round(exit_price or trade.entry_price, 5), round(pnl, 2), result)
    return _serialize_trade(updated)
