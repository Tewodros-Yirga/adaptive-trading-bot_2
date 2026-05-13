"""
Shadow signals router — view shadow (non-live) strategy signals.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ShadowSignal

router = APIRouter(tags=["shadow_signals"])


@router.get("/shadow-signals")
def list_shadow_signals(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(ShadowSignal)
            .order_by(desc(ShadowSignal.signal_time))
            .limit(limit)
        ).all()
    )
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
