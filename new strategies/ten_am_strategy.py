"""
app/strategy/ten_am_strategy.py — 10 AM Strategy (Opening Range Breakout)

Classic "10 o'clock rule" / opening-range-breakout strategy:

  1. The first N minutes of the session (default: the 09:00-10:00 hour on
     the bar's own timestamp) define an "opening range" (high/low).
  2. Once the clock passes the range window, the FIRST candle that closes
     outside the range (with a small ATR buffer, to avoid noise) triggers
     an entry in the breakout direction.
  3. The range must be a meaningful size relative to ATR — a too-tight range
     is a low-information / choppy session and is skipped.

NOTE on timestamps: this only works on intraday timeframes (e.g. 15m/1h)
where `date` strings on each OHLCV bar carry a real time-of-day component in
the exchange/broker's server time. On daily bars there is no intraday
opening range and the strategy will simply never fire (returns None, 0.0).
`range_start_hour` / `range_end_hour` are left un-optimized (not in
PARAM_BOUNDS) since they are a structural/session choice, not a curve-fit
parameter — adjust the DEFAULT_PARAMS values directly if your data uses a
different session convention (e.g. London 08:00-09:00 instead of NY
09:00-10:00).
"""
from datetime import datetime as _dt

from .base import BaseStrategy

DEFAULT_PARAMS = {
    # Session / opening range definition (NOT auto-tuned — see note above)
    "range_start_hour": 9,
    "range_end_hour": 10,
    # Signal quality filters (auto-tuned)
    "atr_length": 14,
    "min_range_atr_ratio": 0.3,     # opening range must be >= this * ATR to be tradeable
    "breakout_confirm_atr": 0.10,   # buffer beyond the range edge required to count as a breakout
    # Risk / targets
    "stop_loss_pct": 0.4,
    "tp1_multiplier": 1.0,
    "tp2_multiplier": 2.0,
    "tp3_multiplier": 3.0,
    "tp4_multiplier": 4.0,
    "lot_size": 0.01,
    "min_stop_loss_pct": 0.1,
    "max_stop_loss_pct": 2.0,
    "min_tp_multiplier": 0.5,
    "max_tp_multiplier": 8.0,
}


def _parse_dt(date_str: str):
    try:
        return _dt.fromisoformat(date_str[:19])
    except Exception:
        return None


def _true_range(bars: list, i: int) -> float:
    high = bars[i]["high"]
    low = bars[i]["low"]
    prev_close = bars[i - 1]["close"] if i > 0 else bars[i]["close"]
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _atr_last(bars: list, period: int) -> float | None:
    n = len(bars)
    if n < period + 1:
        return None
    trs = [_true_range(bars, i) for i in range(n)]
    atr = sum(trs[1:period + 1]) / period
    for tr in trs[period + 1:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


class TenAMStrategy(BaseStrategy):
    name = "Ten_AM"
    display_name = "10 AM Strategy (Opening Range Breakout)"
    description = (
        "Opening range breakout strategy: builds a high/low range from the "
        "09:00-10:00 session hour, then trades the first breakout of that "
        "range once it closes with enough size relative to ATR."
    )

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "atr_length": (7, 30),
        "min_range_atr_ratio": (0.05, 2.0),
        "breakout_confirm_atr": (0.0, 1.0),
        "stop_loss_pct": (0.1, 2.0),
        "tp1_multiplier": (0.5, 8.0),
        "tp2_multiplier": (0.5, 8.0),
        "tp3_multiplier": (0.5, 8.0),
        "tp4_multiplier": (0.5, 8.0),
    }

    @classmethod
    def default_params(cls) -> dict:
        return DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        ohlcv = market_data.get("ohlcv_window")
        if not ohlcv or len(ohlcv) < 30:
            return None, 0.0

        cur = ohlcv[-1]
        cur_dt = _parse_dt(cur.get("date", ""))
        if cur_dt is None:
            return None, 0.0  # no usable time-of-day info (e.g. daily bars)

        start_h = float(self.params.get("range_start_hour", 9))
        end_h = float(self.params.get("range_end_hour", 10))
        cur_hour = cur_dt.hour + cur_dt.minute / 60.0
        cur_date = cur_dt.date()

        if cur_hour < end_h:
            return None, 0.0  # still inside (or before) the opening-range window

        range_bars = []
        for b in ohlcv:
            bdt = _parse_dt(b.get("date", ""))
            if bdt is None:
                continue
            if bdt.date() == cur_date and start_h <= (bdt.hour + bdt.minute / 60.0) < end_h:
                range_bars.append(b)

        if not range_bars:
            return None, 0.0

        range_high = max(b["high"] for b in range_bars)
        range_low = min(b["low"] for b in range_bars)
        range_width = range_high - range_low
        if range_width <= 0:
            return None, 0.0

        atr_len = int(self.params.get("atr_length", 14))
        atr = _atr_last(ohlcv, atr_len)
        if not atr or atr <= 0:
            return None, 0.0

        min_ratio = float(self.params.get("min_range_atr_ratio", 0.3))
        if range_width < atr * min_ratio:
            return None, 0.0  # opening range too tight/noisy to trust

        buffer = atr * float(self.params.get("breakout_confirm_atr", 0.10))

        # Only trade the FIRST breakout of the day — if an earlier bar today
        # (after the range closed) already broke out, skip this one.
        already_broken = False
        for b in ohlcv[:-1]:
            bdt = _parse_dt(b.get("date", ""))
            if bdt is None or bdt.date() != cur_date:
                continue
            if (bdt.hour + bdt.minute / 60.0) < end_h:
                continue
            if b["close"] > range_high + buffer or b["close"] < range_low - buffer:
                already_broken = True
                break
        if already_broken:
            return None, 0.0

        close = cur["close"]
        if close > range_high + buffer:
            return "BUY", 1.0
        if close < range_low - buffer:
            return "SELL", 1.0
        return None, 0.0

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        params = self._sort_tp_multipliers(params)
        sl_pct = float(params.get("stop_loss_pct", DEFAULT_PARAMS["stop_loss_pct"]))
        sl_dist = price * (sl_pct / 100.0)
        sign = 1 if direction == "BUY" else -1
        return {
            "sl": round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.0)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.0)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.0)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 4.0)), 5),
        }
