"""
app/strategy/alchemist.py — Alchemist Strategy

Multi-confluent session-based strategy using:
  - Malaysian S/R (MSNR) zone detection (OCL, OB, QML)
  - Candle Range Theory (CRT) confirmation
  - ICT Smart Money Concepts (structure shifts, liquidity sweeps)
  - Fibonacci precision entries
  - HTF bias filtering
  - Killzone session filtering
  - SMT divergence (optional)
"""
import math
from datetime import datetime, time
from typing import Any

import numpy as np
import pandas as pd

from app.strategy.base import BaseStrategy


class Alchemist(BaseStrategy):
    name = "Alchemist"
    display_name = "Alchemist (MSNR + CRT + SMC)"
    description = (
        "Multi-confluent session-based strategy using Malaysian S/R, Candle Range Theory, "
        "ICT Smart Money Concepts, and Fibonacci precision entries."
    )
    requires_mtf = True  # orchestrator will fetch multi-timeframe bars before calling signal()
    is_adaptive = True   # BUG-07: real adapt() implementation exists

    DEFAULT_PARAMS: dict[str, Any] = {
        # Session / timing
        "killzone_filter_enabled": False,  # disabled by default; True for live trading
        "active_killzones": ["london_open", "ny_open", "overlap"],
        "judas_swing_filter": True,
        # MSNR zone detection
        "ocl_lookback_candles": 5,
        "ob_lookback_candles": 20,
        "qml_swing_lookback": 10,
        "zone_tolerance_pct": 0.003,       # was 0.0015 — wider for daily resolution
        # CRT parameters
        "crt_signal_timeframe": "1h",
        "crt_entry_timeframe": "15m",
        "crt_sweep_min_pips": 3,
        "crt_close_back_required": False,  # was True — too strict for daily bars
        # HTF bias
        "htf_bias_timeframe": "4h",
        "htf_ema_fast": 20,
        "htf_ema_slow": 50,
        # Entry precision
        "fib_entry_enabled": True,
        "fib_entry_level": 0.618,
        "fib_tp3_extension": 1.272,
        "fib_tp4_extension": 1.618,
        # Risk parameters
        "atr_sl_buffer": 0.5,
        "atr_period": 14,
        "min_rr_ratio": 1.2,               # was 1.5
        "min_confidence_threshold": 0.45,  # was 0.55
        # SMT divergence
        "smt_filter_enabled": False,
        "smt_correlated_symbol": "XAUUSD",
        # Structure
        "structure_shift_candles": 3,
        # Storyline
        "min_storyline_checks": 2,         # was hardcoded 3
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "ocl_lookback_candles":     (3, 20),
        "ob_lookback_candles":      (10, 50),
        "qml_swing_lookback":       (5, 30),
        "zone_tolerance_pct":       (0.0005, 0.005),
        "crt_sweep_min_pips":       (1, 10),
        "htf_ema_fast":             (10, 50),
        "htf_ema_slow":             (30, 200),
        "fib_entry_level":          (0.5, 0.79),
        "fib_tp3_extension":        (1.0, 1.618),
        "fib_tp4_extension":        (1.272, 2.618),
        "atr_sl_buffer":            (0.2, 2.0),
        "atr_period":               (7, 21),
        "min_rr_ratio":             (1.0, 4.0),
        "min_confidence_threshold": (0.4, 0.85),
        "structure_shift_candles":  (2, 8),
    }

    KILLZONE_WINDOWS: dict[str, tuple[time, time]] = {
        "london_open": (time(7, 0),  time(10, 0)),
        "ny_open":     (time(12, 0), time(15, 0)),
        "overlap":     (time(12, 0), time(14, 0)),
        "asian":       (time(0, 0),  time(3, 0)),
    }

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    # -------------------------------------------------------------------------
    # Utility: daily-bar detection
    # -------------------------------------------------------------------------

    @staticmethod
    def _is_daily_bars(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 2:
            return False
        try:
            idx = pd.to_datetime(bars.index)
            median_gap_hours = (
                idx.to_series().diff().dropna().median().total_seconds() / 3600
            )
            return median_gap_hours >= 20
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        """
        Returns (direction, confidence) where direction is "BUY", "SELL", or None.

        BUG-01 FIX: signal() no longer writes shared instance state that would
        be visible to concurrent or out-of-order calls.  Instead, intermediate
        results (zone, confidence_components) are stored in instance attributes
        that are overwritten on EVERY call, not accumulated.  The live trading
        loop is single-threaded async so calls are always sequential — this is
        safe.  _last_zone is exposed so compute_levels() can pick it up without
        needing market_data re-passed.  A comment on compute_levels() documents
        this assumption.

        Expected market_data keys:
          symbol            str
          timestamp         datetime
          current_price     float
          atr               float
          1d_bars           pd.DataFrame  (date index, OHLCV columns)
          4h_bars           pd.DataFrame
          1h_bars           pd.DataFrame
          15m_bars          pd.DataFrame
          htf_ema_fast      float | None   (pre-computed, optional)
          htf_ema_slow      float | None   (pre-computed, optional)
          correlated_bars   pd.DataFrame   (optional, for SMT)
        """
        # BUG-01 FIX: Reset per-call state before every signal evaluation.
        # These are overwritten each call rather than accumulated, so sequential
        # calls on the same instance don't bleed state from bar N into bar N+1.
        self._last_market_data: dict = market_data
        self._last_zone: dict | None = None
        self._confidence_components: dict = {}

        htf_bias = self._htf_bias(market_data)
        if htf_bias == "NEUTRAL":
            return None, 0.0

        if self.params.get("killzone_filter_enabled", False):
            ts = market_data.get("timestamp")
            if ts is None or not self._in_killzone(ts):
                return None, 0.0

        if htf_bias == "BULLISH":
            if self._is_buy_signal(market_data):
                confidence = self._compute_confidence(htf_bias, "BUY", market_data)
                if confidence >= self.params.get("min_confidence_threshold", 0.45):
                    return "BUY", confidence

        elif htf_bias == "BEARISH":
            if self._is_sell_signal(market_data):
                confidence = self._compute_confidence(htf_bias, "SELL", market_data)
                if confidence >= self.params.get("min_confidence_threshold", 0.45):
                    return "SELL", confidence

        return None, 0.0

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        """
        Returns {"sl": float, "tp1": float, "tp2": float, "tp3": float, "tp4": float}
        or {} if minimum RR is not met.

        BUG-01 FIX: Reads market_data from self._last_market_data which is set by
        the most recent signal() call.  This is safe because the live trading loop
        is single-threaded async — signal() is always called before compute_levels()
        on the same bar, with no interleaving.
        """
        if not direction:
            return {}

        # BUG-01 FIX: use _last_market_data set by signal(), not self._market_data
        md = getattr(self, "_last_market_data", {})
        atr = md.get("atr", price * 0.005)
        bars_1h: pd.DataFrame = md.get("1h_bars", pd.DataFrame())
        if bars_1h.empty or len(bars_1h) < 3:
            bars_1h = md.get("4h_bars", pd.DataFrame())
        if bars_1h.empty or len(bars_1h) < 3:
            bars_1h = md.get("1d_bars", pd.DataFrame())

        atr_buffer = params.get("atr_sl_buffer", 0.5)
        min_rr = params.get("min_rr_ratio", 1.2)

        if self._is_daily_bars(bars_1h):
            atr_buffer = min(atr_buffer, 0.3)
            min_rr = min(min_rr, 1.0)

        if bars_1h.empty or len(bars_1h) < 3:
            return {}

        manip_candle = bars_1h.iloc[-2]
        range_candle = bars_1h.iloc[-3]

        if direction == "BUY":
            sl = float(manip_candle["low"]) - (atr_buffer * atr)
            tp1 = float(range_candle["high"])
            swing_high = float(bars_1h["high"].iloc[-20:].max())
            swing_low = float(bars_1h["low"].iloc[-20:].min())
            fib = self._compute_fibonacci_levels(swing_low, swing_high, "BUY")
            tp2 = swing_high
            tp3 = fib["tp3"]
            tp4 = fib["tp4"]
        else:  # SELL
            sl = float(manip_candle["high"]) + (atr_buffer * atr)
            tp1 = float(range_candle["low"])
            swing_high = float(bars_1h["high"].iloc[-20:].max())
            swing_low = float(bars_1h["low"].iloc[-20:].min())
            fib = self._compute_fibonacci_levels(swing_low, swing_high, "SELL")
            tp2 = swing_low
            tp3 = fib["tp3"]
            tp4 = fib["tp4"]

        sl_dist = abs(price - sl)
        tp2_dist = abs(tp2 - price)
        if sl_dist == 0 or (tp2_dist / sl_dist) < min_rr:
            return {}

        return {
            "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(tp2, 5),
            "tp3": round(tp3, 5),
            "tp4": round(tp4, 5),
        }

    # -------------------------------------------------------------------------
    # Signal composition helpers
    # -------------------------------------------------------------------------

    def _is_buy_signal(self, market_data: dict) -> bool:
        return (
            self._at_demand_zone(market_data)
            and self._crt_bullish_sweep_confirmed(market_data)
            and self._structure_shift_to_bullish(market_data)
            and self._check_storyline(market_data)
            and (not self.params.get("smt_filter_enabled") or self._smt_bullish(market_data))
        )

    def _is_sell_signal(self, market_data: dict) -> bool:
        return (
            self._at_supply_zone(market_data)
            and self._crt_bearish_sweep_confirmed(market_data)
            and self._structure_shift_to_bearish(market_data)
            and self._check_storyline(market_data)
            and (not self.params.get("smt_filter_enabled") or self._smt_bearish(market_data))
        )

    # -------------------------------------------------------------------------
    # HTF Bias
    # -------------------------------------------------------------------------

    def _htf_bias(self, market_data: dict) -> str:
        bars_4h: pd.DataFrame = market_data.get("4h_bars", pd.DataFrame())
        if not bars_4h.empty and len(bars_4h) >= self.params.get("htf_ema_slow", 50):
            close = bars_4h["close"]
            ema_fast = close.ewm(span=self.params.get("htf_ema_fast", 20), adjust=False).mean().iloc[-1]
            ema_slow = close.ewm(span=self.params.get("htf_ema_slow", 50), adjust=False).mean().iloc[-1]
            price = float(close.iloc[-1])
            if price > ema_fast and ema_fast > ema_slow:
                return "BULLISH"
            if price < ema_fast and ema_fast < ema_slow:
                return "BEARISH"
            return "NEUTRAL"

        bars_1d: pd.DataFrame = market_data.get("1d_bars", pd.DataFrame())
        if bars_1d.empty or len(bars_1d) < 20:
            return "NEUTRAL"
        close = bars_1d["close"]
        ema_fast = close.ewm(span=5, adjust=False).mean().iloc[-1]
        ema_slow = close.ewm(span=20, adjust=False).mean().iloc[-1]
        price = float(close.iloc[-1])
        if price > ema_fast and ema_fast > ema_slow:
            return "BULLISH"
        if price < ema_fast and ema_fast < ema_slow:
            return "BEARISH"
        return "NEUTRAL"

    # -------------------------------------------------------------------------
    # Killzone Filter
    # -------------------------------------------------------------------------

    def _in_killzone(self, timestamp: datetime) -> bool:
        active = self.params.get("active_killzones", ["london_open", "ny_open", "overlap"])
        t = timestamp.time() if isinstance(timestamp, datetime) else timestamp
        return any(
            self.KILLZONE_WINDOWS[kz][0] <= t <= self.KILLZONE_WINDOWS[kz][1]
            for kz in active
            if kz in self.KILLZONE_WINDOWS
        )

    # -------------------------------------------------------------------------
    # MSNR Zone Detection
    # -------------------------------------------------------------------------

    def _find_msnr_zones(self, market_data: dict) -> list[dict]:
        zones: list[dict] = []
        bars_1d: pd.DataFrame = market_data.get("1d_bars", pd.DataFrame())
        bars_4h: pd.DataFrame = market_data.get("4h_bars", pd.DataFrame())
        bars_1h: pd.DataFrame = market_data.get("1h_bars", pd.DataFrame())
        current_price: float = market_data.get("current_price", 0.0)

        n_ocl = self.params.get("ocl_lookback_candles", 5)
        if not bars_1d.empty:
            for i in range(min(n_ocl, len(bars_1d))):
                row = bars_1d.iloc[-(i + 1)]
                for lvl_name in ("open", "close"):
                    level = float(row[lvl_name])
                    zones.append({
                        "type": "OCL",
                        "level": level,
                        "direction": "SUPPORT" if level < current_price else "RESISTANCE",
                        "freshness": i,
                    })

        ob_lookback = self.params.get("ob_lookback_candles", 20)
        if not bars_4h.empty and len(bars_4h) >= ob_lookback:
            window = bars_4h.iloc[-ob_lookback:]
            for i in range(1, len(window) - 1):
                candle = window.iloc[i]
                next_c = window.iloc[i + 1]
                candle_body = abs(float(candle["close"]) - float(candle["open"]))
                next_body = abs(float(next_c["close"]) - float(next_c["open"]))

                if (
                    float(candle["close"]) < float(candle["open"])
                    and float(next_c["close"]) > float(next_c["open"])
                    and next_body > 2 * candle_body
                ):
                    zones.append({
                        "type": "OB",
                        "level": (float(candle["open"]) + float(candle["close"])) / 2,
                        "direction": "SUPPORT",
                        "zone_low": float(candle["close"]),
                        "zone_high": float(candle["open"]),
                        "freshness": len(window) - i,
                    })

                if (
                    float(candle["close"]) > float(candle["open"])
                    and float(next_c["close"]) < float(next_c["open"])
                    and next_body > 2 * candle_body
                ):
                    zones.append({
                        "type": "OB",
                        "level": (float(candle["open"]) + float(candle["close"])) / 2,
                        "direction": "RESISTANCE",
                        "zone_low": float(candle["open"]),
                        "zone_high": float(candle["close"]),
                        "freshness": len(window) - i,
                    })

        qml_lookback = self.params.get("qml_swing_lookback", 10)
        if not bars_1h.empty and len(bars_1h) >= qml_lookback:
            zones += self._find_qml_zones(bars_1h.iloc[-qml_lookback:], current_price)

        return zones

    def _find_qml_zones(self, bars: pd.DataFrame, current_price: float) -> list[dict]:
        zones: list[dict] = []
        highs = bars["high"].values.astype(float)
        lows = bars["low"].values.astype(float)

        for i in range(2, len(bars) - 2):
            if (
                highs[i] > highs[i - 2]
                and lows[i] > lows[i - 2]
                and highs[i + 1] > highs[i]
                and lows[i + 1] < lows[i]
            ):
                zones.append({
                    "type": "QML",
                    "level": float(lows[i]),
                    "direction": "RESISTANCE",
                    "freshness": len(bars) - i,
                })

            if (
                lows[i] < lows[i - 2]
                and highs[i] < highs[i - 2]
                and lows[i + 1] < lows[i]
                and highs[i + 1] > highs[i]
            ):
                zones.append({
                    "type": "QML",
                    "level": float(highs[i]),
                    "direction": "SUPPORT",
                    "freshness": len(bars) - i,
                })

        return zones

    # -------------------------------------------------------------------------
    # Zone proximity checks
    # -------------------------------------------------------------------------

    def _at_demand_zone(self, market_data: dict) -> bool:
        zones = self._find_msnr_zones(market_data)
        tol = self.params.get("zone_tolerance_pct", 0.003)
        price = market_data.get("current_price", 0.0)
        for z in zones:
            if z["direction"] == "SUPPORT" and abs(price - z["level"]) / max(price, 1e-9) <= tol:
                # BUG-01 FIX: store in _last_zone (overwritten each call, not accumulated)
                self._last_zone = z
                return True
        return False

    def _at_supply_zone(self, market_data: dict) -> bool:
        zones = self._find_msnr_zones(market_data)
        tol = self.params.get("zone_tolerance_pct", 0.003)
        price = market_data.get("current_price", 0.0)
        for z in zones:
            if z["direction"] == "RESISTANCE" and abs(price - z["level"]) / max(price, 1e-9) <= tol:
                # BUG-01 FIX: store in _last_zone (overwritten each call, not accumulated)
                self._last_zone = z
                return True
        return False

    # -------------------------------------------------------------------------
    # CRT Confirmation
    # -------------------------------------------------------------------------

    def _crt_bullish_sweep_confirmed(self, market_data: dict) -> bool:
        bars: pd.DataFrame = market_data.get("1h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 3:
            bars = market_data.get("4h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 3:
            bars = market_data.get("1d_bars", pd.DataFrame())
        if len(bars) < 3:
            return False
        range_candle = bars.iloc[-3]
        manip_candle = bars.iloc[-2]
        dist_candle = bars.iloc[-1]

        is_daily = self._is_daily_bars(bars)
        if is_daily:
            min_sweep = max(
                self.params.get("crt_sweep_min_pips", 3) * 0.1,
                float(range_candle["low"]) * 0.001,
            )
        else:
            min_sweep = max(
                self.params.get("crt_sweep_min_pips", 3) * 0.0001,
                float(range_candle["low"]) * 0.0002,
            )

        swept = float(manip_candle["low"]) < float(range_candle["low"]) - min_sweep
        closed_back = float(manip_candle["close"]) > float(range_candle["low"])
        dist_bullish = float(dist_candle["close"]) > float(dist_candle["open"])

        if self.params.get("crt_close_back_required", False):
            return swept and closed_back and dist_bullish
        return swept and dist_bullish

    def _crt_bearish_sweep_confirmed(self, market_data: dict) -> bool:
        bars: pd.DataFrame = market_data.get("1h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 3:
            bars = market_data.get("4h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 3:
            bars = market_data.get("1d_bars", pd.DataFrame())
        if len(bars) < 3:
            return False
        range_candle = bars.iloc[-3]
        manip_candle = bars.iloc[-2]
        dist_candle = bars.iloc[-1]

        is_daily = self._is_daily_bars(bars)
        if is_daily:
            min_sweep = max(
                self.params.get("crt_sweep_min_pips", 3) * 0.1,
                float(range_candle["high"]) * 0.001,
            )
        else:
            min_sweep = max(
                self.params.get("crt_sweep_min_pips", 3) * 0.0001,
                float(range_candle["high"]) * 0.0002,
            )

        swept = float(manip_candle["high"]) > float(range_candle["high"]) + min_sweep
        closed_back = float(manip_candle["close"]) < float(range_candle["high"])
        dist_bearish = float(dist_candle["close"]) < float(dist_candle["open"])

        if self.params.get("crt_close_back_required", False):
            return swept and closed_back and dist_bearish
        return swept and dist_bearish

    # -------------------------------------------------------------------------
    # Structure shift detection
    # -------------------------------------------------------------------------

    def _structure_shift_to_bullish(self, market_data: dict) -> bool:
        bars: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        if bars.empty or len(bars) < 4:
            bars = market_data.get("1h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 4:
            bars = market_data.get("4h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 4:
            bars = market_data.get("1d_bars", pd.DataFrame())
        n = self.params.get("structure_shift_candles", 3)
        if len(bars) < n + 1:
            return False
        window = bars.iloc[-(n + 1):]
        recent_high = float(window["high"].iloc[:-1].max())
        return float(bars["close"].iloc[-1]) > recent_high

    def _structure_shift_to_bearish(self, market_data: dict) -> bool:
        bars: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        if bars.empty or len(bars) < 4:
            bars = market_data.get("1h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 4:
            bars = market_data.get("4h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 4:
            bars = market_data.get("1d_bars", pd.DataFrame())
        n = self.params.get("structure_shift_candles", 3)
        if len(bars) < n + 1:
            return False
        window = bars.iloc[-(n + 1):]
        recent_low = float(window["low"].iloc[:-1].min())
        return float(bars["close"].iloc[-1]) < recent_low

    # -------------------------------------------------------------------------
    # Liquidity sweep check
    # -------------------------------------------------------------------------

    def _liquidity_swept(self, market_data: dict, side: str) -> bool:
        bars: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        if bars.empty or len(bars) < 6:
            bars = market_data.get("1h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 6:
            bars = market_data.get("4h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 6:
            bars = market_data.get("1d_bars", pd.DataFrame())
        if len(bars) < 6:
            return False

        is_daily = self._is_daily_bars(bars)
        lookback = 10 if is_daily else 5
        if len(bars) < lookback + 1:
            lookback = len(bars) - 1

        window = bars.iloc[-(lookback + 1):-1]
        last_bar = bars.iloc[-1]
        if side == "low":
            prior_swing_low = float(window["low"].min())
            return float(last_bar["low"]) < prior_swing_low
        else:
            prior_swing_high = float(window["high"].max())
            return float(last_bar["high"]) > prior_swing_high

    # -------------------------------------------------------------------------
    # Fibonacci levels
    # -------------------------------------------------------------------------

    def _compute_fibonacci_levels(self, swing_low: float, swing_high: float, direction: str) -> dict:
        rng = swing_high - swing_low
        tp3_ext = self.params.get("fib_tp3_extension", 1.272)
        tp4_ext = self.params.get("fib_tp4_extension", 1.618)
        fib_level = self.params.get("fib_entry_level", 0.618)

        if direction == "BUY":
            entry_fib = swing_high - fib_level * rng
            tp3 = swing_high + tp3_ext * rng
            tp4 = swing_high + tp4_ext * rng
        else:
            entry_fib = swing_low + fib_level * rng
            tp3 = swing_low - tp3_ext * rng
            tp4 = swing_low - tp4_ext * rng

        return {"entry_fib": entry_fib, "tp3": tp3, "tp4": tp4}

    # -------------------------------------------------------------------------
    # Storyline checklist
    # -------------------------------------------------------------------------

    def _check_storyline(self, market_data: dict) -> bool:
        checks: dict[str, bool] = {}
        available: set[str] = set()

        bars_1d: pd.DataFrame = market_data.get("1d_bars", pd.DataFrame())
        bars_1h: pd.DataFrame = market_data.get("1h_bars", pd.DataFrame())
        bars_15m: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        eff_1h = bars_1h if (not bars_1h.empty and len(bars_1h) >= 3) else market_data.get("4h_bars", pd.DataFrame())
        if eff_1h.empty or len(eff_1h) < 3:
            eff_1h = bars_1d
        eff_15m = bars_15m if (not bars_15m.empty and len(bars_15m) >= 4) else eff_1h

        available.add("weekly_ocl_side")
        if not bars_1d.empty:
            weekly_ocl = float(
                bars_1d["close"].iloc[-5] if len(bars_1d) >= 5 else bars_1d["close"].iloc[-1]
            )
            htf = self._htf_bias(market_data)
            price = market_data.get("current_price", 0.0)
            checks["weekly_ocl_side"] = (
                (htf == "BULLISH" and price > weekly_ocl)
                or (htf == "BEARISH" and price < weekly_ocl)
            )
        else:
            checks["weekly_ocl_side"] = False

        htf = self._htf_bias(market_data)
        if not eff_1h.empty and len(eff_1h) >= 3:
            available.add("crt_confirmed")
            checks["crt_confirmed"] = (
                self._crt_bullish_sweep_confirmed(market_data)
                or self._crt_bearish_sweep_confirmed(market_data)
            )

        if not bars_1d.empty:
            available.add("fresh_zone")
            zones = self._find_msnr_zones(market_data)
            target_direction = "SUPPORT" if htf == "BULLISH" else "RESISTANCE"
            tol = self.params.get("zone_tolerance_pct", 0.003)
            price = market_data.get("current_price", 0.0)
            fresh_zones = [
                z for z in zones
                if z["direction"] == target_direction
                and z.get("freshness", 99) <= 5
                and abs(price - z["level"]) / max(price, 1e-9) <= tol
            ]
            checks["fresh_zone"] = len(fresh_zones) > 0

        if not eff_1h.empty and len(eff_1h) >= 10:
            available.add("structure_aligned")
            price = market_data.get("current_price", 0.0)
            window_highs = eff_1h["high"].iloc[-10:-1]
            window_lows  = eff_1h["low"].iloc[-10:-1]
            if htf == "BULLISH":
                checks["structure_aligned"] = price > float(window_highs.median())
            else:
                checks["structure_aligned"] = price < float(window_lows.median())

        if not eff_15m.empty and len(eff_15m) >= 4:
            available.add("entry_pattern")
            checks["entry_pattern"] = (
                self._structure_shift_to_bullish(market_data)
                or self._structure_shift_to_bearish(market_data)
            )

        bars_for_sweep = eff_15m if (not eff_15m.empty and len(eff_15m) >= 6) else eff_1h
        if not bars_for_sweep.empty and len(bars_for_sweep) >= 6:
            available.add("liquidity_swept")
            if htf == "BULLISH":
                checks["liquidity_swept"] = self._liquidity_swept(market_data, "low")
            else:
                checks["liquidity_swept"] = self._liquidity_swept(market_data, "high")

        self._confidence_components["storyline_checks"] = checks

        n_available = len(available)
        if n_available == 0:
            return False
        passed = sum(checks.get(k, False) for k in available)
        min_required = min(
            self.params.get("min_storyline_checks", 2),
            n_available,
        )
        return passed >= min_required

    # -------------------------------------------------------------------------
    # Confidence scoring
    # -------------------------------------------------------------------------

    def _compute_confidence(self, htf_bias: str, direction: str, market_data: dict) -> float:
        score = 0.0
        score += 0.20

        if self.params.get("killzone_filter_enabled", False):
            score += 0.15

        if direction == "BUY":
            crt_confirmed = self._crt_bullish_sweep_confirmed(market_data)
        else:
            crt_confirmed = self._crt_bearish_sweep_confirmed(market_data)
        if crt_confirmed:
            score += 0.20

        # BUG-01 FIX: read from _last_zone (set by _at_demand_zone/_at_supply_zone above)
        zone = getattr(self, "_last_zone", None)
        if zone:
            zone_weights = {"QML": 0.15, "OB": 0.12, "OCL": 0.10, "SBR": 0.08, "RBS": 0.08}
            score += zone_weights.get(zone.get("type", ""), 0.08)

        if self._structure_shift_to_bullish(market_data) or self._structure_shift_to_bearish(market_data):
            score += 0.15

        if self.params.get("fib_entry_enabled"):
            bars_1h: pd.DataFrame = market_data.get("1h_bars", pd.DataFrame())
            if not bars_1h.empty and len(bars_1h) >= 20:
                swing_high = float(bars_1h["high"].iloc[-20:].max())
                swing_low = float(bars_1h["low"].iloc[-20:].min())
                fib = self._compute_fibonacci_levels(swing_low, swing_high, direction)
                tol = self.params.get("zone_tolerance_pct", 0.003)
                price = market_data.get("current_price", 0.0)
                if abs(price - fib["entry_fib"]) / max(price, 1e-9) <= tol:
                    score += 0.10

        if self.params.get("smt_filter_enabled"):
            smt_ok = (
                self._smt_bullish(market_data)
                if direction == "BUY"
                else self._smt_bearish(market_data)
            )
            if smt_ok:
                score += 0.05

        return min(score, 1.0)

    # -------------------------------------------------------------------------
    # SMT Divergence
    # -------------------------------------------------------------------------

    def _smt_bullish(self, market_data: dict) -> bool:
        primary_15m: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        corr_bars: pd.DataFrame = market_data.get("correlated_bars", pd.DataFrame())
        if (
            primary_15m.empty or corr_bars.empty
            or len(primary_15m) < 12 or len(corr_bars) < 12
        ):
            return False
        primary_ll = float(primary_15m["low"].iloc[-6:].min()) < float(primary_15m["low"].iloc[-12:-6].min())
        corr_ll = float(corr_bars["low"].iloc[-6:].min()) < float(corr_bars["low"].iloc[-12:-6].min())
        return primary_ll and not corr_ll

    def _smt_bearish(self, market_data: dict) -> bool:
        primary_15m: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        corr_bars: pd.DataFrame = market_data.get("correlated_bars", pd.DataFrame())
        if (
            primary_15m.empty or corr_bars.empty
            or len(primary_15m) < 12 or len(corr_bars) < 12
        ):
            return False
        primary_hh = float(primary_15m["high"].iloc[-6:].max()) > float(primary_15m["high"].iloc[-12:-6].max())
        corr_hh = float(corr_bars["high"].iloc[-6:].max()) > float(corr_bars["high"].iloc[-12:-6].max())
        return primary_hh and not corr_hh

    # -------------------------------------------------------------------------
    # Adaptation logic
    # -------------------------------------------------------------------------

    def adapt(self, trades: list, learning_settings: dict) -> dict:
        """
        Adapts Alchemist-specific parameters based on recent trade outcomes.
        Trades may be ORM Trade objects or dicts. Returns updated params dict.
        """
        if not trades:
            return self.params.copy()

        params = self.params.copy()

        def _get(t, key, default=None):
            return t.get(key, default) if isinstance(t, dict) else getattr(t, key, default)

        wins = [t for t in trades if _get(t, "result") == "WIN"]
        losses = [t for t in trades if _get(t, "result") == "LOSS"]
        win_rate = len(wins) / len(trades)

        step = float(learning_settings.get("step_size", 0.05))
        bounds = self.PARAM_BOUNDS

        def clamp(key: str, val: float) -> float:
            lo, hi = bounds.get(key, (val, val))
            return max(lo, min(hi, val))

        if win_rate < 0.50:
            params["zone_tolerance_pct"] = clamp(
                "zone_tolerance_pct",
                params.get("zone_tolerance_pct", 0.003) * (1 - step),
            )
            params["min_confidence_threshold"] = clamp(
                "min_confidence_threshold",
                params.get("min_confidence_threshold", 0.45) + step * 0.1,
            )

        if win_rate > 0.65:
            gross_profit = sum((_get(t, "pnl") or 0) for t in wins)
            gross_loss = abs(sum((_get(t, "pnl") or 0) for t in losses)) or 0.001
            pf = gross_profit / gross_loss
            if pf < 1.5:
                params["min_rr_ratio"] = clamp(
                    "min_rr_ratio",
                    params.get("min_rr_ratio", 1.2) + step * 0.2,
                )
            elif pf > 2.0:
                params["min_confidence_threshold"] = clamp(
                    "min_confidence_threshold",
                    params.get("min_confidence_threshold", 0.45) - step * 0.05,
                )

        if losses:
            short_losses = [t for t in losses if (_get(t, "duration_mins") or 999) < 30]
            if len(short_losses) / len(losses) > 0.30:
                params["crt_sweep_min_pips"] = clamp(
                    "crt_sweep_min_pips",
                    params.get("crt_sweep_min_pips", 3) + step * 2,
                )

        sl_hit_count = len(losses)
        if sl_hit_count > 0.40 * len(trades):
            params["atr_sl_buffer"] = clamp(
                "atr_sl_buffer",
                params.get("atr_sl_buffer", 0.5) * (1 + step * 0.5),
            )

        return params