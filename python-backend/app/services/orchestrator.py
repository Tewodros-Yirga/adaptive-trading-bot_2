"""
Strategy Orchestrator
Manages multi-strategy signal combination and live trade routing.

Direction resolution:
  - The EnsembleVoter (with optimized backtested weights from WeightManager) is
    the sole authority on trade direction/confidence/weights.
  - A news-veto check (news_veto.news_veto_check) runs first and can block the
    trade when high-confidence news opposes every signalling strategy; it also
    writes a NewsVetoDecision audit record whose trade_id is back-filled after
    the trade is created. A voter failure means "no strategy direction this bar".
  - An EnsembleDecision record is created for the voting audit trail.

Multi-timeframe:
  - MTF market_data is built for strategies with `requires_mtf = True`
    (build_mtf_market_data() + fetch_ohlcv_with_fallback() from ohlcv.py), fetched
    once per orchestrator call and reused across active MTF strategies.

News learning:
  - After `crud.log_trade(...)` creates an OPEN trade, `learn_from_trade` flags
    news items published before the trade as ``trade_correlation_pending``; at
    trade close, score_feedback.run_trade_close_hooks updates news learning and
    nudges each voting strategy's composite_score.
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
# Indicator enrichment for non-MTF strategies
# ---------------------------------------------------------------------------

def _enrich_market_data_from_df(df, params: dict) -> dict:
    """
    Compute all technical indicators needed by non-MTF strategies from a
    DataFrame of OHLCV candles (columns: open, high, low, close, volume).

    Covers:
      - DTC / Multi_EMA_Scalper : ema_values, previous_bull, previous_bear
      - RSI_Reversal             : rsi, prev_rsi
      - MACD_Momentum            : macd_line, macd_signal, macd_histogram,
                                   prev_macd_line, prev_macd_signal
      - Bollinger_Breakout       : bb_upper, bb_lower, prev_bb_upper,
                                   prev_bb_lower, prev_price
      - VWAP_Reversion           : vwap

    EMA periods and other hyper-params are read from `params` so each
    strategy gets indicators computed with *its own* parameter set.
    Falls back to industry defaults when a param key is absent.
    Returns {} on any error so callers always get a safe dict.
    """
    try:
        import math
        import pandas as _pd

        if df is None or df.empty or len(df) < 3:
            return {}

        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        volume = (
            df["volume"].astype(float)
            if "volume" in df.columns
            else _pd.Series(1.0, index=df.index)
        )

        def _ema(series, period: int):
            return series.ewm(span=max(period, 1), adjust=False).mean()

        enriched: dict = {}

        # ── EMA cascade (DTC & Multi_EMA_Scalper — param-driven periods) ─────
        ema_periods = {
            "ema_1": int(params.get("ema_1", 30)),
            "ema_2": int(params.get("ema_2", 35)),
            "ema_3": int(params.get("ema_3", 40)),
            "ema_4": int(params.get("ema_4", 45)),
            "ema_5": int(params.get("ema_5", 50)),
            "ema_6": int(params.get("ema_6", 60)),
        }
        ema_series = {k: _ema(close, v) for k, v in ema_periods.items()}
        ema_values = {k: float(s.iloc[-1]) for k, s in ema_series.items()}
        prev_ema_values = {k: float(s.iloc[-2]) for k, s in ema_series.items()}

        enriched["ema_values"] = ema_values
        _keys = ["ema_1", "ema_2", "ema_3", "ema_4", "ema_5", "ema_6"]
        enriched["previous_bull"] = all(
            prev_ema_values[_keys[i]] > prev_ema_values[_keys[i + 1]] for i in range(5)
        )
        enriched["previous_bear"] = all(
            prev_ema_values[_keys[i]] < prev_ema_values[_keys[i + 1]] for i in range(5)
        )

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi_period = int(params.get("rsi_period", 14))
        delta = close.diff()
        avg_gain = delta.clip(lower=0).ewm(com=rsi_period - 1, adjust=False).mean()
        avg_loss = (-delta).clip(lower=0).ewm(com=rsi_period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("inf"))
        rsi_series = 100.0 - 100.0 / (1.0 + rs)
        if len(rsi_series) >= 2:
            enriched["rsi"] = float(rsi_series.iloc[-1])
            enriched["prev_rsi"] = float(rsi_series.iloc[-2])

        # ── MACD ──────────────────────────────────────────────────────────────
        fast_p = int(params.get("fast_period", 12))
        slow_p = int(params.get("slow_period", 26))
        sig_p = int(params.get("signal_period", 9))
        macd_line = _ema(close, fast_p) - _ema(close, slow_p)
        macd_signal_line = _ema(macd_line, sig_p)
        macd_hist = macd_line - macd_signal_line
        if len(macd_line) >= 2:
            enriched["macd_line"] = float(macd_line.iloc[-1])
            enriched["macd_signal"] = float(macd_signal_line.iloc[-1])
            enriched["macd_histogram"] = float(macd_hist.iloc[-1])
            enriched["prev_macd_line"] = float(macd_line.iloc[-2])
            enriched["prev_macd_signal"] = float(macd_signal_line.iloc[-2])

        # ── Bollinger Bands ───────────────────────────────────────────────────
        bb_period = int(params.get("bb_period", 20))
        bb_std = float(params.get("bb_std", 2.0))
        if len(close) >= bb_period:
            bb_mean = close.rolling(bb_period).mean()
            bb_sigma = close.rolling(bb_period).std(ddof=0)
            bb_upper = bb_mean + bb_std * bb_sigma
            bb_lower = bb_mean - bb_std * bb_sigma
            enriched["bb_upper"] = float(bb_upper.iloc[-1])
            enriched["bb_lower"] = float(bb_lower.iloc[-1])
            if len(bb_upper) >= 2:
                enriched["prev_bb_upper"] = float(bb_upper.iloc[-2])
                enriched["prev_bb_lower"] = float(bb_lower.iloc[-2])
                enriched["prev_price"] = float(close.iloc[-2])

        # ── VWAP (rolling approximation over available bars) ──────────────────
        typical = (high + low + close) / 3.0
        cum_vol = volume.cumsum().replace(0, float("nan"))
        vwap_series = (typical * volume).cumsum() / cum_vol
        last_vwap = vwap_series.iloc[-1]
        if not math.isnan(last_vwap):
            enriched["vwap"] = float(last_vwap)

        # ── OBV ───────────────────────────────────────────────────────────────
        # OBV series needed by OBV_Momentum strategy
        if "volume" in df.columns:
            obv = [0.0]
            for k in range(1, len(df)):
                if df["close"].iloc[k] > df["close"].iloc[k - 1]:
                    obv.append(obv[-1] + float(df["volume"].iloc[k]))
                elif df["close"].iloc[k] < df["close"].iloc[k - 1]:
                    obv.append(obv[-1] - float(df["volume"].iloc[k]))
                else:
                    obv.append(obv[-1])
            enriched["obv_series"] = obv  # full series; strategies slice from [-N:]

        # ── OHLCV window for ADX_Regime, OBV_Momentum, StochRSI_Cross, HTF_Structure ──
        # Convert the DataFrame's last min(200, len(df)) rows to a list of dicts.
        enriched["ohlcv_window"] = [
            {
                "date": str(ts),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume", 0) or 0),
            }
            for ts, r in df.tail(200).iterrows()
        ]

        return enriched

    except Exception as exc:
        logger.warning("_enrich_market_data_from_df failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Ensemble configuration helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Core ensemble resolution (exported for pair analysis and tests)
# ---------------------------------------------------------------------------

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
# EnsembleVoter helpers (Phase 4)
# ---------------------------------------------------------------------------

def _load_ensemble_voter(db: Database) -> "EnsembleVoter":  # type: ignore[name-defined]
    """
    Load current ensemble weights from MongoDB and construct an EnsembleVoter.

    Falls back to equal weights (via WeightManager.get_default_weights()) if
    no weights have been saved yet by the EnsembleBacktester.

    Import EnsembleVoter and WeightManager lazily (inside the function body)
    to avoid circular imports and to keep them out of the module-level
    import block, which the backtester service also imports.
    """
    from ..strategy.ensemble.voter import EnsembleVoter
    from ..strategy.ensemble.weight_manager import WeightManager

    wm = WeightManager()
    weights = wm.load_weights(db)
    suspended = wm.get_suspended_strategies(db)

    if weights is None:
        weights = wm.get_default_weights()
        logger.debug("EnsembleVoter: no saved weights found — using defaults")

    # ── Blend in live score feedback ──────────────────────────────────────
    # Each strategy carries a `live_score` (EWMA of realised R-multiples from
    # live trades, centred on 0). We scale its backtest weight by
    # (1 + gain * live_score), floored at a small positive so a recently-bad
    # strategy is dampened (not erased — suspension handles full removal) and a
    # recently-good one is amplified. This keeps the backtest weights as the
    # base and lets live outcomes tilt them, WITHOUT touching composite_score.
    if str(crud.get_setting(db, "score_feedback_enabled") or "true").lower() in ("1", "true", "yes", "on"):
        try:
            gain = float(crud.get_setting(db, "score_feedback_weight_gain") or 0.25)
            floor = float(crud.get_setting(db, "score_feedback_weight_floor") or 0.1)
            live_scores = crud.get_live_scores(db)
            if gain > 0 and live_scores:
                weights = {
                    name: w * max(floor, 1.0 + gain * float(live_scores.get(name, 0.0)))
                    for name, w in weights.items()
                }
        except Exception as exc:
            logger.warning("EnsembleVoter: live-score blend failed, using base weights: %s", exc)

    return EnsembleVoter(
        weights=weights,
        threshold=_read_ensemble_threshold(db),
        suspended_strategies=suspended,
    )


def _read_ensemble_threshold(db: Database) -> float:
    """Read voting threshold from AppSettings; default 0.60."""
    raw = crud.get_setting(db, "ensemble_voting_threshold")
    try:
        val = float(raw)
        return max(0.40, min(0.80, val))  # clamp to valid range
    except (TypeError, ValueError):
        return 0.60


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
    Main orchestrator entry point.

    1. Determine which active strategies require MTF data; fetch once.
    2. Gather signals from all active strategies (injecting MTF market_data
       for strategies with requires_mtf=True).
    3. Run the news-veto check (news_veto_check) — writes a NewsVetoDecision
       audit record and blocks the trade if news opposes every signal.
    4. Resolve direction/confidence/weights via the EnsembleVoter (optimized
       backtested weights from WeightManager). A voter failure means no strategy
       direction this bar (the news-driven fallback may still apply).
    5. Apply risk check.
    6. Place order via bridge.
    7. Log EnsembleDecision (with voter breakdown) and back-fill
       NewsVetoDecision.trade_id.
    8. Call learn_from_trade so news items published before this trade are
       marked as trade_correlation_pending for later learning.

    Confidence: for non-MTF strategies, signal() returns a plain string, so
    confidence is set to 1.0 when a direction is present; MTF strategies return
    (direction, confidence) tuples and their confidence is used as-is.
    """
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
                # Compute technical indicators from the raw candle DataFrame
                # so non-MTF strategies (DTC, RSI_Reversal, MACD_Momentum,
                # Bollinger_Breakout, Multi_EMA_Scalper, VWAP_Reversion) can
                # generate real signals instead of always returning None.
                _df = market_data.get("_df")
                _indicator_data = (
                    _enrich_market_data_from_df(_df, params)
                    if _df is not None
                    else {}
                )
                effective_md = {**market_data, "price": price, **_indicator_data}

            raw_sig = strat.signal(effective_md)

            # MTF strategies (e.g. Alchemist) return (direction, confidence) tuples.
            # Legacy/non-MTF strategies return a plain string or None.
            # Always produce an explicit float confidence so the EnsembleVoter
            # never multiplies a vote by an accidental 0.
            if isinstance(raw_sig, tuple):
                sig, confidence = raw_sig
                # Ensure confidence is a proper float even if the strategy
                # returned a non-numeric value
                confidence = float(confidence) if confidence is not None else 0.0
            else:
                sig = raw_sig
                # For plain-string strategies: 1.0 when firing, 0.0 when not.
                # We store as float (not bool) to avoid implicit int multiplication.
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

            if sig:
                logger.debug(
                    "Strategy %s signal: %s @ %.5f (confidence=%.3f)",
                    strategy_row.name, sig, price, confidence,
                )

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

    # ── News veto check ───────────────────────────────────────────────────
    # The strategy picker has been retired: the EnsembleVoter (below) is the
    # sole authority on direction. All that remains of the picker is the
    # news-driven veto, which can block a trade when high-confidence news points
    # against every signalling strategy. It also persists an audit record.
    from ..services.news_veto import news_veto_check
    veto_result = news_veto_check(symbol, signal_dicts, db)
    news_veto_decision_id: int | None = veto_result.get("decision_id")

    if veto_result.get("veto"):
        return {
            "status": "BLOCKED_BY_NEWS_VETO",
            "veto_reason": veto_result.get("veto_reason"),
            "news_veto_decision_id": news_veto_decision_id,
        }

    # ── EnsembleVoter: weighted consensus decision (Phase 4) ──────────────
    # Load optimized backtested weights from MongoDB and run the voter.
    # The voter is the sole source of direction/confidence/weights — there is no
    # longer a picker direction to fall back to, so a voter failure means
    # "no trade this bar" (the news-driven fallback below may still synthesize
    # a direction from news bias alone).
    _vote_result: dict = {}
    _level_strategy_name: str | None = None

    try:
        _voter = _load_ensemble_voter(db)
        _vote_result = _voter.vote(signal_dicts)
        final_direction: str | None = _vote_result["direction"]
        resolved_confidence: float = float(_vote_result["confidence"])
        ensemble_weights: dict[str, float] = _voter.normalized_weights
        _level_strategy_name = (
            _voter.get_level_strategy(final_direction, signal_dicts)
            if final_direction else None
        )
        logger.info(
            "[EnsembleVoter] %s | direction=%s conf=%.3f buy=%.3f sell=%.3f fired=%s "
            "level_strategy=%s threshold=%.2f",
            symbol, final_direction, resolved_confidence,
            _vote_result["buy_score"], _vote_result["sell_score"],
            _vote_result["fired"], _level_strategy_name,
            _vote_result["threshold_used"],
        )
    except Exception as _voter_exc:
        # Voter failure: there is no picker direction to fall back to. Degrade to
        # "no strategy direction" — the news-driven fallback below still applies.
        logger.error(
            "[EnsembleVoter] Failed — no strategy direction this bar: %s", _voter_exc
        )
        final_direction = None
        resolved_confidence = 0.0
        ensemble_weights = {}
        _level_strategy_name = None

    # ── Compute levels via the designated level strategy ──────────────────
    levels: dict = {}
    if final_direction and _level_strategy_name:
        try:
            _level_strat_row = next(
                (s for s in active_strategies if s.name == _level_strategy_name), None
            )
            if _level_strat_row:
                _level_params = json.loads(_level_strat_row.params_json or "{}")
                _level_strat_obj = get_strategy(_level_strategy_name, _level_params)
                _levels_raw = _level_strat_obj.compute_levels(
                    final_direction, price, _level_params
                )
                if _levels_raw:
                    levels = {"entry": price, **_levels_raw}
                    logger.debug(
                        "[Levels] computed by %s: sl=%.5f tp1=%.5f",
                        _level_strategy_name,
                        levels.get("sl", 0),
                        levels.get("tp1", 0),
                    )
        except Exception as _lvl_exc:
            logger.warning(
                "[Levels] compute_levels failed for %s: %s", _level_strategy_name, _lvl_exc
            )

    selected_strategies: list[str] = [
        s["strategy_name"]
        for s in signal_dicts
        if s.get("direction") == final_direction
        and float(s.get("confidence") or 0.0) > 0.0
    ]

    if not final_direction:
        # ── News-driven fallback ───────────────────────────────────────────
        # When no strategy fires, synthesize a direction from news bias alone.
        # Thresholds are intentionally very relaxed so the system trades often.
        # Override via AppSettings: news_signal_bias_threshold / news_signal_confidence_threshold
        _bias_data     = get_news_bias(db, symbol)
        _nb            = float(_bias_data["bias"])
        _nc            = float(_bias_data["confidence"])
        _bias_min      = float(crud.get_setting(db, "news_signal_bias_threshold")       or 0.3)
        _conf_min      = float(crud.get_setting(db, "news_signal_confidence_threshold") or 0.5)
        _news_enabled  = (crud.get_setting(db, "news_signal_trading_enabled") or "true").lower() != "false"

        if _news_enabled and abs(_nb) >= _bias_min and _nc >= _conf_min:
            # Synthesize direction from news bias
            final_direction     = "BUY" if _nb > 0 else "SELL"
            resolved_confidence = round(_nc * abs(_nb), 4)
            ensemble_weights    = {"NEWS_DRIVEN": 1.0}
            selected_strategies = ["NEWS_DRIVEN"]
            news_bias           = _nb

            # ATR-based SL / TP (strategy didn't provide levels)
            _atr     = market_data.get("atr") or price * 0.005
            _sl_mult = float(crud.get_setting(db, "news_signal_sl_atr_mult")  or 1.0)
            _tp_mult = float(crud.get_setting(db, "news_signal_tp_atr_mult")  or 1.5)
            if final_direction == "BUY":
                _sl  = price - _atr * _sl_mult
                _tp1 = price + _atr * _tp_mult
            else:
                _sl  = price + _atr * _sl_mult
                _tp1 = price - _atr * _tp_mult
            levels = {"entry": price, "sl": _sl, "tp1": _tp1, "tp2": None, "tp3": None, "tp4": None}

            logger.info(
                "[News-Driven] %s %s @ %.5f | bias=%.3f conf=%.3f "
                "SL=%.5f TP1=%.5f (atr=%.5f sl_mult=%.1f tp_mult=%.1f)",
                final_direction, symbol, price, _nb, _nc,
                _sl, _tp1, _atr, _sl_mult, _tp_mult,
            )
        else:
            # Truly no tradeable signal — no strategy fired, news too neutral
            decision = _log_ensemble_decision(
                db, symbol, signal_dicts, ensemble_weights, None, 0.0,
                levels={}, news_bias=_nb,
                voter_breakdown=_vote_result.get("votes_breakdown"),
            )
            signals_summary = {sd["strategy_name"]: sd["direction"] for sd in signal_dicts}
            # ── Broadcast ensemble_vote (no signal) to dashboard clients ──
            try:
                from ..routers.websocket import broadcast_ws_event
                import asyncio as _asyncio
                _asyncio.create_task(broadcast_ws_event({
                    "event": "ensemble_vote",
                    "symbol": symbol,
                    "direction": None,
                    "confidence": 0.0,
                    "fired": False,
                    "buy_score": _vote_result.get("buy_score", 0.0),
                    "sell_score": _vote_result.get("sell_score", 0.0),
                    "threshold": _vote_result.get("threshold_used", 0.60),
                    "votes_breakdown": _vote_result.get("votes_breakdown", []),
                    "level_strategy": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }))
            except Exception:
                pass
            return {
                "status": "NO_SIGNAL",
                "signals": signals_summary,
                "news_bias": _nb,
                "news_confidence": _nc,
                "news_veto_decision_id": news_veto_decision_id,
                "ensemble_decision_id": decision.id,
            }

    # ── News bias ──────────────────────────────────────────────────────────
    bias_data = get_news_bias(db, symbol)
    news_bias: float = locals().get("news_bias", bias_data["bias"])  # preserve news-driven value

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
            voter_breakdown=_vote_result.get("votes_breakdown"),
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
                "strategy_name": selected_strategies[0] if selected_strategies else "ENSEMBLE",
                "opened_at": datetime.utcnow(),
                **(extra_trade_fields or {}),
            },
        )
        crud.update_ensemble_decision_trade_id(db, decision.id, trade.id)
        return {
            "status": "BLOCKED_BY_RISK",
            "reason": block_reason,
            "trade_id": trade.id,
            "news_veto_decision_id": news_veto_decision_id,
            "ensemble_decision_id": decision.id,
        }

    # ── Duplicate position awareness ───────────────────────────────────────
    existing_same_dir: list[dict] = []
    try:
        live_positions = bridge_client.get_positions()
        _sym_variants: set[str] = {symbol.upper()}
        _sym_u = symbol.upper()
        if _sym_u.endswith("M"):
            _sym_variants.add(_sym_u[:-1])
        else:
            _sym_variants.add(_sym_u + "M")
        existing_same_dir = [
            p for p in live_positions
            if p.get("symbol", "").upper() in _sym_variants
            and p.get("type", "").upper() == final_direction.upper()
        ]
    except Exception as _dup_exc:
        logger.warning("Could not fetch live positions for duplicate check: %s", _dup_exc)

    if existing_same_dir:
        dup_min_conf        = float(crud.get_setting(db, "duplicate_min_confidence")         or 0.75)
        dup_min_atr_dist    = float(crud.get_setting(db, "duplicate_min_price_distance_atr") or 1.0)
        dup_min_strats      = int(  crud.get_setting(db, "duplicate_min_strategy_count")     or 2)
        _atr                = float(market_data.get("atr") or price * 0.005)

        # Escalate confidence requirement per additional stacked position
        position_count      = len(existing_same_dir)
        escalation_factor   = float(crud.get_setting(db, "duplicate_confidence_escalation") or 0.10)
        dup_min_conf_escalated = min(
            dup_min_conf + (position_count - 1) * escalation_factor,
            0.95,  # hard cap
        )

        reasons_blocked: list[str] = []

        if resolved_confidence < dup_min_conf_escalated:
            reasons_blocked.append(
                f"confidence {resolved_confidence:.3f} < required {dup_min_conf_escalated:.3f} "
                f"for position #{position_count + 1} (base={dup_min_conf:.3f} + "
                f"{position_count - 1}×{escalation_factor:.2f} escalation)"
            )

        min_price_dist = min(
            abs(price - float(p.get("openPrice", price)))
            for p in existing_same_dir
        )
        required_dist = _atr * dup_min_atr_dist
        if min_price_dist < required_dist:
            reasons_blocked.append(
                f"price distance {min_price_dist:.5f} < {dup_min_atr_dist:.1f}×ATR "
                f"({required_dist:.5f}) — too close to existing entry"
            )

        agreeing_count = sum(
            1 for s in signal_dicts
            if s.get("direction") == final_direction and float(s.get("confidence") or 0) > 0
        )
        if agreeing_count < dup_min_strats:
            reasons_blocked.append(
                f"only {agreeing_count} strategy/ies agree "
                f"(need ≥{dup_min_strats} for a duplicate)"
            )

        existing_summary = [
            {
                "ticket":    p.get("ticket"),
                "entry":     p.get("openPrice"),
                "volume":    p.get("volume"),
                "profit":    p.get("profit"),
            }
            for p in existing_same_dir
        ]

        if reasons_blocked:
            logger.warning(
                "[DuplicateGuard] BLOCKED %s %s — %d existing position(s) %s | %s",
                final_direction, symbol, len(existing_same_dir),
                existing_summary, "; ".join(reasons_blocked),
            )
            _dup_decision = _log_ensemble_decision(
                db, symbol, signal_dicts, ensemble_weights,
                final_direction, resolved_confidence,
                levels=levels, news_bias=news_bias,
                block_reason="duplicate_blocked: " + "; ".join(reasons_blocked),
                voter_breakdown=_vote_result.get("votes_breakdown"),
            )
            return {
                "status":               "BLOCKED_DUPLICATE_POSITION",
                "reason":               "; ".join(reasons_blocked),
                "existing_positions":   existing_summary,
                "resolved_confidence":  resolved_confidence,
                "required_confidence":  dup_min_conf_escalated,
                "position_count":       position_count,
                "price_distance":       round(min_price_dist, 5),
                "required_distance":    round(required_dist, 5),
                "agreeing_strategies":  agreeing_count,
                "required_strategies":  dup_min_strats,
                "news_veto_decision_id":   news_veto_decision_id,
                "ensemble_decision_id": _dup_decision.id,
            }

        logger.info(
            "[DuplicateGuard] INTENTIONAL DUPLICATE approved: %s %s | "
            "confidence=%.3f (≥%.3f), price_dist=%.5f (≥%.5f), "
            "agreeing_strategies=%d (≥%d) | existing: %s",
            final_direction, symbol,
            resolved_confidence, dup_min_conf_escalated,
            min_price_dist, required_dist,
            agreeing_count, dup_min_strats,
            existing_summary,
        )

    # ── Opposite direction handling ────────────────────────────────────────
    # When the new signal is counter to existing open positions, decide whether
    # to fully close, partially close, or just tighten stops — based on
    # resolved_confidence vs configurable thresholds.
    _existing_positions_all: list[dict] = market_data.get("_existing_positions", [])
    if _existing_positions_all:
        opposite_positions = [
            p for p in _existing_positions_all
            if p.get("type", "").upper() != final_direction.upper()
            and p.get("type", "").upper() in ("BUY", "SELL")
        ]

        if opposite_positions:
            close_full_threshold    = float(crud.get_setting(db, "reversal_full_close_confidence")    or 0.80)
            close_partial_threshold = float(crud.get_setting(db, "reversal_partial_close_confidence") or 0.65)
            partial_close_pct       = float(crud.get_setting(db, "reversal_partial_close_pct")        or 0.50)

            if resolved_confidence >= close_full_threshold:
                # FULL REVERSAL — close all opposite positions, then open new
                for p in opposite_positions:
                    ticket = p.get("ticket")
                    volume = float(p.get("volume") or 0)
                    if ticket and volume > 0 and not p.get("_from_db"):
                        try:
                            bridge_client.close_position(int(ticket), volume)
                            logger.info(
                                "[Reversal] Full close opposite %s position ticket=%s vol=%.2f",
                                p.get("type"), ticket, volume,
                            )
                        except Exception as _rev_exc:
                            logger.warning("[Reversal] Failed to close ticket=%s: %s", ticket, _rev_exc)
                # Fall through to place_order below (new direction)

            elif resolved_confidence >= close_partial_threshold:
                # PARTIAL CLOSE — close a portion of opposite positions, open smaller new
                for p in opposite_positions:
                    ticket = p.get("ticket")
                    volume = float(p.get("volume") or 0)
                    close_vol = round(volume * partial_close_pct, 2)
                    if ticket and close_vol > 0 and not p.get("_from_db"):
                        try:
                            bridge_client.close_position(int(ticket), close_vol)
                            logger.info(
                                "[Reversal] Partial close opposite ticket=%s vol=%.2f/%.2f",
                                ticket, close_vol, volume,
                            )
                        except Exception as _rev_exc:
                            logger.warning(
                                "[Reversal] Partial close failed ticket=%s: %s", ticket, _rev_exc
                            )
                lot_size = max(round(lot_size * partial_close_pct, 2), 0.01)
                # Fall through to place_order below with reduced lot

            else:
                # LOW CONFIDENCE REVERSAL — tighten stops on existing, skip new trade
                for p in opposite_positions:
                    ticket = p.get("ticket")
                    if ticket and not p.get("_from_db"):
                        new_sl = levels.get("entry")  # new signal entry as tighter SL
                        if new_sl:
                            try:
                                bridge_client.modify_position(int(ticket), stop_loss=new_sl)
                                logger.info(
                                    "[Reversal] Tightened SL on opposite ticket=%s to %.5f",
                                    ticket, new_sl,
                                )
                            except Exception as _rev_exc:
                                logger.warning(
                                    "[Reversal] Modify SL failed ticket=%s: %s", ticket, _rev_exc
                                )

                _rev_decision = _log_ensemble_decision(
                    db, symbol, signal_dicts, ensemble_weights,
                    final_direction, resolved_confidence,
                    levels=levels, news_bias=news_bias,
                    block_reason=(
                        f"reversal_confidence_too_low: {resolved_confidence:.3f} < "
                        f"{close_partial_threshold:.3f}, tightened existing SL instead"
                    ),
                    voter_breakdown=_vote_result.get("votes_breakdown"),
                )
                return {
                    "status":              "REVERSAL_MODIFIED_ONLY",
                    "reason":              "Low confidence reversal — tightened existing stops",
                    "resolved_confidence": resolved_confidence,
                    "modified_positions":  len(opposite_positions),
                    "news_veto_decision_id":  news_veto_decision_id,
                    "ensemble_decision_id": _rev_decision.id,
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

    # Extract MT5 ticket from the bridge response so position_stream and
    # reconciler can match this DB trade to a live MT5 position by ticket.
    # orderId is always present (real or simulated).
    mt5_ticket: int | None = None
    try:
        raw_ticket = order.get("orderId") or order.get("ticket")
        if raw_ticket is not None:
            mt5_ticket = int(raw_ticket)
    except (ValueError, TypeError):
        pass

    strategy_for_params = selected_strategies[0] if selected_strategies else None
    if strategy_for_params:
        latest_param = crud.get_latest_param_version(db, strategy_for_params)
    else:
        latest_param = None
    version = latest_param.version if latest_param else 1

    trade_fields: dict = {
        "symbol": symbol,
        "direction": final_direction,
        "entry_price": price,
        "stop_loss": sl,
        "take_profit": levels.get("tp1"),
        "lot_size": lot_size,
        "result": "OPEN",
        "strategy_name": selected_strategies[0] if selected_strategies else "ENSEMBLE",
        "params_version": version,
        "opened_at": datetime.utcnow(),
        **(extra_trade_fields or {}),
    }
    if mt5_ticket is not None:
        trade_fields["mt5_ticket"] = mt5_ticket

    trade = crud.log_trade(db, trade_fields)

    # ── Model 2: notify news intelligence of the new open trade ───────────
    try:
        from ..services.news_intelligence import learn_from_trade as _learn_news
        _learn_news(db, {
            "symbol": symbol,
            "direction": final_direction,
            "opened_at": datetime.utcnow(),
            "pnl": None,
            "result": "OPEN",
            "closed_at": None,
        })
    except Exception as exc:
        logger.warning("News learn-from-open failed: %s", exc)

    decision = _log_ensemble_decision(
        db, symbol, signal_dicts, ensemble_weights, final_direction, resolved_confidence,
        levels=levels, news_bias=news_bias, trade_id=trade.id,
        voter_breakdown=_vote_result.get("votes_breakdown"),
    )

    # ── Broadcast ensemble_vote (fired trade) to dashboard clients ────────
    try:
        from ..routers.websocket import broadcast_ws_event
        import asyncio as _asyncio
        _asyncio.create_task(broadcast_ws_event({
            "event": "ensemble_vote",
            "symbol": symbol,
            "direction": final_direction,
            "confidence": resolved_confidence,
            "fired": True,
            "buy_score": _vote_result.get("buy_score", 0.0),
            "sell_score": _vote_result.get("sell_score", 0.0),
            "threshold": _vote_result.get("threshold_used", 0.60),
            "votes_breakdown": _vote_result.get("votes_breakdown", []),
            "level_strategy": _level_strategy_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }))
    except Exception as _ws_exc:
        logger.debug("WebSocket ensemble_vote broadcast failed: %s", _ws_exc)

    if news_veto_decision_id:
        crud.update_news_veto_decision_trade_id(db, news_veto_decision_id, trade.id)

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
        "news_veto_decision_id": news_veto_decision_id,
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
    voter_breakdown: list[dict] | None = None,  # Phase 4: EnsembleVoter breakdown
) -> EnsembleDecision:
    """
    Persist an EnsembleDecision audit record.

    If `voter_breakdown` is provided (from EnsembleVoter.vote()["votes_breakdown"]),
    it is stored directly in strategy_votes_json — it is already in the correct
    per-strategy dict format and includes suspension/contribution flags.
    If not provided (e.g. the voter failed and no breakdown exists), the legacy
    manual construction is used so all existing call sites remain compatible.
    """
    if voter_breakdown is not None:
        strategy_votes = voter_breakdown
    else:
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