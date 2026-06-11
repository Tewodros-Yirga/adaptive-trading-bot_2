"""
News-veto router — all that remains of the retired Strategy Picker.

The picker no longer scores or selects strategies (the EnsembleVoter is the sole
direction authority). These endpoints expose:
  • the news-veto audit log (NewsVetoDecision records), and
  • the live score-feedback view: each strategy's current best composite_score
    (the value nudged at trade close and read by the EnsembleVoter weighting).
"""
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ..auth_deps import get_current_user, require_admin
from ..db import get_db, COLL_NEWS_VETO_DECISIONS
from .. import crud
from ..models import NewsVetoDecision
from ..schemas import NewsVetoSettingsIn, NewsVetoDecisionOut

router = APIRouter(prefix="/news-veto", tags=["news-veto"])

# News-veto thresholds + live score-feedback knobs.
_SETTING_KEYS = [
    "news_veto_bias_threshold",
    "news_veto_threshold",
    "score_feedback_enabled",
    "score_feedback_alpha",
    "score_feedback_score_bound",
    "score_feedback_weight_gain",
    "score_feedback_weight_floor",
]


def _load_settings(db: Database) -> dict[str, Any]:
    stored = crud.get_settings(db, _SETTING_KEYS)
    return {key: stored.get(key, "") for key in _SETTING_KEYS}


def _strategy_scores(db: Database) -> dict[str, dict]:
    """Per active strategy: backtest composite_score + live_score (R-multiple EWMA)."""
    scores: dict[str, dict] = {}
    for strat in crud.get_active_strategies(db):
        best = crud.get_best_backtest_candidate(db, strat.name)
        scores[strat.name] = {
            "composite_score": (
                round(float(best.composite_score), 6)
                if best and best.composite_score is not None else 0.0
            ),
            "live_score": round(float(getattr(strat, "live_score", 0.0) or 0.0), 4),
        }
    return scores


@router.get("/status")
def get_news_veto_status(db: Database = Depends(get_db), _user=Depends(get_current_user)):
    """News-veto stats over the last 7 days + current per-strategy scores."""
    cutoff = datetime.utcnow() - timedelta(days=7)
    recent_docs = list(
        db[COLL_NEWS_VETO_DECISIONS]
        .find({"timestamp": {"$gte": cutoff}})
        .sort("timestamp", -1)
    )
    recent = [NewsVetoDecision.from_doc(d) for d in recent_docs]
    veto_count = sum(1 for d in recent if d.veto)

    return {
        "settings": _load_settings(db),
        "strategy_scores": _strategy_scores(db),
        "stats_7d": {
            "total_news_checks": len(recent),
            "veto_count": veto_count,
        },
    }


@router.get("/decisions", response_model=list[NewsVetoDecisionOut])
def list_news_veto_decisions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    """News-veto audit log."""
    return crud.get_news_veto_decisions(db, page=page, limit=limit, symbol=symbol)


@router.get("/decisions/{decision_id}", response_model=NewsVetoDecisionOut)
def get_news_veto_decision(decision_id: int, db: Database = Depends(get_db), _user=Depends(get_current_user)):
    row = crud.get_news_veto_decision(db, decision_id)
    if not row:
        raise HTTPException(status_code=404, detail="News-veto decision not found")
    return row


@router.post("/settings")
def update_news_veto_settings(
    payload: NewsVetoSettingsIn,
    db: Database = Depends(get_db),
    _user=Depends(require_admin),
):
    """Update news-veto thresholds and score-feedback knobs."""
    updated: dict[str, str] = {}
    field_map = {
        "news_veto_bias_threshold": payload.news_veto_bias_threshold,
        "news_veto_threshold": payload.news_veto_threshold,
        "score_feedback_enabled": payload.score_feedback_enabled,
        "score_feedback_alpha": payload.score_feedback_alpha,
        "score_feedback_score_bound": payload.score_feedback_score_bound,
        "score_feedback_weight_gain": payload.score_feedback_weight_gain,
        "score_feedback_weight_floor": payload.score_feedback_weight_floor,
    }
    for key, value in field_map.items():
        if value is not None:
            sval = str(value).lower() if isinstance(value, bool) else str(value)
            crud.set_setting(db, key, sval)
            updated[key] = sval

    return {"status": "ok", "updated": updated}
