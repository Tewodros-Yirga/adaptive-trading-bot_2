import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..services.runtime_settings import get_learning_settings, update_learning_settings
from ..strategy.dtc import DEFAULT_PARAMS

router = APIRouter()


@router.get("/params")
def get_params(db: Session = Depends(get_db)):
    return crud.get_current_params(db) or DEFAULT_PARAMS


@router.post("/params")
def set_params(payload: dict, db: Session = Depends(get_db)):
    current = crud.get_current_params(db) or DEFAULT_PARAMS.copy()
    merged = current | payload
    row = crud.save_params(db, merged, reason="Manual override via API", trigger="MANUAL")
    return {"version": row.version, "params": merged}


@router.get("/params/history")
def params_history(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    history = crud.get_params_history(db, limit)
    return [
        {
            "version": h.version,
            "params": json.loads(h.params_json),
            "reason": h.reason,
            "trigger": h.trigger,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        }
        for h in history
    ]


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return get_learning_settings(db)


@router.post("/settings")
def set_settings(payload: dict, db: Session = Depends(get_db)):
    updated = update_learning_settings(db, payload)
    latest = crud.get_params_history(db, 1)
    crud.log_adaptation(
        db,
        {
            "trades_evaluated": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_atr": None,
            "actions_taken": json.dumps(
                [{"rule": "settings_update", "detail": "Learning/stability controls updated from dashboard/API"}]
            ),
            "new_params_version": latest[0].version if latest else 1,
            "confidence_score": None,
            "delta_magnitude": None,
            "rollback_triggered": 0,
        },
    )
    return updated
