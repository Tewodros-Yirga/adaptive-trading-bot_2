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

from pymongo.database import Database
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import crud
from ..models import PickerWeightHistory, StrategyPickerDecision

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

def _load_factor_weights(db: Database) -> dict[str, float]:
    """Load picker factor weights from AppSettings, falling back to defaults."""
    keys = [f"picker_weight_{f}" for f in FACTOR_NAMES]
    setting_map = crud.get_settings(db, keys)
    weights: dict[str, float] = {}
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
    db: Database,
) -> dict[str, float]:
    """
    Returns raw factor scores (0.0–1.0) for each of the 7 factors.
    Falls back to backtest composite score when live trade history is thin,
    but uses strategy-specific factors (freshness, signal_confidence) even then.
    """
    lookback = int(crud.get_setting(db, "picker_lookback_trades") or 20)
    min_live_trades = int(crud.get_setting(db, "picker_min_trades_for_scoring") or 5)

    recent_trades = crud.get_recent_closed_trades_for_strategy(db, strategy_name, limit=lookback)

    if len(recent_trades) < min_live_trades:
        best_candidate = crud.get_best_backtest_candidate(db, strategy_name)
        # Default to 0.2 (not 0.1) — strategies with no backtest history get a
        # small but non-zero score so they can pass picker_min_score (default 0.2)
        # and be evaluated on live signals rather than being permanently frozen out.
        bt_score = best_candidate.composite_score if best_candidate else 0.2

        # Use strategy-specific factors even without live trades
        latest_param = crud.get_latest_param_version_for_strategy(db, strategy_name)
        if latest_param:
            _now = datetime.datetime.now(datetime.timezone.utc)
            _ca = latest_param.created_at if latest_param.created_at.tzinfo else latest_param.created_at.replace(tzinfo=datetime.timezone.utc)
            hours_since = (_now - _ca).total_seconds() / 3600
            freshness = math.exp(-0.01 * hours_since)
        else:
            freshness = 0.5

        return {
            "recent_win_rate": bt_score,
            "profit_factor": bt_score,
            "backtest_composite_score": bt_score,
            "drawdown": 0.8,            # assume low drawdown when untested
            "signal_confidence": float(signal_confidence),
            "recency_of_last_win": 0.0, # no wins yet
            "parameter_freshness": freshness,
        }

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
        _now = datetime.datetime.now(datetime.timezone.utc)
        _lw = last_win.closed_at if last_win.closed_at.tzinfo else last_win.closed_at.replace(tzinfo=datetime.timezone.utc)
        bars_since = (_now - _lw).total_seconds() / 3600
        recency_score = math.exp(-recency_lambda * bars_since)
    else:
        recency_score = 0.0

    latest_param = crud.get_latest_param_version_for_strategy(db, strategy_name)
    if latest_param:
        _now = datetime.datetime.now(datetime.timezone.utc)
        _ca = latest_param.created_at if latest_param.created_at.tzinfo else latest_param.created_at.replace(tzinfo=datetime.timezone.utc)
        hours_since = (_now - _ca).total_seconds() / 3600
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
    factor_scores: dict[str, dict[str, float]] | None = None,
) -> list[str]:
    """
    Returns up to max_n strategy names ranked by composite score.

    Strategies are selected purely on score — the old signal_confidence binary
    gate has been removed.  If the selected strategy isn't firing this bar the
    caller already handles it via the SELECTED_BUT_NO_SIGNAL path, so the gate
    was just preventing the picker from ever producing decisions.

    factor_scores is kept as a parameter so the news-veto caller can still pass
    it through; it is no longer used for the eligibility check here.
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
    db: Database,
    factor_scores: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, float], dict, bool]:
    """
    Adjust scores based on news bias and potentially veto the whole signal.
    Returns (adjusted_scores, news_influence_json, veto_triggered).

    factor_scores: when provided, the veto check uses the signal_confidence
    hard gate so only actually-firing strategies are considered.  Without it
    (e.g. called from tests) the gate is skipped and composite scores alone
    decide which strategies are "top".
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

    # Veto check — only fires when news confidence is very high (>= veto_threshold)
    # AND at least one currently-signaling strategy is going against the bias.
    # Passing factor_scores enables the signal_confidence hard gate so silent
    # strategies are excluded, preventing spurious vetoes like the 06:05 incident
    # where all non-Alchemist strategies (score 0.16, not signaling) were counted
    # as "going against the bias" and triggered the veto incorrectly.
    veto = False
    if news_confidence > veto_threshold:
        bias_direction = "BUY" if news_bias > 0 else "SELL"
        max_n = int(crud.get_setting(db, "picker_max_simultaneous_strategies") or 1)
        min_score = float(crud.get_setting(db, "picker_min_score") or 0.3)
        sec_threshold = float(crud.get_setting(db, "picker_secondary_threshold") or 0.85)
        top_strategies = select_strategies(
            adjusted, max_n, min_score, sec_threshold, factor_scores
        )
        # Only veto if there are strategies that are ACTUALLY signaling and ALL
        # of them are going the wrong way.  If nothing is signaling there is
        # nothing to veto — let the normal NO_SIGNAL path handle it.
        signaling_top = [s for s in top_strategies if strategy_signals.get(s)]
        if signaling_top and all(
            strategy_signals.get(s) != bias_direction for s in signaling_top
        ):
            veto = True
            news_influence["veto"] = True

    return adjusted, news_influence, veto


# ---------------------------------------------------------------------------
# Groq reasoning (optional — free tier, replaces Claude)
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _call_groq_reasoning(selected, scores, news_influence, api_key: str) -> str:
    import httpx

    payload = {
        "model": "llama-3.1-8b-instant",
        "max_tokens": 1000,
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a trading system assistant. Given strategy scores and news context, "
                    "provide a 1-2 sentence plain-English explanation of why the selected strategy "
                    "was chosen and what the main risk is. Respond ONLY in JSON: "
                    '{"reasoning": "...", "main_risk": "..."}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"selected": selected, "scores": scores, "news": news_influence}
                ),
            },
        ],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        import re
        text = resp.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        return parsed.get("reasoning", "") + " Risk: " + parsed.get("main_risk", "")


async def _generate_reasoning(
    selected: list[str],
    scores: dict[str, float],
    news_influence: dict,
    db: Database,
) -> str:
    api_key = crud.get_setting(db, "groq_api_key")
    if not api_key:
        return ""
    try:
        return await _call_groq_reasoning(selected, scores, news_influence, api_key)
    except Exception as e:
        logger.warning(f"Groq reasoning call failed: {e}")
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
    db: Database,
) -> dict:
    """
    Main entry point called by the orchestrator.

    Scoring uses signal_confidence from all strategies (signal or not), so
    the picker selects the best-scored strategies regardless of whether they
    produced a signal this bar.

    FIX: After selecting strategies, we filter to only those that actually
    produced a direction before calling resolve_direction.  This prevents
    confidence from always being 0.0 (which happened when selected strategies
    had direction=None and their confidence was multiplied into the vote sum).

    Args:
        symbol: e.g. "XAUUSD"
        raw_signals: list of dicts with keys:
            strategy_name, direction, confidence, proposed_entry, proposed_sl,
            proposed_tp1, proposed_tp2, proposed_tp3, proposed_tp4
        db: MongoDB Database

    Returns dict with keys:
        resolved_direction, selected_strategies, ensemble_weights,
        levels, picker_decision_id, picker_confidence, veto, veto_reason
    """
    from ..services.orchestrator import resolve_direction, resolve_ensemble_levels

    max_n = int(crud.get_setting(db, "picker_max_simultaneous_strategies") or 1)
    min_score = float(crud.get_setting(db, "picker_min_score") or 0.15)
    sec_threshold = float(crud.get_setting(db, "picker_secondary_threshold") or 0.85)

    weights = _load_factor_weights(db)
    strategy_signals = {s["strategy_name"]: s.get("direction") for s in raw_signals}

    # ── Compute per-strategy factor + total scores ─────────────────────────
    # IMPORTANT: score using the actual signal confidence (1.0 when direction
    # is present, 0.0 when not) so the picker favours strategies that are
    # currently firing.  This also means a strategy that scores well on
    # historical factors but has no signal right now will rank lower than one
    # that IS firing.
    scores: dict[str, float] = {}
    all_factor_scores: dict[str, dict[str, float]] = {}
    for sig in raw_signals:
        # Use the real confidence from the signal, not a default of 0.
        actual_confidence = float(sig.get("confidence") or 0.0)
        factors = compute_factor_scores(sig["strategy_name"], actual_confidence, db)
        all_factor_scores[sig["strategy_name"]] = factors
        scores[sig["strategy_name"]] = compute_total_score(factors, weights)

    # ── Diagnostic log: show signal directions + composite scores ──────────
    signaling_count = sum(1 for s in raw_signals if s.get("direction"))
    logger.info(
        "[Picker] %s | %d/%d strategies firing | raw_scores: %s",
        symbol,
        signaling_count,
        len(raw_signals),
        {k: round(v, 4) for k, v in scores.items()},
    )
    if signaling_count == 0:
        logger.info(
            "[Picker] No strategy produced a direction this bar. "
            "Directions: %s",
            {s["strategy_name"]: s.get("direction") for s in raw_signals},
        )

    # ── News adjustments (synchronous) ────────────────────────────────────
    adjusted_scores, news_influence, veto = apply_news_adjustments(
        scores, strategy_signals, symbol, db, factor_scores=all_factor_scores
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
        decision = crud.create_picker_decision(db, decision)
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

    # ── Diagnostic: explain eligibility for each strategy ────────────────
    for name, score in adjusted_scores.items():
        is_signaling = (all_factor_scores.get(name, {}).get("signal_confidence", 0.0) or 0.0) > 0.0
        if score < min_score:
            logger.debug(
                "[Picker] %s below min_score: score=%.4f < %.4f (not eligible)",
                name, score, min_score,
            )
        elif not is_signaling:
            logger.debug(
                "[Picker] %s eligible (score=%.4f) but not firing this bar — "
                "will be selected; downstream handles SELECTED_BUT_NO_SIGNAL",
                name, score,
            )

    selected = select_strategies(adjusted_scores, max_n, min_score, sec_threshold, all_factor_scores)
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
        decision = crud.create_picker_decision(db, decision)
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

    # ── FIX: only vote with selected strategies that have an actual direction.
    # Previously ALL signals for selected strategies (including those with
    # direction=None) were passed to resolve_direction.  Multiplying any
    # weight by confidence=0.0 produced buy_weight=sell_weight=0 → no
    # direction resolved and confidence always returned as 0.0.
    filtered_signals = [s for s in raw_signals if s["strategy_name"] in selected]
    signaling_signals = [s for s in filtered_signals if s.get("direction")]

    if not signaling_signals:
        # Selected strategies exist but none produced a signal this bar.
        decision = StrategyPickerDecision(
            symbol=symbol,
            timestamp=datetime.datetime.utcnow(),
            strategy_scores_json=_format_scores(adjusted_scores, all_factor_scores, selected),
            selected_strategies_json=selected,
            ensemble_weights_used_json=ensemble_weights,
            news_influence_json=news_influence,
            picker_confidence=0.0,
            reasoning="SELECTED_BUT_NO_SIGNAL",
        )
        decision = crud.create_picker_decision(db, decision)
        return {
            "resolved_direction": None,
            "selected_strategies": selected,
            "ensemble_weights": ensemble_weights,
            "levels": {},
            "picker_decision_id": decision.id,
            "picker_confidence": 0.0,
            "veto": False,
            "veto_reason": None,
        }

    # ── Resolve direction only from strategies that are actually firing ────
    resolved_direction, confidence = resolve_direction(signaling_signals, ensemble_weights)

    # ── Compute entry/SL/TP levels from agreeing signaling strategies ──────
    levels = (
        resolve_ensemble_levels(resolved_direction, signaling_signals, ensemble_weights)
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
    decision = crud.create_picker_decision(db, decision)

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

def update_picker_weights_from_trade(trade, decision: StrategyPickerDecision, db: Database) -> None:
    """
    Gradient-based update of picker factor weights after a trade closes.

    Args:
        trade: closed Trade ORM object (result must be "WIN" or "LOSS")
        decision: the StrategyPickerDecision that led to the trade
        db: active Database
    """
    if trade.result not in ("WIN", "LOSS"):
        return

    keys = [f"picker_weight_{f}" for f in FACTOR_NAMES]

    try:
        setting_map = crud.get_settings(db, keys)
        weights_before = {
            k.replace("picker_weight_", ""): float(v) for k, v in setting_map.items()
        }
        # Fill in any missing keys with defaults
        for f in FACTOR_NAMES:
            if f not in weights_before:
                weights_before[f] = DEFAULT_WEIGHTS[f]

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

        # Persist updated weights via crud
        for f in FACTOR_NAMES:
            crud.set_setting(db, f"picker_weight_{f}", str(round(weights_after[f], 8)))

        deltas = {f: round(weights_after[f] - weights_before.get(f, 0.0), 8) for f in weights_before}
        crud.create_picker_weight_history(db, PickerWeightHistory(
            trade_id=trade.id,
            weights_before_json=weights_before,
            weights_after_json=weights_after,
            weight_deltas_json=deltas,
            trade_result=trade.result,
            updated_at=datetime.datetime.utcnow(),
        ))
        logger.info(
            f"Picker weights updated from trade #{trade.id} ({trade.result}). "
            f"Δmax={max(abs(v) for v in deltas.values()):.4f}"
        )
    except Exception as e:
        logger.warning(f"update_picker_weights_from_trade failed: {e}")