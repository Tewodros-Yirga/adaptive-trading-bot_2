"""
Market context + strategy suitability (item 23).

Computes a lightweight market-context vector for the current bar:
  - session         : forex session derived from UTC time (no market data needed)
  - trend_regime    : TREND / RANGE / MIXED / UNKNOWN  (from recent closes if available)
  - volatility_regime: HIGH / NORMAL / LOW / UNKNOWN    (from ATR / price if available)

`strategy_context_fit()` returns a 0..1 score for how well a given strategy
suits the current context. This is wired into the picker as an OPT-IN 8th factor
(`context_fit`) whose default weight is 0.0 — so it has ZERO effect on live
selection until an operator raises its weight. Unknown context or unknown
strategy returns the neutral 0.5.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)

NEUTRAL = 0.5

# Strategy families and what they prefer. regime_pref / vol_pref values are the
# fit score (0..1) used when the context matches that bucket; missing buckets
# fall back to NEUTRAL. session_boost is added (then clamped) for preferred
# sessions. Strategy names match the registry.
_TREND_FOLLOWERS = {
    "DTC", "Multi_EMA_Scalper", "HTF_Structure", "ADX_Regime_Filter",
    "MACD_Momentum", "OBV_Momentum",
}
_MEAN_REVERTERS = {"RSI_Reversal", "StochRSI_Cross", "VWAP_Reversion"}
_BREAKOUT = {"Bollinger_Breakout"}
# Alchemist is session/killzone-sensitive (ICT concepts).
_SESSION_SENSITIVE = {"Alchemist"}

# Sessions considered "active"/high-quality for liquidity-driven strategies.
_ACTIVE_SESSIONS = {"LONDON", "OVERLAP", "NEWYORK"}


def current_session(now: datetime.datetime | None = None) -> str:
    """Approximate forex session from UTC hour (server-side, no data needed)."""
    h = (now or datetime.datetime.now(datetime.timezone.utc)).hour
    if 0 <= h < 7:
        return "ASIAN"
    if 7 <= h < 12:
        return "LONDON"
    if 12 <= h < 16:
        return "OVERLAP"      # London + New York overlap (highest liquidity)
    if 16 <= h < 21:
        return "NEWYORK"
    return "OFF"


def _closes_from_market_data(market_data: dict[str, Any]) -> list[float]:
    """Best-effort extraction of recent close prices from the market_data dict."""
    if not market_data:
        return []
    ohlcv = market_data.get("ohlcv")
    if isinstance(ohlcv, list) and ohlcv:
        out: list[float] = []
        for bar in ohlcv:
            try:
                if isinstance(bar, dict):
                    out.append(float(bar.get("close")))
            except (TypeError, ValueError):
                continue
        if out:
            return out
    # Fallback to a raw DataFrame if present (avoid hard pandas dependency).
    df = market_data.get("_df")
    try:
        if df is not None and hasattr(df, "__contains__") and "close" in df:
            return [float(x) for x in list(df["close"])[-60:]]
    except Exception:
        pass
    return []


def compute_market_context(market_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the market-context vector. Always returns a dict; regime/vol may be
    UNKNOWN when no usable market data is supplied."""
    ctx: dict[str, Any] = {
        "session": current_session(),
        "trend_regime": "UNKNOWN",
        "volatility_regime": "UNKNOWN",
    }
    md = market_data or {}

    # ── Volatility regime from ATR / price ────────────────────────────────
    try:
        atr = float(md.get("atr") or 0.0)
        price = float(md.get("price") or 0.0)
        if atr > 0 and price > 0:
            ratio = atr / price
            if ratio >= 0.010:
                ctx["volatility_regime"] = "HIGH"
            elif ratio <= 0.003:
                ctx["volatility_regime"] = "LOW"
            else:
                ctx["volatility_regime"] = "NORMAL"
    except (TypeError, ValueError):
        pass

    # ── Trend regime from a cheap fast/slow SMA separation on recent closes ─
    closes = _closes_from_market_data(md)
    if len(closes) >= 30:
        fast = sum(closes[-10:]) / 10.0
        slow = sum(closes[-30:]) / 30.0
        ref = abs(slow) or 1.0
        sep = abs(fast - slow) / ref
        if sep >= 0.002:
            ctx["trend_regime"] = "TREND"
        elif sep <= 0.0007:
            ctx["trend_regime"] = "RANGE"
        else:
            ctx["trend_regime"] = "MIXED"
    return ctx


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def strategy_context_fit(strategy_name: str, context: dict[str, Any] | None) -> float:
    """Return a 0..1 suitability score for `strategy_name` in the given context.

    Neutral (0.5) when context is unknown/missing so an enabled context_fit
    factor never penalises a strategy without evidence.
    """
    if not context:
        return NEUTRAL

    trend = context.get("trend_regime", "UNKNOWN")
    vol = context.get("volatility_regime", "UNKNOWN")
    session = context.get("session", "UNKNOWN")

    score = NEUTRAL

    # ── Regime fit ─────────────────────────────────────────────────────────
    if strategy_name in _TREND_FOLLOWERS:
        if trend == "TREND":
            score += 0.30
        elif trend == "RANGE":
            score -= 0.25
    elif strategy_name in _MEAN_REVERTERS:
        if trend == "RANGE":
            score += 0.30
        elif trend == "TREND":
            score -= 0.25
    elif strategy_name in _BREAKOUT:
        if trend == "TREND" or vol == "HIGH":
            score += 0.25
        elif vol == "LOW":
            score -= 0.20

    # ── Volatility fit ─────────────────────────────────────────────────────
    if strategy_name in _MEAN_REVERTERS and vol == "HIGH":
        score -= 0.10        # mean-reversion gets chopped up in high vol
    if strategy_name in _TREND_FOLLOWERS and vol == "LOW":
        score -= 0.10        # trend-following stalls in dead markets

    # ── Session fit (liquidity-driven strategies prefer active sessions) ────
    if strategy_name in _SESSION_SENSITIVE:
        score += 0.20 if session in _ACTIVE_SESSIONS else -0.20
    elif strategy_name in _MEAN_REVERTERS and session == "ASIAN":
        score += 0.10        # ranges are common in the Asian session

    return round(_clamp(score), 6)
