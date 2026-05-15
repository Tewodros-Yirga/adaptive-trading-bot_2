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
    # Public interface
    # -------------------------------------------------------------------------

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        """
        Returns (direction, confidence) where direction is "BUY", "SELL", or None.

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
        # Store for helper access so we don't thread market_data through every call
        self._market_data = market_data
        self._current_zone: dict | None = None
        self._confidence_components: dict = {}

        htf_bias = self._htf_bias(market_data)
        if htf_bias == "NEUTRAL":
            return None, 0.0

        if self.params.get("killzone_filter_enabled", True):
            ts = market_data.get("timestamp")
            if ts is None or not self._in_killzone(ts):
                return None, 0.0

        if htf_bias == "BULLISH":
            if self._is_buy_signal(market_data):
                confidence = self._compute_confidence(htf_bias, "BUY", market_data)
                if confidence >= self.params.get("min_confidence_threshold", 0.55):
                    return "BUY", confidence

        elif htf_bias == "BEARISH":
            if self._is_sell_signal(market_data):
                confidence = self._compute_confidence(htf_bias, "SELL", market_data)
                if confidence >= self.params.get("min_confidence_threshold", 0.55):
                    return "SELL", confidence

        return None, 0.0

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        """
        Returns {"sl": float, "tp1": float, "tp2": float, "tp3": float, "tp4": float}
        or {} if minimum RR is not met.
        """
        if not direction:
            return {}

        md = getattr(self, "_market_data", {})
        atr = md.get("atr", price * 0.005)
        # Prefer 1h bars; fall back to 4h, then 1d for historical backtests
        bars_1h: pd.DataFrame = md.get("1h_bars", pd.DataFrame())
        if bars_1h.empty or len(bars_1h) < 3:
            bars_1h = md.get("4h_bars", pd.DataFrame())
        if bars_1h.empty or len(bars_1h) < 3:
            bars_1h = md.get("1d_bars", pd.DataFrame())

        atr_buffer = params.get("atr_sl_buffer", 0.5)
        min_rr = params.get("min_rr_ratio", 1.2)

        # Use relaxed RR and smaller ATR buffer for daily-resolution data
        is_daily_proxy = bars_1h is md.get("1d_bars", pd.DataFrame())
        if is_daily_proxy:
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
        tp1_dist = abs(tp1 - price)
        if sl_dist == 0 or (tp1_dist / sl_dist) < min_rr:
            return {}  # RR guard: reject trade

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
            and self._liquidity_swept(market_data, "low")
            and self._check_storyline(market_data)
            and (not self.params.get("smt_filter_enabled") or self._smt_bullish(market_data))
        )

    def _is_sell_signal(self, market_data: dict) -> bool:
        return (
            self._at_supply_zone(market_data)
            and self._crt_bearish_sweep_confirmed(market_data)
            and self._structure_shift_to_bearish(market_data)
            and self._liquidity_swept(market_data, "high")
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

        # Fallback: use 1d_bars with shorter periods suitable for daily resolution
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

        # OCL: open/close of last N daily candles
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

        # OB: order blocks — last opposing candle before a large displacement on 4H
        ob_lookback = self.params.get("ob_lookback_candles", 20)
        if not bars_4h.empty and len(bars_4h) >= ob_lookback:
            window = bars_4h.iloc[-ob_lookback:]
            for i in range(1, len(window) - 1):
                candle = window.iloc[i]
                next_c = window.iloc[i + 1]
                candle_body = abs(float(candle["close"]) - float(candle["open"]))
                next_body = abs(float(next_c["close"]) - float(next_c["open"]))

                # Bullish OB: bearish candle before large bullish displacement
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

                # Bearish OB: bullish candle before large bearish displacement
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

        # QML: Quasimodo pattern on 1H
        qml_lookback = self.params.get("qml_swing_lookback", 10)
        if not bars_1h.empty and len(bars_1h) >= qml_lookback:
            zones += self._find_qml_zones(bars_1h.iloc[-qml_lookback:], current_price)

        return zones

    def _find_qml_zones(self, bars: pd.DataFrame, current_price: float) -> list[dict]:
        zones: list[dict] = []
        highs = bars["high"].values.astype(float)
        lows = bars["low"].values.astype(float)

        for i in range(2, len(bars) - 2):
            # Bearish QML: HH → HL → HH2 then breaks HL
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

            # Bullish QML: LL → LH → LL2 then breaks LH
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
        tol = self.params.get("zone_tolerance_pct", 0.0015)
        price = market_data.get("current_price", 0.0)
        for z in zones:
            if z["direction"] == "SUPPORT" and abs(price - z["level"]) / max(price, 1e-9) <= tol:
                self._current_zone = z
                return True
        return False

    def _at_supply_zone(self, market_data: dict) -> bool:
        zones = self._find_msnr_zones(market_data)
        tol = self.params.get("zone_tolerance_pct", 0.0015)
        price = market_data.get("current_price", 0.0)
        for z in zones:
            if z["direction"] == "RESISTANCE" and abs(price - z["level"]) / max(price, 1e-9) <= tol:
                self._current_zone = z
                return True
        return False

    # -------------------------------------------------------------------------
    # CRT Confirmation
    # -------------------------------------------------------------------------

    def _crt_bullish_sweep_confirmed(self, market_data: dict) -> bool:
        # Prefer 1h bars; fall back to 4h, then 1d for historical backtests where 1h is unavailable
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

        # For daily bars, use a larger sweep threshold (price-relative)
        is_daily = bars is market_data.get("1d_bars", pd.DataFrame())
        if is_daily:
            min_sweep = max(
                self.params.get("crt_sweep_min_pips", 3) * 0.1,
                float(range_candle["low"]) * 0.001,  # 0.1% of price for daily
            )
        else:
            # For gold (XAUUSD), use price-relative sweep threshold instead of fixed pips
            min_sweep = max(
                self.params.get("crt_sweep_min_pips", 3) * 0.0001,
                float(range_candle["low"]) * 0.0002,  # 0.02% of price
            )

        swept = float(manip_candle["low"]) < float(range_candle["low"]) - min_sweep
        closed_back = float(manip_candle["close"]) > float(range_candle["low"])
        dist_bullish = float(dist_candle["close"]) > float(dist_candle["open"])

        if self.params.get("crt_close_back_required", False):
            return swept and closed_back and dist_bullish
        return swept and dist_bullish

    def _crt_bearish_sweep_confirmed(self, market_data: dict) -> bool:
        # Prefer 1h bars; fall back to 4h, then 1d for historical backtests where 1h is unavailable
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

        is_daily = bars is market_data.get("1d_bars", pd.DataFrame())
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
        # Prefer 15m; fall back through 1h → 4h → 1d when finer data unavailable
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
        # Prefer 15m; fall back through 1h → 4h → 1d when finer data unavailable
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
        # Prefer 15m; fall back through 1h → 4h → 1d when finer data unavailable
        bars: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        if bars.empty or len(bars) < 6:
            bars = market_data.get("1h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 6:
            bars = market_data.get("4h_bars", pd.DataFrame())
        if bars.empty or len(bars) < 6:
            bars = market_data.get("1d_bars", pd.DataFrame())
        if len(bars) < 6:
            return False

        # Use wider window for daily bars (more meaningful swing detection)
        is_daily = bars is market_data.get("1d_bars", pd.DataFrame())
        lookback = 10 if is_daily else 5
        if len(bars) < lookback + 1:
            lookback = len(bars) - 1

        window = bars.iloc[-(lookback + 1):-1]  # last N complete bars
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
        """
        Scores multiple confirmations. Requires a configurable minimum number
        to pass (default 2 out of available checks).
        Checks that depend on unavailable timeframes are skipped rather than
        counted as failures — this lets the strategy work in daily backtests
        where 1h/15m data is beyond yfinance's history window.
        """
        checks: dict[str, bool] = {}
        available: set[str] = set()  # only checks we could actually evaluate

        bars_1d: pd.DataFrame = market_data.get("1d_bars", pd.DataFrame())
        bars_1h: pd.DataFrame = market_data.get("1h_bars", pd.DataFrame())
        bars_15m: pd.DataFrame = market_data.get("15m_bars", pd.DataFrame())
        # Effective intraday bars after fallback (same logic as other methods)
        eff_1h = bars_1h if (not bars_1h.empty and len(bars_1h) >= 3) else market_data.get("4h_bars", pd.DataFrame())
        # For daily backtests: treat daily bars as 1h proxy when intraday unavailable
        if eff_1h.empty or len(eff_1h) < 3:
            eff_1h = bars_1d
        eff_15m = bars_15m if (not bars_15m.empty and len(bars_15m) >= 4) else eff_1h

        # 1. Price on correct side of weekly OCL
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

        # 2. CRT manipulation confirmed (uses effective intraday bars with fallback)
        htf = self._htf_bias(market_data)
        if not eff_1h.empty and len(eff_1h) >= 3:
            available.add("crt_confirmed")
            checks["crt_confirmed"] = (
                self._crt_bullish_sweep_confirmed(market_data)
                or self._crt_bearish_sweep_confirmed(market_data)
            )
        # else: skip — unavailable, don't count as failure

        # 3. Fresh OB or QML zone in direction of bias
        # Only add to available if we have daily bars (OCL zones come from daily data)
        if not bars_1d.empty:
            available.add("fresh_zone")
            zones = self._find_msnr_zones(market_data)
            target_direction = "SUPPORT" if htf == "BULLISH" else "RESISTANCE"
            # Use wider tolerance for daily-resolution backtests
            tol = self.params.get("zone_tolerance_pct", 0.003)
            price = market_data.get("current_price", 0.0)
            fresh_zones = [
                z for z in zones
                if z["direction"] == target_direction
                and z.get("freshness", 99) <= 5
                and abs(price - z["level"]) / max(price, 1e-9) <= tol
            ]
            checks["fresh_zone"] = len(fresh_zones) > 0
        # else: skip — no daily data

        # 4. Structure aligned (intraday or daily fallback)
        if not eff_1h.empty and len(eff_1h) >= 10:
            available.add("structure_aligned")
            price = market_data.get("current_price", 0.0)
            if htf == "BULLISH":
                intervening_lows = eff_1h["low"].iloc[-10:][eff_1h["low"].iloc[-10:] < price]
                checks["structure_aligned"] = len(intervening_lows) == 0
            else:
                intervening_highs = eff_1h["high"].iloc[-10:][eff_1h["high"].iloc[-10:] > price]
                checks["structure_aligned"] = len(intervening_highs) == 0
        # else: skip — unavailable

        # 5. Entry pattern: structure shift (uses effective bars with fallback)
        if not eff_15m.empty and len(eff_15m) >= 4:
            available.add("entry_pattern")
            checks["entry_pattern"] = (
                self._structure_shift_to_bullish(market_data)
                or self._structure_shift_to_bearish(market_data)
            )
        # else: skip — unavailable

        self._confidence_components["storyline_checks"] = checks

        # Require min_storyline_checks out of the checks we could actually run
        n_available = len(available)
        if n_available == 0:
            return False
        passed = sum(checks.get(k, False) for k in available)
        min_required = min(
            self.params.get("min_storyline_checks", 2),
            n_available,          # can't require more than we have
        )
        return passed >= min_required

    # -------------------------------------------------------------------------
    # Confidence scoring
    # -------------------------------------------------------------------------

    def _compute_confidence(self, htf_bias: str, direction: str, market_data: dict) -> float:
        score = 0.0
        score += 0.20  # HTF alignment already confirmed
        score += 0.15  # In killzone already confirmed

        if direction == "BUY":
            crt_confirmed = self._crt_bullish_sweep_confirmed(market_data)
        else:
            crt_confirmed = self._crt_bearish_sweep_confirmed(market_data)
        if crt_confirmed:
            score += 0.20

        zone = self._current_zone
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
                tol = self.params.get("zone_tolerance_pct", 0.0015)
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
        return primary_ll and not corr_ll  # primary made new LL but correlated didn't

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

        # Tighten entry quality if win rate is low
        if win_rate < 0.50:
            params["zone_tolerance_pct"] = clamp(
                "zone_tolerance_pct",
                params.get("zone_tolerance_pct", 0.003) * (1 - step),
            )
            params["min_confidence_threshold"] = clamp(
                "min_confidence_threshold",
                params.get("min_confidence_threshold", 0.45) + step * 0.1,
            )

        # Widen targets if win rate is good but PF is low
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

        # Tighten sweep requirement if many losses had tiny sweeps (short-duration proxy)
        if losses:
            short_losses = [t for t in losses if (_get(t, "duration_mins") or 999) < 30]
            if len(short_losses) / len(losses) > 0.30:
                params["crt_sweep_min_pips"] = clamp(
                    "crt_sweep_min_pips",
                    params.get("crt_sweep_min_pips", 3) + step * 2,
                )

        # Widen SL buffer if many SL hits
        sl_hit_count = len(losses)
        if sl_hit_count > 0.40 * len(trades):
            params["atr_sl_buffer"] = clamp(
                "atr_sl_buffer",
                params.get("atr_sl_buffer", 0.5) * (1 + step * 0.5),
            )

        return params