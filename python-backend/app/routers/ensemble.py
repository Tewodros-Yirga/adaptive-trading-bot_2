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


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_threshold(db: Database) -> float:
    raw = crud.get_setting(db, "ensemble_voting_threshold")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.60


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase 4: New weight / voter endpoints
# ---------------------------------------------------------------------------

@router.get("/weights")
def get_weights(db: Database = Depends(get_db)):
    """
    Return the currently active ensemble weights loaded from MongoDB.
    Includes: weights per strategy, suspended strategies, normalization info,
    and when they were last saved.
    """
    from ..strategy.ensemble.weight_manager import WeightManager
    from ..db import COLL_ENSEMBLE_WEIGHTS

    wm = WeightManager()
    weights = wm.load_weights(db)
    suspended = wm.get_suspended_strategies(db)
    defaults = wm.get_default_weights()

    # Load metadata from ensemble_weights collection
    doc = db[COLL_ENSEMBLE_WEIGHTS].find_one({"is_active": True}, sort=[("saved_at", -1)])
    metadata = doc.get("metadata", {}) if doc else {}
    saved_at = doc.get("saved_at") if doc else None

    return {
        "weights": weights or defaults,
        "using_defaults": weights is None,
        "suspended_strategies": list(suspended),
        "saved_at": saved_at,
        "metadata": metadata,
        "voting_threshold": _get_threshold(db),
    }


@router.get("/voter-snapshot")
def get_voter_snapshot(db: Database = Depends(get_db)):
    """
    Return a live snapshot of what the EnsembleVoter's current state looks like:
    normalized weights after Alchemist floor + suspension, threshold,
    and the ALCHEMIST_MIN_WEIGHT constant.
    Used by the frontend weight matrix display.
    """
    from ..strategy.ensemble.voter import EnsembleVoter, ALCHEMIST_MIN_WEIGHT
    from ..services.orchestrator import _load_ensemble_voter

    voter = _load_ensemble_voter(db)
    return {
        "normalized_weights": voter.normalized_weights,
        "suspended_strategies": list(voter.suspended_strategies),
        "threshold": voter.threshold,
        "alchemist_min_weight": ALCHEMIST_MIN_WEIGHT,
        "alchemist_has_floor": voter.normalized_weights.get("Alchemist", 0) >= ALCHEMIST_MIN_WEIGHT,
    }


@router.post("/weights/reset")
def reset_weights(
    db: Database = Depends(get_db),
    _w=Depends(require_write_access),
):
    """
    Reset ensemble weights to equal defaults and clear the active saved weight.
    Useful for testing or after a bad optimization cycle.
    """
    from ..strategy.ensemble.weight_manager import WeightManager

    wm = WeightManager()
    defaults = wm.get_default_weights()
    wm.save_weights(db, defaults, metadata={"source": "manual_reset"})
    return {"status": "ok", "weights": defaults}


@router.post("/weights/suspend")
def set_suspended(
    body: dict,
    db: Database = Depends(get_db),
    _w=Depends(require_write_access),
):
    """
    Manually override the suspended strategy list.
    Body: { "suspended": ["DTC", "Multi_EMA_Scalper"] }
    """
    import json as _json

    suspended = body.get("suspended", [])
    if not isinstance(suspended, list):
        raise HTTPException(400, "suspended must be a list of strategy names")
    crud.set_setting(db, "ensemble_suspended_strategies", _json.dumps(suspended))
    return {"status": "ok", "suspended": suspended}