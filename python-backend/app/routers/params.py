"""
Params router — learning settings and parameter version history.
"""
import json

from fastapi import APIRouter, Depends, Query
from ..auth_deps import require_write_access
from sqlalchemy.orm import Session

from ..db import get_db
from .. import crud
from ..services.runtime_settings import get_learning_settings, update_learning_settings

router = APIRouter(prefix="/params", tags=["params"])


@router.get("/learning")
def get_learning(db: Session = Depends(get_db)):
    return get_learning_settings(db)


@router.post("/learning")
def update_learning(body: dict, db: Session = Depends(get_db), _w=Depends(require_write_access)):
    return update_learning_settings(db, body)


@router.get("/history")
def params_history(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    history = crud.get_params_history(db, limit)
    return [
        {
            "version": p.version,
            "params": json.loads(p.params_json),
            "reason": p.reason,
            "trigger": p.trigger,
            "confidence_score": p.confidence_score,
            "delta_magnitude": p.delta_magnitude,
            "created_at": p.created_at,
        }
        for p in history
    ]
