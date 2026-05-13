"""
Strategy Orchestrator
Manages multi-strategy signal combination and live trade routing.
"""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud
from ..models import ShadowSignal, Strategy
from ..services.bridge_client import bridge_client
from ..services.news_intelligence import get_news_bias
from ..services.risk_manager import check_and_compute_lot_size
from ..strategy.registry import get_strategy

# ── Ensemble modes ─────────────────────────────────────────────────────────────
ENSEMBLE_MODES = ("DOMINANT", "WEIGHTED_VOTE", "UNANIMOUS")


def get_ensemble_config(db: Session) -> dict:
    raw = crud.get_setting(db, "ensemble_config")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {
        "mode": "DOMINANT",
        "dominant_strategy": "DTC",
        "min_confirmations": 1,
        "weights": {},
    }


def set_ensemble_config(db: Session, config: dict) -> dict:
    validated = {
        "mode": config.get("mode", "DOMINANT") if config.get("mode") in ENSEMBLE_MODES else "DOMINANT",
        "dominant_strategy": config.get("dominant_strategy", "DTC"),
        "min_confirmations": max(1, int(config.get("min_confirmations", 1))),
        "weights": config.get("weights", {}),
    }
    crud.set_setting(db, "ensemble_config", json.dumps(validated))
    return validated


def get_live_strategy(db: Session) -> Strategy | None:
    return db.scalar(select(Strategy).where(Strategy.is_live.is_(True)).limit(1))


def get_active_strategies(db: Session) -> list[Strategy]:
    return list(db.scalars(select(Strategy).where(Strategy.is_active.is_(True))).all())


def set_strategy_live(db: Session, name: str) -> Strategy:
    """Set one strategy as live, all others become shadow."""
    strategies = db.scalars(select(Strategy)).all()
    for s in strategies:
        s.is_live = s.name == name
        s.updated_at = datetime.utcnow()
    db.commit()
    live = db.scalar(select(Strategy).where(Strategy.name == name))
    if not live:
        raise ValueError(f"Strategy {name} not found")
    return live


def _combine_signals(signals: dict[str, str | None], config: dict, live_name: str) -> str | None:
    """Combine signals from multiple strategies based on ensemble config."""
    mode = config.get("mode", "DOMINANT")
    active_signals = {k: v for k, v in signals.items() if v in ("BUY", "SELL")}

    if mode == "UNANIMOUS":
        if not active_signals:
            return None
        values = set(active_signals.values())
        return values.pop() if len(values) == 1 and len(active_signals) == len(signals) else None

    if mode == "DOMINANT":
        dominant = config.get("dominant_strategy", live_name)
        primary = signals.get(dominant)
        if not primary:
            return None
        min_conf = int(config.get("min_confirmations", 1))
        confirmations = sum(1 for k, v in active_signals.items() if k != dominant and v == primary)
        return primary if confirmations >= min_conf else None

    if mode == "WEIGHTED_VOTE":
        weights = config.get("weights", {})
        buy_weight = sum(weights.get(k, 1.0) for k, v in active_signals.items() if v == "BUY")
        sell_weight = sum(weights.get(k, 1.0) for k, v in active_signals.items() if v == "SELL")
        if buy_weight == sell_weight == 0:
            return None
        return "BUY" if buy_weight > sell_weight else "SELL"

    return signals.get(live_name)


def process_signal(
    db: Session,
    market_data: dict,
    symbol: str,
    price: float,
    extra_trade_fields: dict | None = None,
) -> dict:
    """
    Main orchestrator entry point.
    Evaluates all active strategies, applies ensemble logic, news filter, risk checks,
    and places order via bridge if all pass.
    """
    live_strategy = get_live_strategy(db)
    active_strategies = get_active_strategies(db)
    ensemble = get_ensemble_config(db)

    if not live_strategy:
        return {"status": "NO_LIVE_STRATEGY", "reason": "No strategy is set as live"}

    # ── Collect signals from all active strategies ─────────────────────────────
    signals: dict[str, str | None] = {}
    all_levels: dict[str, dict] = {}
    for strategy_row in active_strategies:
        try:
            params = json.loads(strategy_row.params_json or "{}")
            strat = get_strategy(strategy_row.name, params)
            sig = strat.signal({**market_data, "price": price})
            signals[strategy_row.name] = sig
            if sig:
                all_levels[strategy_row.name] = strat.compute_levels(sig, price, params)
        except Exception as e:
            signals[strategy_row.name] = None

    # ── Log shadow signals for non-live strategies ─────────────────────────────
    for strat_name, sig in signals.items():
        if sig and strat_name != live_strategy.name:
            levels = all_levels.get(strat_name, {})
            shadow = ShadowSignal(
                strategy_name=strat_name,
                symbol=symbol,
                direction=sig,
                entry_price=price,
                sl=levels.get("sl"),
                tp1=levels.get("tp1"),
                signal_time=datetime.utcnow(),
            )
            db.add(shadow)
    db.commit()

    # ── Combine signals ────────────────────────────────────────────────────────
    final_signal = _combine_signals(signals, ensemble, live_strategy.name)
    if not final_signal:
        return {"status": "NO_SIGNAL", "signals": signals}

    # ── News filter ────────────────────────────────────────────────────────────
    news_block_threshold = float(crud.get_setting(db, "news_block_threshold") or "0.7")
    news_caution_factor = float(crud.get_setting(db, "news_caution_factor") or "0.5")
    bias_data = get_news_bias(db, symbol)
    news_bias = bias_data["bias"]
    news_conf = bias_data["confidence"]

    if news_conf > news_block_threshold:
        if final_signal == "BUY" and news_bias < -0.3:
            return {"status": "BLOCKED_BY_NEWS", "bias": news_bias, "confidence": news_conf}
        if final_signal == "SELL" and news_bias > 0.3:
            return {"status": "BLOCKED_BY_NEWS", "bias": news_bias, "confidence": news_conf}

    # ── Compute levels from live strategy ─────────────────────────────────────
    live_params = json.loads(live_strategy.params_json or "{}")
    live_strat_obj = get_strategy(live_strategy.name, live_params)
    levels = live_strat_obj.compute_levels(final_signal, price, live_params)
    default_lot = float(live_params.get("lot_size", 0.01))
    if news_conf > 0.4 and abs(news_bias) > 0.2:
        default_lot = round(default_lot * news_caution_factor, 2)

    # ── Risk check ─────────────────────────────────────────────────────────────
    lot_size, block_reason = check_and_compute_lot_size(
        db, symbol=symbol, entry_price=price, stop_loss=levels["sl"], default_lot_size=default_lot
    )
    if block_reason:
        trade = crud.log_trade(
            db,
            {
                "symbol": symbol,
                "direction": final_signal,
                "entry_price": price,
                "stop_loss": levels["sl"],
                "take_profit": levels["tp1"],
                "lot_size": 0,
                "result": "BLOCKED",
                "strategy_name": live_strategy.name,
                "opened_at": datetime.utcnow(),
                **(extra_trade_fields or {}),
            },
        )
        return {"status": "BLOCKED_BY_RISK", "reason": block_reason, "trade_id": trade.id}

    # ── Place order ────────────────────────────────────────────────────────────
    order = bridge_client.place_order(
        {
            "symbol": symbol,
            "direction": final_signal,
            "lot_size": lot_size,
            "stop_loss": levels["sl"],
            "take_profit": levels["tp1"],
            "price": price,
        }
    )

    from sqlalchemy import desc
    from ..models import ParameterVersion

    latest = db.scalar(select(ParameterVersion).order_by(desc(ParameterVersion.version)).limit(1))
    version = latest.version if latest else 1

    trade = crud.log_trade(
        db,
        {
            "symbol": symbol,
            "direction": final_signal,
            "entry_price": price,
            "stop_loss": levels["sl"],
            "take_profit": levels["tp1"],
            "lot_size": lot_size,
            "result": "OPEN",
            "strategy_name": live_strategy.name,
            "params_version": version,
            "opened_at": datetime.utcnow(),
            **(extra_trade_fields or {}),
        },
    )

    return {
        "status": "OK",
        "signal": final_signal,
        "trade_id": trade.id,
        "order": order,
        "levels": levels,
        "lot_size": lot_size,
        "signals_by_strategy": signals,
        "news_bias": news_bias,
    }
