"""
Adaptation router — trigger adaptation and view logs.
"""
from fastapi import APIRouter, Depends, Query
from ..auth_deps import require_write_access
from pymongo.database import Database

from ..db import get_db, COLL_ADAPTATION_LOGS
from ..models import AdaptationLog
from ..services.adaptation import run_adaptation

router = APIRouter(prefix="/adapt", tags=["adaptation"])


@router.post("")
def trigger_adaptation(db: Database = Depends(get_db), _w=Depends(require_write_access)):
    result = run_adaptation(db)
    return result


@router.get("/log")
def get_adaptation_log(limit: int = Query(30, ge=1, le=200), db: Database = Depends(get_db)):
    docs = db[COLL_ADAPTATION_LOGS].find().sort("evaluated_at", -1).limit(limit)
    rows = [AdaptationLog.from_doc(d) for d in docs]
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