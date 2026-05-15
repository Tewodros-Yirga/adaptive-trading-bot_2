"""
Shadow signals router — view shadow (non-live) strategy signals.
"""
from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from ..db import get_db, COLL_SHADOW_SIGNALS
from ..models import ShadowSignal

router = APIRouter(tags=["shadow_signals"])


@router.get("/shadow-signals")
def list_shadow_signals(limit: int = Query(50, ge=1, le=500), db: Database = Depends(get_db)):
    docs = db[COLL_SHADOW_SIGNALS].find().sort("signal_time", -1).limit(limit)
    rows = [ShadowSignal.from_doc(d) for d in docs]
    return [
        {
            "id": r.id,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "direction": r.direction,
            "entry_price": r.entry_price,
            "sl": r.sl,
            "tp1": r.tp1,
            "confidence": r.confidence,
            "signal_time": r.signal_time,
            "would_have_resulted_in": r.would_have_resulted_in,
            "actual_exit_price": r.actual_exit_price,
            "pnl_hypothetical": r.pnl_hypothetical,
        }
        for r in rows
    ]