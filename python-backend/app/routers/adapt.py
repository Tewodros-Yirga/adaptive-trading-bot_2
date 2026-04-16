import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AdaptationLog
from ..services.adaptation import run_adaptation

router = APIRouter()


@router.post("/adapt/run")
def adapt_run(window: int = Query(20, ge=5, le=200), db: Session = Depends(get_db)):
    return run_adaptation(db, window=window)


@router.get("/adapt/log")
def adapt_log(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    logs = list(db.scalars(select(AdaptationLog).order_by(desc(AdaptationLog.evaluated_at)).limit(limit)).all())
    return [
        {
            "id": l.id,
            "trades_evaluated": l.trades_evaluated,
            "win_rate": l.win_rate,
            "profit_factor": l.profit_factor,
            "avg_atr": l.avg_atr,
            "actions": json.loads(l.actions_taken) if l.actions_taken else [],
            "new_params_version": l.new_params_version,
            "confidence_score": l.confidence_score,
            "delta_magnitude": l.delta_magnitude,
            "rollback_triggered": bool(l.rollback_triggered),
            "evaluated_at": l.evaluated_at.isoformat() if l.evaluated_at else None,
        }
        for l in logs
    ]
