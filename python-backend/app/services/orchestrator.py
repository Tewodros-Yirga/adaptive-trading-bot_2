"""
Strategy Orchestrator
Manages multi-strategy signal combination and live trade routing.

Phase 3 changes:
  - `process_signal()` now delegates strategy selection and ensemble resolution
    to `strategy_picker.pick_and_route()` instead of using static `is_live`.
  - `StrategyPickerDecision.trade_id` is back-filled after the trade is created.
  - `EnsembleDecision` record is still created for voting audit trail.
  - DOMINANT mode migrated to WEIGHTED_VOTE on read (unchanged from Phase 2).

Phase 4 additions:
  - MTF (multi-timeframe) market_data is built for strategies with `requires_mtf = True`.
    Uses `build_mtf_market_data()` + `fetch_ohlcv_with_fallback()` from ohlcv.py.
  - Per-strategy MTF data is fetched once per orchestrator call and reused across
    all active MTF strategies (keyed by symbol).
"""
import json
import logging
from datetime import datetime, timedelta

from pymongo.database import Database

from .. import crud
from ..models import EnsembleDecision, Strategy
from ..services.bridge_client import bridge_client
from ..services.news_intelligence import get_news_bias
from ..services.risk_manager import check_and_compute_lot_size
from ..strategy.registry import get_strategy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ensemble configuration helpers
# ---------------------------------------------------------------------------

def get_ensemble_config(db: Database) -> dict:
    raw = crud.get_setting(db, "ensemble_config")
    if raw:
        try:
            config = json.loads(raw)
            return _migrate_dominant_to_weighted_vote(config)
        except Exception:
            pass
    return {
        "mode": "WEIGHTED_VOTE",
        "weights": {},
        "min_vote_threshold": 0.0,
    }


def set_ensemble_config(db: Database, config: dict) -> dict:
    migrated = _migrate_dominant_to_weighted_vote(config)
    crud.set_setting(db, "ensemble_config", json.dumps(migrated))
    return migrated


def _migrate_dominant_to_weighted_vote(config: dict) -> dict:
    if config.get("mode") != "DOMINANT":
        return config
    dominant = config.get("dominant_strategy", "")
    existing_weights: dict[str, float] = config.get("weights", {})
    new_weights = dict(existing_weights)
    if dominant:
        new_weights[dominant] = 1.0
    return {
        "mode": "WEIGHTED_VOTE",
        "weights": new_weights,
        "min_vote_threshold": 0.0,
        "_migrated_from_dominant": dominant,
    }


# ---------------------------------------------------------------------------
# Core ensemble resolution (exported for pair analysis and tests)
# ---------------------------------------------------------------------------

def resolve_direction(
    signals: list[dict],
    weights: dict[str, float],
) -> tuple[str | None, float]:
    """
    Unified weighted-vote direction resolver.
    Returns (resolved_direction, confidence_score).
    """
    total_weight = sum(weights.values()) or 1.0
    norm_weights = {k: v / total_weight for k, v in weights.items()}

    buy_weight = sum(
        norm_weights.get(s["strategy_name"], 0.0) * float(s.get("confidence") or 0.0)
        for s in signals
        if s.get("direction") == "BUY"
    )
    sell_weight = sum(
        norm_weights.get(s["strategy_name"], 0.0) * float(s.get("confidence") or 0.0)
        for s in signals
        if s.get("direction") == "SELL"
    )

    if buy_weight == 0.0 and sell_weight == 0.0:
        return None, 0.0

    total = buy_weight + sell_weight
    if buy_weight > sell_weight:
        return "BUY", buy_weight / total
    if sell_weight > buy_weight:
        return "SELL", sell_weight / total
    return None, 0.0


def resolve_ensemble_levels(
    resolved_direction: str,
    signals: list[dict],
    weights: dict[str, float],
) -> dict:
    """
    Compute entry/SL/TP from agreeing strategies only, using normalised weights.
    Returns dict with keys: entry, sl, tp1, tp2, tp3, tp4.
    Returns {} if no agreeing strategies.
    """
    agreeing = [s for s in signals if s.get("direction") == resolved_direction]
    if not agreeing:
        return {}

    total_w = sum(weights.get(s["strategy_name"], 0.0) for s in agreeing)
    if total_w == 0.0:
        total_w = float(len(agreeing))
        norm: dict[str, float] = {s["strategy_name"]: 1.0 / total_w for s in agreeing}
    else:
        norm = {s["strategy_name"]: weights.get(s["strategy_name"], 0.0) / total_w for s in agreeing}

    if resolved_direction == "BUY":
        entries = [s["proposed_entry"] for s in agreeing if s.get("proposed_entry") is not None]
        sls = [s["proposed_sl"] for s in agreeing if s.get("proposed_sl") is not None]
        entry = min(entries) if entries else None
        sl = min(sls) if sls else None
    else:  # SELL
        entries = [s["proposed_entry"] for s in agreeing if s.get("proposed_entry") is not None]
        sls = [s["proposed_sl"] for s in agreeing if s.get("proposed_sl") is not None]
        entry = max(entries) if entries else None
        sl = max(sls) if sls else None

    def weighted_tp(key: str) -> float | None:
        vals = [
            (norm[s["strategy_name"]], s[key])
            for s in agreeing
            if s.get(key) is not None
        ]
        if not vals:
            return None
        return sum(w * v for w, v in vals)

    return {
        "entry": entry,
        "sl": sl,
        "tp1": weighted_tp("proposed_tp1"),
        "tp2": weighted_tp("proposed_tp2"),
        "tp3": weighted_tp("proposed_tp3"),
        "tp4": weighted_tp("proposed_tp4"),
    }


# ---------------------------------------------------------------------------
# Strategy DB helpers
# ---------------------------------------------------------------------------

def get_live_strategy(db: Database) -> Strategy | None:
    strategies = crud.get_all_strategies(db)
    for s in strategies:
        if getattr(s, "is_live", False):
            return s
    return None


def get_active_strategies(db: Database) -> list[Strategy]:
    return crud.get_active_strategies(db)


def set_strategy_live(db: Database, name: str) -> Strategy:
    from ..db import COLL_STRATEGIES
    _db = db
    _db[COLL_STRATEGIES].update_many({}, {"$set": {"is_live": False, "updated_at": datetime.utcnow()}})
    doc = _db[COLL_STRATEGIES].find_one_and_update(
        {"name": name},
        {"$set": {"is_live": True, "updated_at": datetime.utcnow()}},
        return_document=True,
    )
    if not doc:
        raise ValueError(f"Strategy {name} not found")
    return Strategy.from_doc(doc)


# ---------------------------------------------------------------------------
# MTF data fetching helpers
# ---------------------------------------------------------------------------

async def _fetch_mtf_bars(symbol: str, db: Database) -> dict:
    """
    Fetch the last ~100 bars for each required timeframe for MTF strategies.
    Returns dict: {"1d": DataFrame, "4h": DataFrame, "1h": DataFrame, "15m": DataFrame}.
    Missing or failed fetches produce empty DataFrames (strategy guards handle gracefully).
    """
    from .ohlcv import fetch_ohlcv_with_fallback

    # We fetch a generous window — strategies slice internally
    to_date = datetime.utcnow().strftime("%Y-%m-%d")
    timeframes_and_days = {
        "1d": 200,   # ~200 daily bars
        "4h": 60,    # ~100 4h bars (60 calendar days ≈ ~360 4h bars, enough)
        "1h": 14,    # ~336 1h bars
        "15m": 5,    # ~480 15m bars
    }

    bars_by_tf: dict = {}
    for tf, lookback_days in timeframes_and_days.items():
        from_date = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        try:
            df, _source = await fetch_ohlcv_with_fallback(symbol, from_date, to_date, tf, db)
            bars_by_tf[tf] = df
        except Exception as exc:
            logger.warning("MTF fetch failed for %s %s: %s", symbol, tf, exc)
            bars_by_tf[tf] = __import__("pandas").DataFrame()

    return bars_by_tf


# ---------------------------------------------------------------------------
# Main orchestrator entry point
# ---------------------------------------------------------------------------

async def process_signal(
    db: Database,
    market_data: dict,
    symbol: str,
    price: float,
    extra_trade_fields: dict | None = None,
) -> dict:
    """
    Main orchestrator entry point (Phase 3 + Phase 4 MTF).

    1. Determine which active strategies require MTF data; fetch once.
    2. Gather signals from all active strategies (injecting MTF market_data
       for strategies with requires_mtf=True).
    3. Delegate selection + direction resolution to strategy_picker.pick_and_route().
    4. Apply risk check.
    5. Place order via bridge.
    6. Log EnsembleDecision and back-fill StrategyPickerDecision.trade_id.
    """
    from ..services.strategy_picker import pick_and_route, update_picker_weights_from_trade  # noqa

    active_strategies = get_active_strategies(db)

    if not active_strategies:
        return {"status": "NO_ACTIVE_STRATEGIES", "reason": "No active strategies found"}

    # ── Check which strategies need MTF data ──────────────────────────────
    needs_mtf = any(
        getattr(get_strategy(s.name), "requires_mtf", False)
        for s in active_strategies
    )

    mtf_bars: dict = {}
    if needs_mtf:
        try:
            mtf_bars = await _fetch_mtf_bars(symbol, db)
        except Exception as exc:
            logger.warning("MTF batch fetch failed for %s: %s", symbol, exc)

    # ── Collect raw signals from all active strategies ────────────────────
    signal_dicts: list[dict] = []
    for strategy_row in active_strategies:
        try:
            params = json.loads(strategy_row.params_json or "{}")
            strat = get_strategy(strategy_row.name, params)

            # Build market_data for this strategy
            if getattr(strat, "requires_mtf", False) and mtf_bars:
                from .ohlcv import build_mtf_market_data, _compute_atr_simple
                import pandas as pd

                atr_1h = _compute_atr_simple(mtf_bars.get("1h", pd.DataFrame()))
                effective_md = build_mtf_market_data(
                    symbol=symbol,
                    current_idx=-1,
                    bars_by_tf=mtf_bars,
                    atr=atr_1h or market_data.get("atr"),
                )
                # Merge caller-supplied extras (e.g. correlated_bars)
                effective_md.update({k: v for k, v in market_data.items() if k not in effective_md})
                effective_md["current_price"] = price
            else:
                effective_md = {**market_data, "price": price}

            raw_sig = strat.signal(effective_md)

            # signal() may return (direction, confidence) tuple for MTF strategies
            # or plain string for legacy strategies
            if isinstance(raw_sig, tuple):
                sig, confidence = raw_sig
            else:
                sig = raw_sig
                confidence = 1.0 if sig else 0.0

            levels = strat.compute_levels(sig, price, params) if sig else {}

            signal_dicts.append({
                "strategy_name": strategy_row.name,
                "direction": sig,
                "confidence": confidence,
                "proposed_entry": price if sig else None,
                "proposed_sl": levels.get("sl") if sig else None,
                "proposed_tp1": levels.get("tp1") if sig else None,
                "proposed_tp2": levels.get("tp2") if sig else None,
                "proposed_tp3": levels.get("tp3") if sig else None,
                "proposed_tp4": levels.get("tp4") if sig else None,
            })
        except Exception as exc:
            logger.warning(f"Strategy {strategy_row.name} signal error: {exc}")
            signal_dicts.append({
                "strategy_name": strategy_row.name,
                "direction": None,
                "confidence": 0.0,
                "proposed_entry": None,
                "proposed_sl": None,
                "proposed_tp1": None,
                "proposed_tp2": None,
                "proposed_tp3": None,
                "proposed_tp4": None,
            })

    # ── Log shadow signals ─────────────────────────────────────────────────
    try:
        from ..db import next_id
        coll_name = "shadow_signals"
        try:
            from ..db import COLL_SHADOW_SIGNALS
            coll_name = COLL_SHADOW_SIGNALS
        except ImportError:
            pass
        for sd in signal_dicts:
            if sd["direction"]:
                db[coll_name].insert_one({
                    "_id": next_id(db, coll_name),
                    "strategy_name": sd["strategy_name"],
                    "symbol": symbol,
                    "direction": sd["direction"],
                    "entry_price": price,
                    "sl": sd.get("proposed_sl"),
                    "tp1": sd.get("proposed_tp1"),
                    "signal_time": datetime.utcnow(),
                })
    except Exception as exc:
        logger.warning("Shadow signal logging failed: %s", exc)

    # ── Strategy Picker: select + resolve ──────────────────────────────────
    picker_result = await pick_and_route(symbol, signal_dicts, db)
    picker_decision_id: int | None = picker_result.get("picker_decision_id")

    if picker_result.get("veto"):
        return {
            "status": "BLOCKED_BY_PICKER_VETO",
            "veto_reason": picker_result.get("veto_reason"),
            "picker_decision_id": picker_decision_id,
        }

    final_direction: str | None = picker_result.get("resolved_direction")
    resolved_confidence: float = float(picker_result.get("picker_confidence") or 0.0)
    ensemble_weights: dict[str, float] = picker_result.get("ensemble_weights") or {}
    levels: dict = picker_result.get("levels") or {}
    selected_strategies: list[str] = picker_result.get("selected_strategies") or []

    if not final_direction:
        decision = _log_ensemble_decision(
            db, symbol, signal_dicts, ensemble_weights, None, 0.0,
            levels={}, news_bias=None,
        )
        signals_summary = {sd["strategy_name"]: sd["direction"] for sd in signal_dicts}
        return {
            "status": "NO_SIGNAL",
            "signals": signals_summary,
            "picker_decision_id": picker_decision_id,
            "ensemble_decision_id": decision.id,
        }

    # ── News bias ──────────────────────────────────────────────────────────
    bias_data = get_news_bias(db, symbol)
    news_bias: float = bias_data["bias"]

    # ── Fallback levels ────────────────────────────────────────────────────
    if not levels:
        filtered = [s for s in signal_dicts if s["strategy_name"] in selected_strategies]
        levels = resolve_ensemble_levels(final_direction, filtered, ensemble_weights)

    # ── Risk check ─────────────────────────────────────────────────────────
    default_lot = 0.01
    news_caution_factor = float(crud.get_setting(db, "news_caution_factor") or 0.5)
    news_conf = bias_data["confidence"]
    for strategy_row in active_strategies:
        if strategy_row.name in selected_strategies:
            try:
                p = json.loads(strategy_row.params_json or "{}")
                default_lot = float(p.get("lot_size", 0.01))
            except Exception:
                pass
            break

    if news_conf > 0.4 and abs(news_bias) > 0.2:
        default_lot = round(default_lot * news_caution_factor, 2)

    sl = levels.get("sl") or levels.get("stop_loss")
    lot_size, block_reason = check_and_compute_lot_size(
        db, symbol=symbol, entry_price=price, stop_loss=sl, default_lot_size=default_lot
    )

    if block_reason:
        decision = _log_ensemble_decision(
            db, symbol, signal_dicts, ensemble_weights, final_direction, resolved_confidence,
            levels=levels, news_bias=news_bias, risk_blocked=True, block_reason=block_reason,
        )
        trade = crud.log_trade(
            db,
            {
                "symbol": symbol,
                "direction": final_direction,
                "entry_price": price,
                "stop_loss": sl,
                "take_profit": levels.get("tp1"),
                "lot_size": 0,
                "result": "BLOCKED",
                "strategy_name": selected_strategies[0] if selected_strategies else "PICKER",
                "opened_at": datetime.utcnow(),
                **(extra_trade_fields or {}),
            },
        )
        crud.update_ensemble_decision_trade_id(db, decision.id, trade.id)
        return {
            "status": "BLOCKED_BY_RISK",
            "reason": block_reason,
            "trade_id": trade.id,
            "picker_decision_id": picker_decision_id,
            "ensemble_decision_id": decision.id,
        }

    # ── Place order ────────────────────────────────────────────────────────
    order = bridge_client.place_order(
        {
            "symbol": symbol,
            "direction": final_direction,
            "lot_size": lot_size,
            "stop_loss": sl,
            "take_profit": levels.get("tp1"),
            "price": price,
        }
    )

    strategy_for_params = selected_strategies[0] if selected_strategies else None
    if strategy_for_params:
        latest_param = crud.get_latest_param_version(db, strategy_for_params)
    else:
        latest_param = crud.get_current_params(db)  # returns dict, not ParameterVersion
        latest_param = None  # version will default to 1
    version = latest_param.version if latest_param else 1

    trade = crud.log_trade(
        db,
        {
            "symbol": symbol,
            "direction": final_direction,
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": levels.get("tp1"),
            "lot_size": lot_size,
            "result": "OPEN",
            "strategy_name": selected_strategies[0] if selected_strategies else "PICKER",
            "params_version": version,
            "opened_at": datetime.utcnow(),
            **(extra_trade_fields or {}),
        },
    )

    decision = _log_ensemble_decision(
        db, symbol, signal_dicts, ensemble_weights, final_direction, resolved_confidence,
        levels=levels, news_bias=news_bias, trade_id=trade.id,
    )

    if picker_decision_id:
        crud.update_picker_decision_trade_id(db, picker_decision_id, trade.id)

    signals_summary = {sd["strategy_name"]: sd["direction"] for sd in signal_dicts}

    return {
        "status": "OK",
        "signal": final_direction,
        "trade_id": trade.id,
        "order": order,
        "levels": levels,
        "lot_size": lot_size,
        "signals_by_strategy": signals_summary,
        "selected_strategies": selected_strategies,
        "ensemble_weights": ensemble_weights,
        "news_bias": news_bias,
        "resolved_confidence": resolved_confidence,
        "picker_decision_id": picker_decision_id,
        "ensemble_decision_id": decision.id,
    }


# ---------------------------------------------------------------------------
# EnsembleDecision logging helper
# ---------------------------------------------------------------------------

def _log_ensemble_decision(
    db: Database,
    symbol: str,
    signal_dicts: list[dict],
    weights: dict[str, float],
    resolved_direction: str | None,
    resolved_confidence: float,
    levels: dict,
    news_bias: float | None = None,
    news_blocked: bool = False,
    risk_blocked: bool = False,
    block_reason: str | None = None,
    trade_id: int | None = None,
) -> EnsembleDecision:
    total_w = sum(weights.values()) or 1.0
    norm_weights = {k: v / total_w for k, v in weights.items()}

    strategy_votes = []
    for sd in signal_dicts:
        name = sd["strategy_name"]
        w = norm_weights.get(name, 0.0)
        conf = float(sd.get("confidence") or 0.0)
        direction = sd.get("direction")
        was_agreeing = direction == resolved_direction if resolved_direction else False
        strategy_votes.append(
            {
                "strategy_name": name,
                "direction": direction,
                "confidence": conf,
                "weight": round(w, 6),
                "weighted_vote": round(w * conf, 6),
                "proposed_entry": sd.get("proposed_entry"),
                "proposed_sl": sd.get("proposed_sl"),
                "proposed_tp1": sd.get("proposed_tp1"),
                "was_agreeing": was_agreeing,
                "contributed_to_levels": was_agreeing and w > 0,
            }
        )

    fields = {
        "symbol": symbol,
        "timestamp": datetime.utcnow(),
        "resolved_direction": resolved_direction,
        "resolved_confidence": resolved_confidence,
        "trade_id": trade_id,
        "strategy_votes_json": strategy_votes,
        "final_entry": levels.get("entry"),
        "final_sl": levels.get("sl"),
        "final_tp1": levels.get("tp1"),
        "final_tp2": levels.get("tp2"),
        "final_tp3": levels.get("tp3"),
        "final_tp4": levels.get("tp4"),
        "news_bias": news_bias,
        "news_blocked": news_blocked,
        "risk_blocked": risk_blocked,
        "block_reason": block_reason,
    }
    return crud.create_ensemble_decision(db, fields)