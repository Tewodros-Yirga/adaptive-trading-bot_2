"""
Trades router — exposes trade queries to the frontend.
"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from .. import crud

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
def list_trades(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    trades = crud.get_recent_trades(db, limit)
    return [
        {
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
            "strategy_name": t.strategy_name,
            "params_version": t.params_version,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
        }
        for t in trades
    ]


@router.get("/stats")
def trade_stats(db: Session = Depends(get_db)):
    return crud.get_stats(db)


@router.get("/closed")
def closed_trades(limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    trades = crud.get_closed_trades(db, limit)
    return [
        {
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
            "strategy_name": t.strategy_name,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
        }
        for t in trades
    ]
