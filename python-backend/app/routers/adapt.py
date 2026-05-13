"""
Adaptation router — trigger adaptation and view logs.
"""
import json

from fastapi import APIRouter, Depends, Query
from ..auth_deps import require_write_access
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AdaptationLog
from ..services.adaptation import run_adaptation

router = APIRouter(prefix="/adapt", tags=["adaptation"])


@router.post("")
def trigger_adaptation(db: Session = Depends(get_db), _w=Depends(require_write_access)):
    result = run_adaptation(db)
    return result


@router.get("/log")
def get_adaptation_log(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(AdaptationLog)
            .order_by(desc(AdaptationLog.evaluated_at))
            .limit(limit)
        ).all()
    )
    return [
        {
            "id": r.id,
            "trades_evaluated": r.trades_evaluated,
            "win_rate": r.win_rate,
            "profit_factor": r.profit_factor,
            "avg_atr": r.avg_atr,
            "actions_taken": r.actions_taken,
            "new_params_version": r.new_params_version,
            "confidence_score": r.confidence_score,
            "delta_magnitude": r.delta_magnitude,
            "rollback_triggered": r.rollback_triggered,
            "strategy_name": r.strategy_name,
            "evaluated_at": r.evaluated_at,
        }
        for r in rows
    ]
