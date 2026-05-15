"""
Picker Router — Strategy Picker meta-service endpoints.
"""
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ..auth_deps import get_current_user, require_admin
from ..db import get_db, COLL_STRATEGY_PICKER_DECISIONS, COLL_PICKER_WEIGHT_HISTORY, COLL_TRADES
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


def _load_active_weights(db: Database) -> dict[str, float]:
    raw: dict[str, float] = {}
    for f in _FACTOR_NAMES:
        val = crud.get_setting(db, f"picker_weight_{f}")
        try:
            raw[f] = float(val) if val is not None else _DEFAULT_WEIGHTS[f]
        except (ValueError, TypeError):
            raw[f] = _DEFAULT_WEIGHTS[f]
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 8) for k, v in raw.items()}


def _load_all_settings(db: Database) -> dict[str, Any]:
    stored = crud.get_settings(db, _SETTING_KEYS)
    return {key: stored.get(key, "") for key in _SETTING_KEYS}


@router.get("/status", response_model=PickerStatusOut)
def get_picker_status(db: Database = Depends(get_db), _user=Depends(get_current_user)):
    active_weights = _load_active_weights(db)
    settings = _load_all_settings(db)

    cutoff = datetime.utcnow() - timedelta(days=7)
    recent_docs = list(
        db[COLL_STRATEGY_PICKER_DECISIONS]
        .find({"timestamp": {"$gte": cutoff}})
        .sort("timestamp", -1)
    )
    recent_decisions = [StrategyPickerDecision.from_doc(d) for d in recent_docs]

    total = len(recent_decisions)
    with_trade = sum(1 for d in recent_decisions if d.trade_id is not None)
    veto_count = sum(
        1 for d in recent_decisions
        if d.news_influence_json and d.news_influence_json.get("veto")
    )
    no_eligible = sum(1 for d in recent_decisions if not d.selected_strategies_json)

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

    return PickerStatusOut(active_weights=active_weights, settings=settings, stats_7d=stats_7d)


@router.get("/decisions", response_model=list[StrategyPickerDecisionOut])
def list_picker_decisions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    return crud.get_picker_decisions(db, page=page, limit=limit, symbol=symbol)


@router.get("/decisions/{decision_id}", response_model=StrategyPickerDecisionOut)
def get_picker_decision(decision_id: int, db: Database = Depends(get_db), _user=Depends(get_current_user)):
    row = crud.get_picker_decision(db, decision_id)
    if not row:
        raise HTTPException(status_code=404, detail="Picker decision not found")
    return row


@router.get("/weight-history", response_model=list[PickerWeightHistoryOut])
def list_weight_history(
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    return crud.get_picker_weight_history(db, limit=limit)


@router.get("/weights/current")
def get_current_weights(db: Database = Depends(get_db), _user=Depends(get_current_user)):
    return _load_active_weights(db)


@router.get("/weights/history", response_model=list[PickerWeightHistoryOut])
def list_weights_history_alias(
    limit: int = Query(100, ge=1, le=500),
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    return crud.get_picker_weight_history(db, limit=limit)


@router.get("/performance", response_model=PickerPerformanceOut)
def get_picker_performance(
    days: int = Query(30, ge=1, le=365),
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    cutoff = datetime.utcnow() - timedelta(days=days)

    decision_docs = list(
        db[COLL_STRATEGY_PICKER_DECISIONS].find({
            "timestamp": {"$gte": cutoff},
            "trade_id": {"$ne": None},
        })
    )
    decisions = [StrategyPickerDecision.from_doc(d) for d in decision_docs]

    trade_ids = [d.trade_id for d in decisions if d.trade_id is not None]
    trades: dict[int, Trade] = {}
    if trade_ids:
        trade_docs = db[COLL_TRADES].find({"_id": {"$in": trade_ids}})
        trades = {d["_id"]: Trade.from_doc(d) for d in trade_docs}

    wins = sum(1 for d in decisions if trades.get(d.trade_id) and trades[d.trade_id].result == "WIN")
    losses = sum(1 for d in decisions if trades.get(d.trade_id) and trades[d.trade_id].result == "LOSS")
    resolved = wins + losses
    win_rate = round((wins / resolved) * 100, 2) if resolved > 0 else 0.0

    confidences = [d.picker_confidence for d in decisions if d.picker_confidence is not None]
    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    update_count = db[COLL_PICKER_WEIGHT_HISTORY].count_documents({"updated_at": {"$gte": cutoff}})

    return PickerPerformanceOut(
        total_decisions=len(decisions),
        decisions_with_trades=len(decisions),
        total_wins=wins,
        total_losses=losses,
        win_rate_pct=win_rate,
        avg_picker_confidence=avg_confidence,
        weight_update_count=update_count,
    )


@router.post("/settings")
def update_picker_settings(
    payload: PickerSettingsIn,
    db: Database = Depends(get_db),
    _user=Depends(require_admin),
):
    updated: dict[str, str] = {}

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
        current = _load_active_weights(db)
        merged = {**current, **weight_updates}
        total = sum(merged.values()) or 1.0
        normalised = {k: v / total for k, v in merged.items()}
        for factor, w in normalised.items():
            key = f"picker_weight_{factor}"
            crud.set_setting(db, key, str(round(w, 8)))
            updated[key] = str(round(w, 8))

    return {"status": "ok", "updated": updated}


@router.post("/reset-weights")
def reset_picker_weights(db: Database = Depends(get_db), _user=Depends(require_admin)):
    total = sum(_DEFAULT_WEIGHTS.values())
    normalised = {k: v / total for k, v in _DEFAULT_WEIGHTS.items()}
    for factor, weight in normalised.items():
        crud.set_setting(db, f"picker_weight_{factor}", str(round(weight, 8)))
    return {
        "status": "ok",
        "reset_weights": {f"picker_weight_{k}": round(v, 8) for k, v in normalised.items()},
    }