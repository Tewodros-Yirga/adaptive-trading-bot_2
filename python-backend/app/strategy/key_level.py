"""
app/strategy/key_level.py — XAUUSD Key-Level Strategy (dynamic breakout/reversion)

XAUUSD reacts to round psychological price levels (multiples of $100 —
4000, 4100, 4200 ...). As price approaches one of these levels it either
breaks THROUGH it (continuation) or REJECTS off it (bounce back).

There is deliberately NO hardcoded / user-set "breakout" vs "reversion" flag.
Every distance, threshold and lookback is a continuous numeric parameter in
PARAM_BOUNDS so the existing coordinate-ascent optimizer can tune it. The mode
that applies to a given signal is decided PER SIGNAL from measurable market
conditions via a continuous "breakout propensity score", and the decision
boundary (breakout_score_threshold) is itself an optimizer-tunable number:

    breakout_score = w_momentum   * momentum_component
                   + w_volatility * volatility_component
                   + w_history    * history_component
                     (weights normalized to sum to 1 at eval time)

    resolved_mode  = "breakout"  if breakout_score >= breakout_score_threshold
                     "reversion" otherwise

All three components are derived from market_data["ohlcv_window"] and
market_data["atr"] — no external data needed. This is a non-MTF strategy: it
only needs ohlcv_window, which backtester.py / orchestrator.py already supply
to every non-MTF strategy.

STATE HANDOFF (BUG-01 pattern): compute_levels() only receives (price, params)
and has no access to ohlcv_window/atr, so it cannot recompute breakout_score.
The resolved mode is decided once in signal() and handed to compute_levels()
via self._last_mode — an instance attribute that is RESET at the top of every
signal() call and overwritten, never accumulated. This is exactly the pattern
documented in alchemist.py's BUG-01 fix: both live trading and the backtester
call signal() immediately before compute_levels() on the same bar with no
interleaving, so a plain self._last_mode is safe. The target level itself is
NOT stashed — it is a pure function of price, so compute_levels() recomputes it
the same way signal() did.

Post-hoc "which mode fired" analytics: if that visibility ever matters it needs
a small hook in backtester.py's _build_trade_log_entry() to pull self._last_mode
(or self._last_breakout_score) off the strategy after a signal fires. Not
required for a working version — left as instance attributes for that purpose.
"""
from typing import Any

from app.strategy.base import BaseStrategy


class KeyLevelStrategy(BaseStrategy):
    name = "Key_Level"
    display_name = "Key Level (dynamic breakout/reversion)"
    description = (
        "XAUUSD round-number key-level strategy. Computes a continuous breakout-"
        "propensity score from momentum, volatility regime and recent level "
        "history, then resolves breakout vs reversion mode per signal against a "
        "tunable threshold. XAUUSD-only."
    )

    # Advisory only — the orchestrator does not enforce this, but it flags the
    # strategy as XAUUSD-specific the same way the concept spec asks.
    applicable_symbols = ["XAUUSD"]

    is_adaptive = False  # no bespoke adapt(); optimizer tunes via PARAM_BOUNDS

    DEFAULT_PARAMS: dict[str, Any] = {
        # Level detection & approach zone (integer-typed counts step as ints)
        "key_level_interval":         100,   # round-number spacing ($)
        "approach_zone_points":       40.0,  # max distance from level to consider
        "min_gap_points":             5.0,   # min distance from level (avoid on-top entries)
        # Momentum confirmation
        "momentum_lookback":          5,     # bars
        "min_momentum_points":        20.0,  # min net move to confirm real momentum
        # Volatility regime
        "volatility_lookback":        30,    # bars for true-range percentile
        # Level history
        "level_history_lookback":     120,   # bars scanned for prior level tests
        "level_test_resolution_bars": 12,    # bars given for a prior test to resolve
        "level_history_max_tests":    8,     # cap on conclusive events tallied
        # Breakout-propensity score weights (normalized to sum to 1 at eval time)
        "w_momentum":                 0.4,
        "w_volatility":               0.3,
        "w_history":                  0.3,
        "breakout_score_threshold":   0.5,   # decision boundary breakout vs reversion
        # Entry / exit
        "tp_beyond_points":           25.0,  # breakout-mode TP offset past the level
        "stop_loss_pct":              0.3,   # SL as % of price
        "tp1_multiplier":             1.0,
        "tp2_multiplier":             2.0,
        "tp3_multiplier":             3.0,
        "tp4_multiplier":             4.0,
        "min_rr_ratio":               1.2,   # minimum reward/risk to accept a trade
        "min_confidence_threshold":   0.4,   # minimum signal confidence to fire
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "key_level_interval":         (50, 200),
        "approach_zone_points":       (10, 80),
        "min_gap_points":             (1, 20),
        "momentum_lookback":          (2, 15),
        "min_momentum_points":        (5, 50),
        "volatility_lookback":        (10, 60),
        "level_history_lookback":     (30, 200),
        "level_test_resolution_bars": (5, 30),
        "level_history_max_tests":    (3, 15),
        "w_momentum":                 (0.0, 1.0),
        "w_volatility":               (0.0, 1.0),
        "w_history":                  (0.0, 1.0),
        "breakout_score_threshold":   (0.3, 0.7),
        "tp_beyond_points":           (5, 60),
        "stop_loss_pct":              (0.1, 1.0),
        "tp1_multiplier":             (0.5, 6.0),
        "tp2_multiplier":             (0.5, 6.0),
        "tp3_multiplier":             (0.5, 6.0),
        "tp4_multiplier":             (0.5, 6.0),
        "min_rr_ratio":               (0.8, 3.0),
        "min_confidence_threshold":   (0.2, 0.8),
    }

    # Feasibility guard for RESTART sampling only (coordinate ascent still uses
    # the full PARAM_BOUNDS). Sampling these gating params near the top of their
    # range routinely produces ZERO trades, wasting a backtest and starving the
    # search of gradient — same rationale as Alchemist's RESTART_SAMPLE_BOUNDS
    # and the "Alchemist ZERO TRADES" diagnostic in backtester.py. Capping the
    # restart range keeps fresh bases in trade-producing territory; the search
    # can still push past these ceilings via the gradient if it genuinely helps.
    RESTART_SAMPLE_BOUNDS: dict[str, tuple[float, float]] = {
        "breakout_score_threshold": (0.4, 0.6),   # full range 0.3–0.7 skews mode picks
        "min_confidence_threshold": (0.2, 0.45),  # full range up to 0.8 rejects most setups
    }

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        """
        Return (direction, confidence) — direction is "BUY", "SELL" or None.

        Never raises: degrades to (None, 0.0) on missing price / insufficient
        window. Resolves the per-signal mode into self._last_mode (reset here,
        overwritten every call) for compute_levels() to pick up — the BUG-01
        state-handoff pattern documented at module top.
        """
        # BUG-01 pattern: reset per-call state before every evaluation so state
        # from bar N never bleeds into bar N+1.
        self._last_mode: str | None = None
        self._last_breakout_score: float | None = None

        ohlcv = market_data.get("ohlcv_window")
        if not ohlcv:
            return None, 0.0

        p = self.params
        interval = int(p.get("key_level_interval", 100))
        if interval <= 0:
            return None, 0.0
        approach_zone = float(p.get("approach_zone_points", 40.0))
        min_gap = float(p.get("min_gap_points", 5.0))
        momentum_lookback = max(1, int(p.get("momentum_lookback", 5)))
        min_momentum = float(p.get("min_momentum_points", 20.0))

        # Need at least enough bars to measure momentum plus a small history.
        if len(ohlcv) < momentum_lookback + 2:
            return None, 0.0

        try:
            closes = [float(b["close"]) for b in ohlcv]
        except (KeyError, TypeError, ValueError):
            return None, 0.0

        price = closes[-1]
        if price is None or price <= 0:
            return None, 0.0

        # ── Level detection & approach zone ──────────────────────────────────
        level_below = (price // interval) * interval
        level_above = level_below + interval
        dist_above = level_above - price     # >0 when price is below level_above
        dist_below = price - level_below     # >0 when price is above level_below

        # ── Momentum: net change over the lookback (sign = approach direction) ─
        net_change = price - closes[-1 - momentum_lookback]
        if abs(net_change) < min_momentum:
            return None, 0.0  # choppy / directionless drift — filter regardless of mode

        # Pair the approach direction with the level it is heading into. Rising
        # toward level_above, or falling toward level_below.
        if net_change > 0 and min_gap <= dist_above <= approach_zone:
            target_level = level_above
            approach = "rising"
        elif net_change < 0 and min_gap <= dist_below <= approach_zone:
            target_level = level_below
            approach = "falling"
        else:
            return None, 0.0

        # ── Breakout-propensity components (all in [0, 1]) ───────────────────
        momentum_component = min(abs(net_change) / (min_momentum * 3.0), 1.0)
        volatility_component = self._volatility_percentile(ohlcv)
        history_component = self._level_break_rate(closes, interval)

        # Normalize weights to sum to 1 at eval time (spec).
        w_m = max(0.0, float(p.get("w_momentum", 0.4)))
        w_v = max(0.0, float(p.get("w_volatility", 0.3)))
        w_h = max(0.0, float(p.get("w_history", 0.3)))
        w_sum = w_m + w_v + w_h
        if w_sum <= 0:
            w_m = w_v = w_h = 1.0 / 3.0
        else:
            w_m, w_v, w_h = w_m / w_sum, w_v / w_sum, w_h / w_sum

        breakout_score = (
            w_m * momentum_component
            + w_v * volatility_component
            + w_h * history_component
        )
        self._last_breakout_score = breakout_score

        threshold = float(p.get("breakout_score_threshold", 0.5))
        if breakout_score >= threshold:
            mode = "breakout"
        else:
            mode = "reversion"
        self._last_mode = mode

        # ── Resolve traded direction from (approach, mode) ───────────────────
        #   rising  toward level_above:  breakout -> BUY,  reversion -> SELL
        #   falling toward level_below:  breakout -> SELL, reversion -> BUY
        if approach == "rising":
            direction = "BUY" if mode == "breakout" else "SELL"
        else:  # falling
            direction = "SELL" if mode == "breakout" else "BUY"

        # ── Confidence: conviction of the resolved mode vs the boundary ──────
        if mode == "breakout":
            denom = max(1.0 - threshold, 1e-9)
            norm_dist = (breakout_score - threshold) / denom
        else:
            denom = max(threshold, 1e-9)
            norm_dist = (threshold - breakout_score) / denom
        confidence = 0.5 + 0.5 * max(0.0, min(norm_dist, 1.0))
        confidence = max(0.0, min(confidence, 1.0))

        if confidence < float(p.get("min_confidence_threshold", 0.4)):
            return None, 0.0

        return direction, confidence

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        """
        Return {"sl", "tp1", "tp2", "tp3", "tp4"} or {} if min RR is not met.

        Reads the resolved mode from self._last_mode (set by the signal() call
        on this same bar — BUG-01 handoff). The target level is recomputed from
        price alone since it is a pure function of price.
        """
        if not direction:
            return {}

        params = self._sort_tp_multipliers(params)
        mode = getattr(self, "_last_mode", None) or "reversion"

        stop_loss_pct = float(params.get("stop_loss_pct", 0.3))
        sl_dist = price * (stop_loss_pct / 100.0)
        if sl_dist <= 0:
            return {}
        sign = 1 if direction == "BUY" else -1

        tp1_m = float(params.get("tp1_multiplier", 1.0))
        tp2_m = float(params.get("tp2_multiplier", 2.0))
        tp3_m = float(params.get("tp3_multiplier", 3.0))
        tp4_m = float(params.get("tp4_multiplier", 4.0))

        sl = price - sign * sl_dist

        if mode == "breakout":
            # TP anchored to the level being broken, offset by tp_beyond_points *
            # multiplier PAST it in the trade direction.
            interval = int(params.get("key_level_interval", 100))
            if interval <= 0:
                return {}
            tp_beyond = float(params.get("tp_beyond_points", 25.0))
            level_below = (price // interval) * interval
            level_above = level_below + interval
            # BUY breaks up through level_above; SELL breaks down through level_below.
            level = level_above if direction == "BUY" else level_below
            tp1 = level + sign * tp_beyond * tp1_m
            tp2 = level + sign * tp_beyond * tp2_m
            tp3 = level + sign * tp_beyond * tp3_m
            tp4 = level + sign * tp_beyond * tp4_m
        else:
            # Reversion: plain percentage-of-price targets around entry — betting
            # on a bounce back the way price came, not a break.
            tp1 = price + sign * sl_dist * tp1_m
            tp2 = price + sign * sl_dist * tp2_m
            tp3 = price + sign * sl_dist * tp3_m
            tp4 = price + sign * sl_dist * tp4_m

        # Same reward/risk gate in both modes.
        min_rr = float(params.get("min_rr_ratio", 1.2))
        risk = abs(price - sl)
        reward = abs(tp2 - price)
        if risk <= 0 or (reward / risk) < min_rr:
            return {}

        return {
            "sl":  round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(tp2, 5),
            "tp3": round(tp3, 5),
            "tp4": round(tp4, 5),
        }

    # -------------------------------------------------------------------------
    # Component helpers
    # -------------------------------------------------------------------------

    def _volatility_percentile(self, ohlcv: list) -> float:
        """
        Percentile rank of the current bar's true range within the trailing
        volatility_lookback window (capped at the 200-bar window the backtester
        provides). 0 = quiet market, 1 = expanding/volatile. Expansion favors
        breakout; compression favors reversion.
        """
        lookback = max(2, int(self.params.get("volatility_lookback", 30)))
        window = ohlcv[-min(lookback, len(ohlcv)):]
        if len(window) < 2:
            return 0.5

        trs: list[float] = []
        prev_close = None
        for b in window:
            try:
                high = float(b["high"])
                low = float(b["low"])
                close = float(b["close"])
            except (KeyError, TypeError, ValueError):
                return 0.5
            if prev_close is None:
                tr = high - low
            else:
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
            prev_close = close

        current_tr = trs[-1]
        # Percentile rank = fraction of bars whose TR <= current bar's TR.
        n_le = sum(1 for t in trs if t <= current_tr)
        return n_le / len(trs)

    def _level_break_rate(self, closes: list, interval: int) -> float:
        """
        Recent break-rate for this exact level spacing.

        Scan back through the window up to level_history_lookback bars. Each time
        price ENTERS an approach zone around any level of `interval` spacing and
        wasn't in one on the prior bar, that's an approach event. Look forward up
        to level_test_resolution_bars: close beyond the level by >= min_gap_points
        counts as BROKEN; price reversing back past its zone-entry point without
        breaking counts as REJECTED; otherwise inconclusive/discarded. Take the
        last level_history_max_tests conclusive events and return
        breaks / (breaks + rejects). Default 0.5 (neutral) if not enough history.
        """
        p = self.params
        approach_zone = float(p.get("approach_zone_points", 40.0))
        min_gap = float(p.get("min_gap_points", 5.0))
        lookback = max(2, int(p.get("level_history_lookback", 120)))
        resolution = max(1, int(p.get("level_test_resolution_bars", 12)))
        max_tests = max(1, int(p.get("level_history_max_tests", 8)))

        n = len(closes)
        if n < 3 or interval <= 0:
            return 0.5

        # Zone descriptor for a given close: which level it is approaching and
        # from which side. Returns (level, approach) or None. When near both a
        # level above and below, pick the closer one.
        def _zone(price: float):
            level_below = (price // interval) * interval
            level_above = level_below + interval
            dist_above = level_above - price
            dist_below = price - level_below
            above_in = min_gap <= dist_above <= approach_zone
            below_in = min_gap <= dist_below <= approach_zone
            if above_in and (not below_in or dist_above <= dist_below):
                return level_above, "rising"
            if below_in:
                return level_below, "falling"
            return None

        start = max(1, n - lookback)
        events: list[bool] = []  # True = broken, False = rejected

        for e in range(start, n - 1):
            zone_now = _zone(closes[e])
            if zone_now is None:
                continue
            # Rising edge: must not have been in a zone on the prior bar.
            if _zone(closes[e - 1]) is not None:
                continue

            level, approach = zone_now
            entry_price = closes[e]
            resolved = None  # True broken / False rejected
            for j in range(e + 1, min(e + 1 + resolution, n)):
                cj = closes[j]
                if approach == "rising":
                    if cj >= level + min_gap:
                        resolved = True
                        break
                    if cj < entry_price:
                        resolved = False
                        break
                else:  # falling
                    if cj <= level - min_gap:
                        resolved = True
                        break
                    if cj > entry_price:
                        resolved = False
                        break
            if resolved is not None:
                events.append(resolved)

        if not events:
            return 0.5
        events = events[-max_tests:]
        breaks = sum(1 for e in events if e)
        rejects = len(events) - breaks
        if breaks + rejects == 0:
            return 0.5
        return breaks / (breaks + rejects)
