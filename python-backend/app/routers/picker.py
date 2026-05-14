"""
Picker Router
Exposes read/write endpoints for the Strategy Picker meta-service.

Endpoints:
    GET  /picker/status          — current config, active weights, 7d stats
    GET  /picker/decisions       — paginated StrategyPickerDecision log
    GET  /picker/decisions/{id}  — single decision detail
    GET  /picker/weight-history  — PickerWeightHistory entries
    GET  /picker/performance     — meta win-rate of picker selections
    POST /picker/settings        — update picker AppSettings (admin only)
    POST /picker/reset-weights   — reset factor weights to defaults (admin only)
"""
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..auth_deps import get_current_user, require_admin
from ..db import get_db
from .. import crud
from ..models import PickerWeightHistory, StrategyPickerDecision, Trade
from ..schemas import (
    PickerPerformanceOut,
    PickerSettingsIn,
    PickerStatusOut,
    PickerWeightHistoryOut,
    StrategyPickerDecisionOut,
)

router = APIRouter(prefix="/picker", tags=["picker"])

# Factor names mirrored from strategy_picker.py to avoid a circular import
_FACTOR_NAMES = [
    "recent_win_rate",
    "profit_factor",
    "backtest_composite_score",
    "drawdown",
    "signal_confidence",
    "recency_of_last_win",
    "parameter_freshness",
]

_DEFAULT_WEIGHTS: dict[str, float] = {
    "recent_win_rate": 0.25,
    "profit_factor": 0.20,
    "backtest_composite_score": 0.20,
    "drawdown": 0.15,
    "signal_confidence": 0.10,
    "recency_of_last_win": 0.05,
    "parameter_freshness": 0.05,
}

_SETTING_KEYS = [
    "picker_max_simultaneous_strategies",
    "picker_min_score",
    "picker_secondary_threshold",
    "picker_lookback_trades",
    "picker_min_trades_for_scoring",
    "picker_learning_rate",
    "picker_recency_lambda",
    "picker_news_bias_threshold",
    "picker_news_bonus",
    "picker_news_penalty",
    "picker_news_veto_threshold",
] + [f"picker_weight_{f}" for f in _FACTOR_NAMES]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_active_weights(db: Session) -> dict[str, float]:
    """Return normalised factor weights from AppSettings."""
    raw: dict[str, float] = {}
    for f in _FACTOR_NAMES:
        val = crud.get_setting(db, f"picker_weight_{f}")
        try:
            raw[f] = float(val) if val is not None else _DEFAULT_WEIGHTS[f]
        except (ValueError, TypeError):
            raw[f] = _DEFAULT_WEIGHTS[f]
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 8) for k, v in raw.items()}


def _load_all_settings(db: Session) -> dict[str, Any]:
    stored = crud.get_settings(db, _SETTING_KEYS)
    result: dict[str, Any] = {}
    for key in _SETTING_KEYS:
        result[key] = stored.get(key, "")
    return result


# ---------------------------------------------------------------------------
# GET /picker/status
# ---------------------------------------------------------------------------

@router.get("/status", response_model=PickerStatusOut)
def get_picker_status(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Return current weights, settings, and rolling 7-day selection stats."""
    active_weights = _load_active_weights(db)
    settings = _load_all_settings(db)

    # Rolling 7-day stats
    cutoff = datetime.utcnow() - timedelta(days=7)
    recent_decisions = list(
        db.scalars(
            select(StrategyPickerDecision)
            .where(StrategyPickerDecision.timestamp >= cutoff)
            .order_by(desc(StrategyPickerDecision.timestamp))
        ).all()
    )

    total = len(recent_decisions)
    with_trade = sum(1 for d in recent_decisions if d.trade_id is not None)
    veto_count = sum(
        1 for d in recent_decisions
        if d.news_influence_json and d.news_influence_json.get("veto")
    )
    no_eligible = sum(
        1 for d in recent_decisions
        if not d.selected_strategies_json
    )

    # Strategy frequency among selections
    freq: dict[str, int] = {}
    for d in recent_decisions:
        for name in (d.selected_strategies_json or []):
            freq[name] = freq.get(name, 0) + 1

    stats_7d = {
        "total_decisions": total,
        "decisions_with_trades": with_trade,
        "veto_count": veto_count,
        "no_eligible_count": no_eligible,
        "strategy_selection_frequency": freq,
    }

    return PickerStatusOut(
        active_weights=active_weights,
        settings=settings,
        stats_7d=stats_7d,
    )


# ---------------------------------------------------------------------------
# GET /picker/decisions
# ---------------------------------------------------------------------------

@router.get("/decisions", response_model=list[StrategyPickerDecisionOut])
def list_picker_decisions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return crud.get_picker_decisions(db, page=page, limit=limit, symbol=symbol)


# ---------------------------------------------------------------------------
# GET /picker/decisions/{decision_id}
# ---------------------------------------------------------------------------

@router.get("/decisions/{decision_id}", response_model=StrategyPickerDecisionOut)
def get_picker_decision(
    decision_id: int,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    row = crud.get_picker_decision(db, decision_id)
    if not row:
        raise HTTPException(status_code=404, detail="Picker decision not found")
    return row


# ---------------------------------------------------------------------------
# GET /picker/weight-history
# ---------------------------------------------------------------------------

@router.get("/weight-history", response_model=list[PickerWeightHistoryOut])
def list_weight_history(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return crud.get_picker_weight_history(db, limit=limit)


# ---------------------------------------------------------------------------
# GET /picker/performance
# ---------------------------------------------------------------------------

@router.get("/performance", response_model=PickerPerformanceOut)
def get_picker_performance(
    days: int = Query(30, ge=1, le=365, description="Rolling window in days"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """
    Meta-performance: what fraction of picker-selected trades resulted in WIN.
    Covers decisions with a linked trade_id within the rolling window.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    decisions = list(
        db.scalars(
            select(StrategyPickerDecision)
            .where(StrategyPickerDecision.timestamp >= cutoff)
            .where(StrategyPickerDecision.trade_id.isnot(None))
        ).all()
    )

    trade_ids = [d.trade_id for d in decisions]
    trades: dict[int, Trade] = {}
    if trade_ids:
        rows = list(
            db.scalars(select(Trade).where(Trade.id.in_(trade_ids))).all()
        )
        trades = {t.id: t for t in rows}

    wins = sum(
        1 for d in decisions
        if trades.get(d.trade_id) and trades[d.trade_id].result == "WIN"
    )
    losses = sum(
        1 for d in decisions
        if trades.get(d.trade_id) and trades[d.trade_id].result == "LOSS"
    )
    total_decided = len(decisions)
    resolved = wins + losses
    win_rate = round((wins / resolved) * 100, 2) if resolved > 0 else 0.0

    confidences = [d.picker_confidence for d in decisions if d.picker_confidence is not None]
    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    weight_updates = db.scalar(
        select(PickerWeightHistory)
        .where(PickerWeightHistory.updated_at >= cutoff)
    )
    update_count = (
        db.query(PickerWeightHistory)
        .filter(PickerWeightHistory.updated_at >= cutoff)
        .count()
    )

    return PickerPerformanceOut(
        total_decisions=total_decided,
        decisions_with_trades=total_decided,
        total_wins=wins,
        total_losses=losses,
        win_rate_pct=win_rate,
        avg_picker_confidence=avg_confidence,
        weight_update_count=update_count,
    )


# ---------------------------------------------------------------------------
# POST /picker/settings  (admin only)
# ---------------------------------------------------------------------------

@router.post("/settings")
def update_picker_settings(
    payload: PickerSettingsIn,
    db: Session = Depends(get_db),
    _user=Depends(require_admin),
):
    """
    Update any subset of picker AppSettings.
    Factor weights are re-normalised before persisting so they always sum to 1.0.
    """
    updated: dict[str, str] = {}

    # Non-weight settings — persist directly
    non_weight_map = {
        "picker_max_simultaneous_strategies": payload.picker_max_simultaneous_strategies,
        "picker_min_score": payload.picker_min_score,
        "picker_secondary_threshold": payload.picker_secondary_threshold,
        "picker_lookback_trades": payload.picker_lookback_trades,
        "picker_min_trades_for_scoring": payload.picker_min_trades_for_scoring,
        "picker_learning_rate": payload.picker_learning_rate,
        "picker_recency_lambda": payload.picker_recency_lambda,
        "picker_news_bias_threshold": payload.picker_news_bias_threshold,
        "picker_news_bonus": payload.picker_news_bonus,
        "picker_news_penalty": payload.picker_news_penalty,
        "picker_news_veto_threshold": payload.picker_news_veto_threshold,
    }
    for key, value in non_weight_map.items():
        if value is not None:
            crud.set_setting(db, key, str(value))
            updated[key] = str(value)

    # Factor weights — collect any supplied values, merge with current, normalise
    weight_updates: dict[str, float] = {}
    weight_payload_map = {
        "recent_win_rate": payload.picker_weight_recent_win_rate,
        "profit_factor": payload.picker_weight_profit_factor,
        "backtest_composite_score": payload.picker_weight_backtest_composite_score,
        "drawdown": payload.picker_weight_drawdown,
        "signal_confidence": payload.picker_weight_signal_confidence,
        "recency_of_last_win": payload.picker_weight_recency_of_last_win,
        "parameter_freshness": payload.picker_weight_parameter_freshness,
    }
    for factor, new_val in weight_payload_map.items():
        if new_val is not None:
            weight_updates[factor] = new_val

    if weight_updates:
        # Load current weights, overlay updates, then normalise
        current = _load_active_weights(db)
        merged = {**current, **weight_updates}
        total = sum(merged.values()) or 1.0
        normalised = {k: v / total for k, v in merged.items()}
        for factor, w in normalised.items():
            key = f"picker_weight_{factor}"
            crud.set_setting(db, key, str(round(w, 8)))
            updated[key] = str(round(w, 8))

    return {"status": "ok", "updated": updated}


# ---------------------------------------------------------------------------
# POST /picker/reset-weights  (admin only)
# ---------------------------------------------------------------------------

@router.post("/reset-weights")
def reset_picker_weights(
    db: Session = Depends(get_db),
    _user=Depends(require_admin),
):
    """Reset all picker_weight_* AppSettings to their hard-coded defaults."""
    total = sum(_DEFAULT_WEIGHTS.values())
    normalised = {k: v / total for k, v in _DEFAULT_WEIGHTS.items()}
    for factor, weight in normalised.items():
        crud.set_setting(db, f"picker_weight_{factor}", str(round(weight, 8)))
    return {
        "status": "ok",
        "reset_weights": {f"picker_weight_{k}": round(v, 8) for k, v in normalised.items()},
    }