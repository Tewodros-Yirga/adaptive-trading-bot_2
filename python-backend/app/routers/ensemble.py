"""
Ensemble Decision Log Router
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ..auth_deps import get_current_user, require_write_access
from ..db import get_db
from .. import crud
from ..services.orchestrator import get_ensemble_config, set_ensemble_config

router = APIRouter(prefix="/ensemble", tags=["ensemble"])


@router.get("/decisions")
def list_decisions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    db: Database = Depends(get_db),
):
    decisions = crud.get_ensemble_decisions(db, page=page, limit=limit, symbol=symbol)
    return [
        {
            "id": d.id,
            "symbol": d.symbol,
            "timestamp": d.timestamp,
            "resolved_direction": d.resolved_direction,
            "resolved_confidence": d.resolved_confidence,
            "trade_id": d.trade_id,
            "strategy_votes": d.strategy_votes_json,
            "final_entry": d.final_entry,
            "final_sl": d.final_sl,
            "final_tp1": d.final_tp1,
            "final_tp2": d.final_tp2,
            "final_tp3": d.final_tp3,
            "final_tp4": d.final_tp4,
            "news_bias": d.news_bias,
            "news_blocked": d.news_blocked,
            "risk_blocked": d.risk_blocked,
            "block_reason": d.block_reason,
        }
        for d in decisions
    ]


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: int, db: Database = Depends(get_db)):
    d = crud.get_ensemble_decision(db, decision_id)
    if not d:
        raise HTTPException(404, "Ensemble decision not found")
    return {
        "id": d.id,
        "symbol": d.symbol,
        "timestamp": d.timestamp,
        "resolved_direction": d.resolved_direction,
        "resolved_confidence": d.resolved_confidence,
        "trade_id": d.trade_id,
        "strategy_votes": d.strategy_votes_json,
        "final_entry": d.final_entry,
        "final_sl": d.final_sl,
        "final_tp1": d.final_tp1,
        "final_tp2": d.final_tp2,
        "final_tp3": d.final_tp3,
        "final_tp4": d.final_tp4,
        "news_bias": d.news_bias,
        "news_blocked": d.news_blocked,
        "risk_blocked": d.risk_blocked,
        "block_reason": d.block_reason,
    }


@router.get("/config")
def get_config(db: Database = Depends(get_db)):
    return get_ensemble_config(db)


@router.post("/config")
def update_config(
    body: dict,
    db: Database = Depends(get_db),
    _w=Depends(require_write_access),
):
    return set_ensemble_config(db, body)