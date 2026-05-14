"""
Strategy Picker Service
Scores all active strategies on 7 factors, applies news adjustments,
selects the best N strategies, and returns ensemble weights + resolved direction.
Also handles online weight learning from live trade outcomes.
"""
import datetime
import json
import logging
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import crud
from ..models import AppSetting, PickerWeightHistory, StrategyPickerDecision

logger = logging.getLogger(__name__)

FACTOR_NAMES = [
    "recent_win_rate",
    "profit_factor",
    "backtest_composite_score",
    "drawdown",
    "signal_confidence",
    "recency_of_last_win",
    "parameter_freshness",
]

DEFAULT_WEIGHTS = {
    "recent_win_rate": 0.25,
    "profit_factor": 0.20,
    "backtest_composite_score": 0.20,
    "drawdown": 0.15,
    "signal_confidence": 0.10,
    "recency_of_last_win": 0.05,
    "parameter_freshness": 0.05,
}


# ---------------------------------------------------------------------------
# Weight loading helpers
# ---------------------------------------------------------------------------

def _load_factor_weights(db: Session) -> dict[str, float]:
    """Load picker factor weights from AppSettings, falling back to defaults."""
    keys = [f"picker_weight_{f}" for f in FACTOR_NAMES]
    rows = list(db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))).all())
    weights: dict[str, float] = {}
    setting_map = {r.key: r.value for r in rows}
    for f in FACTOR_NAMES:
        key = f"picker_weight_{f}"
        try:
            weights[key] = float(setting_map.get(key, DEFAULT_WEIGHTS[f]))
        except (ValueError, TypeError):
            weights[key] = DEFAULT_WEIGHTS[f]

    # Normalise so they sum to 1.0
    total = sum(weights.values()) or 1.0
    return {k: v / total for k, v in weights.items()}


# ---------------------------------------------------------------------------
# Factor scoring
# ---------------------------------------------------------------------------

def compute_factor_scores(
    strategy_name: str,
    signal_confidence: float,
    db: Session,
) -> dict[str, float]:
    """
    Returns raw factor scores (0.0–1.0) for each of the 7 factors.
    Falls back to backtest composite score when live trade history is thin.
    """
    lookback = int(crud.get_setting(db, "picker_lookback_trades") or 20)
    min_live_trades = int(crud.get_setting(db, "picker_min_trades_for_scoring") or 5)

    recent_trades = crud.get_recent_closed_trades_for_strategy(db, strategy_name, limit=lookback)

    if len(recent_trades) < min_live_trades:
        best_candidate = crud.get_best_backtest_candidate(db, strategy_name)
        score = best_candidate.composite_score if best_candidate else 0.3
        return {f: score for f in FACTOR_NAMES}

    wins = [t for t in recent_trades if t.result == "WIN"]
    recent_win_rate = len(wins) / len(recent_trades)

    gross_profit = sum(t.pnl for t in recent_trades if t.pnl and t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in recent_trades if t.pnl and t.pnl < 0)) or 0.001
    profit_factor_score = min(gross_profit / gross_loss / 3.0, 1.0)

    best_candidate = crud.get_best_backtest_candidate(db, strategy_name)
    backtest_score = best_candidate.composite_score if best_candidate else 0.3

    max_drawdown_pct = float(crud.get_setting(db, "max_drawdown_pct") or 20.0)
    pnl_series = [t.pnl or 0 for t in recent_trades]
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_series:
        running += p
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    drawdown_score = max(0.0, 1.0 - (max_dd / (max_drawdown_pct * 100 or 1)))

    recency_lambda = float(crud.get_setting(db, "picker_recency_lambda") or 0.1)
    if wins:
        last_win = max(wins, key=lambda t: t.closed_at)
        bars_since = (datetime.datetime.utcnow() - last_win.closed_at).total_seconds() / 3600
        recency_score = math.exp(-recency_lambda * bars_since)
    else:
        recency_score = 0.0

    latest_param = crud.get_latest_param_version_for_strategy(db, strategy_name)
    if latest_param:
        hours_since = (datetime.datetime.utcnow() - latest_param.created_at).total_seconds() / 3600
        freshness_score = math.exp(-0.01 * hours_since)
    else:
        freshness_score = 0.5

    return {
        "recent_win_rate": recent_win_rate,
        "profit_factor": profit_factor_score,
        "backtest_composite_score": backtest_score,
        "drawdown": drawdown_score,
        "signal_confidence": float(signal_confidence),
        "recency_of_last_win": recency_score,
        "parameter_freshness": freshness_score,
    }


def compute_total_score(factor_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Dot-product of factor scores × their weights."""
    return sum(
        factor_scores.get(f, 0.0) * weights.get(f"picker_weight_{f}", 0.0)
        for f in FACTOR_NAMES
    )


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------

def select_strategies(
    scores: dict[str, float],
    max_n: int,
    min_score: float,
    secondary_threshold: float,
) -> list[str]:
    """
    Returns up to max_n strategy names.
    Secondary strategies must score >= top_score * secondary_threshold.
    """
    eligible = {k: v for k, v in scores.items() if v >= min_score}
    if not eligible:
        return []
    ranked = sorted(eligible, key=eligible.__getitem__, reverse=True)
    selected = [ranked[0]]
    top_score = eligible[ranked[0]]
    for name in ranked[1:max_n]:
        if eligible[name] >= top_score * secondary_threshold:
            selected.append(name)
        else:
            break
    return selected


def scores_to_weights(selected: list[str], scores: dict[str, float]) -> dict[str, float]:
    """Convert raw scores to normalised weights (sum to 1.0) for selected strategies."""
    total = sum(scores[s] for s in selected) or 1.0
    return {s: scores[s] / total for s in selected}


# ---------------------------------------------------------------------------
# News-informed score adjustment
# ---------------------------------------------------------------------------

def apply_news_adjustments(
    scores: dict[str, float],
    strategy_signals: dict[str, str | None],
    symbol: str,
    db: Session,
) -> tuple[dict[str, float], dict, bool]:
    """
    Adjust scores based on news bias and potentially veto the whole signal.
    Returns (adjusted_scores, news_influence_json, veto_triggered).
    """
    from ..services.news_intelligence import get_news_bias

    bias_threshold = float(crud.get_setting(db, "picker_news_bias_threshold") or 0.5)
    veto_threshold = float(crud.get_setting(db, "picker_news_veto_threshold") or 0.85)
    bonus = float(crud.get_setting(db, "picker_news_bonus") or 0.15)
    penalty = float(crud.get_setting(db, "picker_news_penalty") or 0.15)

    bias_data = get_news_bias(db, symbol)
    news_bias = bias_data["bias"]
    news_confidence = bias_data["confidence"]

    news_influence: dict[str, Any] = {
        "news_bias": news_bias,
        "news_confidence": news_confidence,
        "adjustments": {},
    }

    adjusted = scores.copy()
    if abs(news_bias) > bias_threshold:
        bias_direction = "BUY" if news_bias > 0 else "SELL"
        for name, score in scores.items():
            signal_dir = strategy_signals.get(name)
            if signal_dir == bias_direction:
                adjusted[name] = score * (1 + bonus)
                news_influence["adjustments"][name] = f"+{bonus * 100:.0f}%"
            elif signal_dir and signal_dir != bias_direction:
                adjusted[name] = score * (1 - penalty)
                news_influence["adjustments"][name] = f"-{penalty * 100:.0f}%"

    # Veto check
    veto = False
    if news_confidence > veto_threshold:
        bias_direction = "BUY" if news_bias > 0 else "SELL"
        max_n = int(crud.get_setting(db, "picker_max_simultaneous_strategies") or 1)
        min_score = float(crud.get_setting(db, "picker_min_score") or 0.3)
        sec_threshold = float(crud.get_setting(db, "picker_secondary_threshold") or 0.85)
        top_strategies = select_strategies(adjusted, max_n, min_score, sec_threshold)
        if top_strategies and all(strategy_signals.get(s) != bias_direction for s in top_strategies):
            veto = True
            news_influence["veto"] = True

    return adjusted, news_influence, veto


# ---------------------------------------------------------------------------
# Claude reasoning (optional)
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _call_claude_reasoning(selected, scores, news_influence, api_key: str) -> str:
    import httpx

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "system": (
            "You are a trading system assistant. Given strategy scores and news context, "
            "provide a 1-2 sentence plain-English explanation of why the selected strategy "
            "was chosen and what the main risk is. Respond ONLY in JSON: "
            '{"reasoning": "...", "main_risk": "..."}'
        ),
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {"selected": selected, "scores": scores, "news": news_influence}
                ),
            }
        ],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        parsed = json.loads(text)
        return parsed.get("reasoning", "") + " Risk: " + parsed.get("main_risk", "")


async def _generate_reasoning(
    selected: list[str],
    scores: dict[str, float],
    news_influence: dict,
    db: Session,
) -> str:
    api_key = crud.get_setting(db, "anthropic_api_key")
    if not api_key:
        return ""
    try:
        return await _call_claude_reasoning(selected, scores, news_influence, api_key)
    except Exception as e:
        logger.warning(f"Claude reasoning call failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Score formatting helper
# ---------------------------------------------------------------------------

def _format_scores(
    total_scores: dict[str, float],
    factor_scores: dict[str, dict[str, float]],
    selected: list[str],
) -> list[dict]:
    out = []
    for name, total in total_scores.items():
        out.append(
            {
                "strategy_name": name,
                "total_score": round(total, 6),
                "score_components": {k: round(v, 6) for k, v in factor_scores.get(name, {}).items()},
                "selected": name in selected,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Main picker entry point
# ---------------------------------------------------------------------------

async def pick_and_route(
    symbol: str,
    raw_signals: list[dict],
    db: Session,
) -> dict:
    """
    Main entry point called by the orchestrator.

    Args:
        symbol: e.g. "XAUUSD"
        raw_signals: list of dicts with keys:
            strategy_name, direction, confidence, proposed_entry, proposed_sl,
            proposed_tp1, proposed_tp2, proposed_tp3, proposed_tp4
        db: SQLAlchemy Session

    Returns dict with keys:
        resolved_direction, selected_strategies, ensemble_weights,
        levels, picker_decision_id, picker_confidence, veto, veto_reason
    """
    from ..services.orchestrator import resolve_direction, resolve_ensemble_levels

    max_n = int(crud.get_setting(db, "picker_max_simultaneous_strategies") or 1)
    min_score = float(crud.get_setting(db, "picker_min_score") or 0.3)
    sec_threshold = float(crud.get_setting(db, "picker_secondary_threshold") or 0.85)

    weights = _load_factor_weights(db)
    strategy_signals = {s["strategy_name"]: s.get("direction") for s in raw_signals}

    # Compute per-strategy factor + total scores
    scores: dict[str, float] = {}
    all_factor_scores: dict[str, dict[str, float]] = {}
    for sig in raw_signals:
        factors = compute_factor_scores(sig["strategy_name"], float(sig.get("confidence") or 0.0), db)
        all_factor_scores[sig["strategy_name"]] = factors
        scores[sig["strategy_name"]] = compute_total_score(factors, weights)

    # News adjustments (synchronous — get_news_bias is sync)
    adjusted_scores, news_influence, veto = apply_news_adjustments(
        scores, strategy_signals, symbol, db
    )

    if veto:
        decision = StrategyPickerDecision(
            symbol=symbol,
            timestamp=datetime.datetime.utcnow(),
            strategy_scores_json=_format_scores(adjusted_scores, all_factor_scores, []),
            selected_strategies_json=[],
            ensemble_weights_used_json={},
            news_influence_json={**news_influence, "veto": True},
            picker_confidence=0.0,
            reasoning="NEWS_VETO",
        )
        db.add(decision)
        db.commit()
        db.refresh(decision)
        return {
            "resolved_direction": None,
            "selected_strategies": [],
            "ensemble_weights": {},
            "levels": {},
            "picker_decision_id": decision.id,
            "picker_confidence": 0.0,
            "veto": True,
            "veto_reason": "NEWS_VETO",
        }

    selected = select_strategies(adjusted_scores, max_n, min_score, sec_threshold)
    if not selected:
        decision = StrategyPickerDecision(
            symbol=symbol,
            timestamp=datetime.datetime.utcnow(),
            strategy_scores_json=_format_scores(adjusted_scores, all_factor_scores, []),
            selected_strategies_json=[],
            ensemble_weights_used_json={},
            news_influence_json=news_influence,
            picker_confidence=0.0,
            reasoning="NO_ELIGIBLE_STRATEGY",
        )
        db.add(decision)
        db.commit()
        db.refresh(decision)
        return {
            "resolved_direction": None,
            "selected_strategies": [],
            "ensemble_weights": {},
            "levels": {},
            "picker_decision_id": decision.id,
            "picker_confidence": 0.0,
            "veto": False,
            "veto_reason": None,
        }

    ensemble_weights = scores_to_weights(selected, adjusted_scores)
    filtered_signals = [s for s in raw_signals if s["strategy_name"] in selected]
    resolved_direction, confidence = resolve_direction(filtered_signals, ensemble_weights)
    levels = (
        resolve_ensemble_levels(resolved_direction, filtered_signals, ensemble_weights)
        if resolved_direction
        else {}
    )

    reasoning = await _generate_reasoning(selected, adjusted_scores, news_influence, db)

    decision = StrategyPickerDecision(
        symbol=symbol,
        timestamp=datetime.datetime.utcnow(),
        strategy_scores_json=_format_scores(adjusted_scores, all_factor_scores, selected),
        selected_strategies_json=selected,
        ensemble_weights_used_json=ensemble_weights,
        news_influence_json=news_influence,
        picker_confidence=confidence,
        reasoning=reasoning,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)

    return {
        "resolved_direction": resolved_direction,
        "selected_strategies": selected,
        "ensemble_weights": ensemble_weights,
        "levels": levels,
        "picker_decision_id": decision.id,
        "picker_confidence": confidence,
        "veto": False,
        "veto_reason": None,
    }


# ---------------------------------------------------------------------------
# Online weight learning
# ---------------------------------------------------------------------------

def update_picker_weights_from_trade(trade, decision: StrategyPickerDecision, db: Session) -> None:
    """
    Gradient-based update of picker factor weights after a trade closes.
    Uses SELECT FOR UPDATE to prevent concurrent write races.

    Args:
        trade: closed Trade ORM object (result must be "WIN" or "LOSS")
        decision: the StrategyPickerDecision that led to the trade
        db: active Session (transaction managed internally)
    """
    if trade.result not in ("WIN", "LOSS"):
        return

    keys = [f"picker_weight_{f}" for f in FACTOR_NAMES]

    try:
        settings_rows = list(
            db.scalars(
                select(AppSetting)
                .where(AppSetting.key.in_(keys))
                .with_for_update()
            ).all()
        )
        weights_before = {
            s.key.replace("picker_weight_", ""): float(s.value) for s in settings_rows
        }

        lr = float(crud.get_setting(db, "picker_learning_rate") or 0.05)
        selected_list: list[str] = decision.selected_strategies_json or []
        if not selected_list:
            return

        selected_name = selected_list[0]
        score_components: dict[str, float] = {}
        for entry in decision.strategy_scores_json or []:
            if entry.get("strategy_name") == selected_name:
                score_components = entry.get("score_components", {})
                break

        is_win = trade.result == "WIN"
        weights_after: dict[str, float] = {}
        for f, w in weights_before.items():
            contribution = score_components.get(f, 0.0) * w
            gradient = contribution if is_win else -contribution
            weights_after[f] = max(0.01, min(0.99, w + lr * gradient))

        # Normalise
        total = sum(weights_after.values()) or 1.0
        weights_after = {k: v / total for k, v in weights_after.items()}

        # Persist updated weights
        setting_map = {s.key: s for s in settings_rows}
        for f in FACTOR_NAMES:
            key = f"picker_weight_{f}"
            if key in setting_map:
                setting_map[key].value = str(round(weights_after[f], 8))

        deltas = {f: round(weights_after[f] - weights_before.get(f, 0.0), 8) for f in weights_before}
        db.add(
            PickerWeightHistory(
                trade_id=trade.id,
                weights_before_json=weights_before,
                weights_after_json=weights_after,
                weight_deltas_json=deltas,
                trade_result=trade.result,
                updated_at=datetime.datetime.utcnow(),
            )
        )
        db.commit()
        logger.info(
            f"Picker weights updated from trade #{trade.id} ({trade.result}). "
            f"Δmax={max(abs(v) for v in deltas.values()):.4f}"
        )
    except Exception as e:
        logger.warning(f"update_picker_weights_from_trade failed: {e}")
        db.rollback()