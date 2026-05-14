"""
Ensemble Decision Log Router
Provides read access to all EnsembleDecision records and ensemble config management.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth_deps import get_current_user, require_write_access
from ..db import get_db
from .. import crud
from ..schemas import EnsembleDecisionOut
from ..services.orchestrator import get_ensemble_config, set_ensemble_config

router = APIRouter(prefix="/ensemble", tags=["ensemble"])


# ---------------------------------------------------------------------------
# GET /ensemble/decisions
# ---------------------------------------------------------------------------

@router.get("/decisions")
def list_decisions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Paginated list of ensemble decision records."""
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


# ---------------------------------------------------------------------------
# GET /ensemble/decisions/{id}
# ---------------------------------------------------------------------------

@router.get("/decisions/{decision_id}")
def get_decision(decision_id: int, db: Session = Depends(get_db)):
    """Single ensemble decision detail."""
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


# ---------------------------------------------------------------------------
# GET /ensemble/config
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    """Return current ensemble configuration (with automatic DOMINANT→WEIGHTED_VOTE migration)."""
    return get_ensemble_config(db)


# ---------------------------------------------------------------------------
# POST /ensemble/config
# ---------------------------------------------------------------------------

@router.post("/config")
def update_config(
    body: dict,
    db: Session = Depends(get_db),
    _w=Depends(require_write_access),
):
    """
    Update ensemble configuration.
    If mode=DOMINANT is provided, it is automatically migrated to WEIGHTED_VOTE
    with the dominant strategy receiving weight 1.0.
    """
    updated = set_ensemble_config(db, body)
    return updated