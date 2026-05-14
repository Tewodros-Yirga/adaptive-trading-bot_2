"""
Backtesting Engine
Uses the shared ohlcv.py module for data fetching (yfinance → Alpha Vantage → Twelve Data → OANDA).
Runs in ProcessPoolExecutor to avoid blocking the event loop.

Phase 2 extensions:
  - Per-trade detail log (trade_log_json)
  - Monthly breakdown (monthly_breakdown_json)
  - Parameter evolution log (parameter_evolution_log_json)
  - Drawdown periods (drawdown_periods_json)
  - Batch orchestration with cross-analysis and pair analysis

Phase 4 additions:
  - MTF (multi-timeframe) simulation support for strategies with requires_mtf=True.
    At each bar in the simulation loop, a rolling window of bars for each timeframe
    is assembled via build_mtf_market_data().
  - Alchemist (and any future requires_mtf strategy) receives the full MTF dict.
"""
import asyncio
import json
import logging
import math
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from itertools import combinations

import httpx
from sqlalchemy.orm import Session

from .. import crud
from ..models import BacktestResult, OHLCVCache, StrategyPairAnalysis
from ..strategy.registry import get_strategy

logger = logging.getLogger(__name__)

_EXECUTOR = ProcessPoolExecutor(max_workers=2)


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _compute_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            delta = closes[i] - closes[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(delta, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-delta, 0)) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = round(100 - 100 / (1 + rs), 2)
    return rsi


def _ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result[period - 1] = ema
    for i in range(period, len(values)):
        ema = values[i] * k + ema * (1 - k)
        result[i] = ema
    return result


def _compute_atr(ohlcv: list[dict], period: int = 14) -> list[float | None]:
    atr: list[float | None] = [None] * len(ohlcv)
    if len(ohlcv) < period + 1:
        return atr
    trs = []
    for i in range(1, len(ohlcv)):
        h = ohlcv[i]["high"]
        l = ohlcv[i]["low"]
        pc = ohlcv[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr_val = sum(trs[:period]) / period
    atr[period] = atr_val
    for i in range(period + 1, len(ohlcv)):
        atr_val = (atr_val * (period - 1) + trs[i - 1]) / period
        atr[i] = atr_val
    return atr


def _session_name(date_str: str) -> str:
    try:
        hour = int(date_str[11:13]) if len(date_str) > 13 else 0
    except (ValueError, IndexError):
        return "Unknown"
    if 7 <= hour < 16:
        return "London"
    if 13 <= hour < 22:
        return "New York"
    if 0 <= hour < 8:
        return "Tokyo"
    return "Sydney"


def _day_of_week(date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(date_str[:10])
        return dt.strftime("%A")
    except ValueError:
        return "Unknown"


# ---------------------------------------------------------------------------
# MTF helpers for simulation
# ---------------------------------------------------------------------------

def _build_mtf_bars_for_index(
    i: int,
    primary_ohlcv: list[dict],
    extra_ohlcv_by_tf: dict[str, list[dict]],
) -> dict:
    """
    Build per-timeframe DataFrames sliced up to bar index ``i`` in the
    primary timeframe (1h by default). Used during backtesting for MTF strategies.

    Returns dict: {"1d": DataFrame, "4h": DataFrame, "1h": DataFrame, "15m": DataFrame}

    The primary OHLCV list is treated as the 1h frame. Higher/lower timeframes
    are supplied via extra_ohlcv_by_tf and sliced by date up to the current bar's date.
    """
    import pandas as pd

    current_date_str = primary_ohlcv[i].get("date", "")

    def _list_to_df(ohlcv_list: list[dict], cutoff_date: str) -> pd.DataFrame:
        filtered = [r for r in ohlcv_list if r.get("date", "") <= cutoff_date]
        if not filtered:
            return pd.DataFrame()
        df = pd.DataFrame(filtered)
        df["datetime"] = pd.to_datetime(df["date"])
        df = df.set_index("datetime")
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close"])

    primary_df = _list_to_df(primary_ohlcv[: i + 1], current_date_str)

    # Build the bars_by_tf dict from the extra timeframes.
    # Only fall back to primary (daily) as "1h" if no real 1h data was provided,
    # to avoid feeding daily bars to 1h-based strategy checks (e.g. Alchemist CRT).
    bars_by_tf: dict[str, "pd.DataFrame"] = {}
    for tf, ohlcv_list in extra_ohlcv_by_tf.items():
        bars_by_tf[tf] = _list_to_df(ohlcv_list, current_date_str)
    if "1h" not in bars_by_tf:
        bars_by_tf["1h"] = primary_df

    return bars_by_tf


# ---------------------------------------------------------------------------
# Core backtest function (subprocess-safe pure function)
# ---------------------------------------------------------------------------

def _run_backtest_sync(
    strategy_name: str,
    symbol: str,
    from_date: str,
    to_date: str,
    params: dict,
    initial_balance: float,
    leverage: int,
    risk_per_trade_pct: float,
    ohlcv: list[dict],
    adapt_every_n_trades: int = 20,
    extra_ohlcv_by_tf: dict | None = None,
) -> dict:
    """
    Pure function — runs in subprocess. Returns metrics dict with extended report fields.

    ``extra_ohlcv_by_tf`` is used for MTF strategies (e.g. Alchemist):
      {"1d": [...], "4h": [...], "15m": [...]}
    """
    if not ohlcv or len(ohlcv) < 30:
        return {"error": "Insufficient OHLCV data"}

    strat = get_strategy(strategy_name, params)
    is_mtf_strategy = getattr(strat, "requires_mtf", False)
    closes = [r["close"] for r in ohlcv]
    highs  = [r["high"]  for r in ohlcv]
    lows   = [r["low"]   for r in ohlcv]
    volumes = [r.get("volume", 0) for r in ohlcv]

    # ── Pre-compute ALL indicators for ALL strategies ──────────────────────
    rsi_series = _compute_rsi(closes, int(params.get("rsi_period", 14)))
    atr_series = _compute_atr(ohlcv, 14)

    # EMA ribbons (used by DTC, Multi_EMA_Scalper)
    ema_periods = [
        int(params.get("ema_1", 30)), int(params.get("ema_2", 35)),
        int(params.get("ema_3", 40)), int(params.get("ema_4", 45)),
        int(params.get("ema_5", 50)), int(params.get("ema_6", 60)),
    ]
    ema_series_list = [_ema_series(closes, p) for p in ema_periods]

    # MACD (used by MACD_Momentum)
    fast_p  = int(params.get("fast_period",  12))
    slow_p  = int(params.get("slow_period",  26))
    sig_p   = int(params.get("signal_period", 9))
    ema_fast = _ema_series(closes, fast_p)
    ema_slow = _ema_series(closes, slow_p)
    macd_line_series: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    _macd_valid = [v for v in macd_line_series if v is not None]
    macd_signal_series: list[float | None] = [None] * len(macd_line_series)
    if len(_macd_valid) >= sig_p:
        # compute EMA of the macd_line where available
        _filled = [v if v is not None else 0.0 for v in macd_line_series]
        _sig = _ema_series(_filled, sig_p)
        macd_signal_series = [s if m is not None else None for m, s in zip(macd_line_series, _sig)]
    macd_histogram_series: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line_series, macd_signal_series)
    ]

    # Bollinger Bands (used by Bollinger_Breakout)
    bb_period = int(params.get("bb_period", 20))
    bb_std    = float(params.get("bb_std", 2.0))
    bb_upper_series: list[float | None] = [None] * len(closes)
    bb_lower_series: list[float | None] = [None] * len(closes)
    for _bi in range(bb_period - 1, len(closes)):
        _window = closes[_bi - bb_period + 1: _bi + 1]
        _mean = sum(_window) / bb_period
        _std  = math.sqrt(sum((c - _mean) ** 2 for c in _window) / bb_period)
        bb_upper_series[_bi] = _mean + bb_std * _std
        bb_lower_series[_bi] = _mean - bb_std * _std

    # VWAP (rolling daily-reset approximation using cumulative price*vol / vol)
    vwap_series: list[float | None] = [None] * len(closes)
    _cum_pv = 0.0
    _cum_v  = 0.0
    _prev_date = ""
    for _vi, bar in enumerate(ohlcv):
        _d = bar.get("date", "")[:10]
        if _d != _prev_date:          # new day — reset
            _cum_pv = 0.0
            _cum_v  = 0.0
            _prev_date = _d
        _tp = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        _v  = volumes[_vi] or 1.0
        _cum_pv += _tp * _v
        _cum_v  += _v
        vwap_series[_vi] = _cum_pv / _cum_v

    balance = initial_balance
    equity_curve = [{"date": ohlcv[0]["date"], "equity": balance}]
    trades: list[dict] = []
    trade_log: list[dict] = []
    open_trade: dict | None = None
    adaptation_events: list[dict] = []
    current_params = dict(params)

    drawdown_periods: list[dict] = []
    in_drawdown = False
    dd_start_date: str | None = None
    dd_start_equity = balance
    peak_equity = balance
    trade_index = 0

    start_idx = max(ema_periods[5], slow_p + sig_p, bb_period, 30)

    for i in range(start_idx, len(ohlcv)):
        bar = ohlcv[i]
        price = bar["close"]
        bar_date = bar.get("date", str(i))

        if open_trade:
            exit_reason = None
            exit_price = None
            if open_trade["direction"] == "BUY":
                if bar["low"] <= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                    exit_reason = "SL_HIT"
                    pnl = (exit_price - open_trade["entry"]) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "LOSS", "pnl": round(pnl, 4), "exit": exit_price})
                    open_trade["result"] = "LOSS"
                    open_trade["exit_price"] = exit_price
                    open_trade["pnl"] = round(pnl, 4)
                    open_trade["exit_reason"] = exit_reason
                    open_trade["closed_at"] = bar_date
                    trade_log.append(_build_trade_log_entry(trade_index, open_trade, symbol, current_params))
                    open_trade = None
                    trade_index += 1
                elif bar["high"] >= open_trade["tp"]:
                    exit_price = open_trade["tp"]
                    exit_reason = "TP1_HIT"
                    pnl = (exit_price - open_trade["entry"]) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "WIN", "pnl": round(pnl, 4), "exit": exit_price})
                    open_trade["result"] = "WIN"
                    open_trade["exit_price"] = exit_price
                    open_trade["pnl"] = round(pnl, 4)
                    open_trade["exit_reason"] = exit_reason
                    open_trade["closed_at"] = bar_date
                    trade_log.append(_build_trade_log_entry(trade_index, open_trade, symbol, current_params))
                    open_trade = None
                    trade_index += 1
            else:  # SELL
                if bar["high"] >= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                    exit_reason = "SL_HIT"
                    pnl = (open_trade["entry"] - exit_price) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "LOSS", "pnl": round(pnl, 4), "exit": exit_price})
                    open_trade["result"] = "LOSS"
                    open_trade["exit_price"] = exit_price
                    open_trade["pnl"] = round(pnl, 4)
                    open_trade["exit_reason"] = exit_reason
                    open_trade["closed_at"] = bar_date
                    trade_log.append(_build_trade_log_entry(trade_index, open_trade, symbol, current_params))
                    open_trade = None
                    trade_index += 1
                elif bar["low"] <= open_trade["tp"]:
                    exit_price = open_trade["tp"]
                    exit_reason = "TP1_HIT"
                    pnl = (open_trade["entry"] - exit_price) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "WIN", "pnl": round(pnl, 4), "exit": exit_price})
                    open_trade["result"] = "WIN"
                    open_trade["exit_price"] = exit_price
                    open_trade["pnl"] = round(pnl, 4)
                    open_trade["exit_reason"] = exit_reason
                    open_trade["closed_at"] = bar_date
                    trade_log.append(_build_trade_log_entry(trade_index, open_trade, symbol, current_params))
                    open_trade = None
                    trade_index += 1

        # Adaptive parameter update
        if adapt_every_n_trades > 0 and len(trades) > 0 and len(trades) % adapt_every_n_trades == 0:
            recent = trades[-adapt_every_n_trades:]
            adaptation_events = _maybe_adapt(
                strat, current_params, recent, initial_balance, len(trades), adaptation_events, bar_date
            )

        # Track drawdown
        peak_equity = max(peak_equity, balance)
        current_dd = peak_equity - balance
        if current_dd > 0 and not in_drawdown:
            in_drawdown = True
            dd_start_date = bar_date
            dd_start_equity = peak_equity
        elif current_dd == 0 and in_drawdown:
            in_drawdown = False
            drawdown_periods.append({
                "start_date": dd_start_date,
                "end_date": bar_date,
                "peak_equity": round(dd_start_equity, 2),
                "trough_equity": round(balance, 2),
                "drawdown_pct": round((dd_start_equity - balance) / dd_start_equity * 100, 2),
            })

        if open_trade is not None:
            equity_curve.append({"date": bar_date, "equity": round(balance, 2)})
            continue

        # ── Build market_data for this bar ───────────────────────────────
        if is_mtf_strategy and extra_ohlcv_by_tf:
            bars_by_tf = _build_mtf_bars_for_index(i, ohlcv, extra_ohlcv_by_tf)
            from .ohlcv import build_mtf_market_data
            try:
                bar_ts = datetime.fromisoformat(bar_date)
            except ValueError:
                bar_ts = datetime.utcnow()

            # For daily bars, bar_date is date-only (e.g. "2023-01-15") → midnight.
            # Midnight is outside all trading session killzones (London 07-10, NY 12-15).
            # Simulate a London-open entry time so session-aware strategies can fire.
            if len(bar_date) == 10 and bar_ts.hour == 0 and bar_ts.minute == 0:
                bar_ts = bar_ts.replace(hour=8, minute=30)

            atr_val = atr_series[i] or price * 0.005
            market_data_bar = build_mtf_market_data(
                symbol=symbol,
                current_idx=-1,
                bars_by_tf=bars_by_tf,
                atr=atr_val,
            )
            market_data_bar["timestamp"] = bar_ts
            market_data_bar["current_price"] = price

            raw_sig = strat.signal(market_data_bar)
            if isinstance(raw_sig, tuple):
                signal, _conf = raw_sig
            else:
                signal = raw_sig

            if signal:
                levels = strat.compute_levels(signal, price, current_params)
            else:
                levels = {}
        else:
            # ── Universal market_data_bar — all indicators for all strategies ──
            ema_vals = {f"ema_{j+1}": ema_series_list[j][i] for j in range(6)}
            prev_ema_vals = {f"ema_{j+1}": ema_series_list[j][i - 1] for j in range(6)}

            prev_bull = all(
                (prev_ema_vals.get(f"ema_{k}") or 0) > (prev_ema_vals.get(f"ema_{k+1}") or 0)
                for k in range(1, 6)
            )
            prev_bear = all(
                (prev_ema_vals.get(f"ema_{k}") or 0) < (prev_ema_vals.get(f"ema_{k+1}") or 0)
                for k in range(1, 6)
            )

            market_data_bar = {
                # Common
                "price":          price,
                "prev_price":     ohlcv[i - 1]["close"],
                "atr":            atr_series[i],
                # EMA ribbon (DTC, Multi_EMA_Scalper)
                "ema_values":     ema_vals,
                "previous_bull":  prev_bull,
                "previous_bear":  prev_bear,
                # RSI (DTC, RSI_Reversal)
                "rsi":            rsi_series[i],
                "prev_rsi":       rsi_series[i - 1],
                # MACD (MACD_Momentum)
                "macd_line":      macd_line_series[i],
                "macd_signal":    macd_signal_series[i],
                "macd_histogram": macd_histogram_series[i],
                "prev_macd_line":   macd_line_series[i - 1],
                "prev_macd_signal": macd_signal_series[i - 1],
                # Bollinger Bands (Bollinger_Breakout)
                "bb_upper":       bb_upper_series[i],
                "bb_lower":       bb_lower_series[i],
                "prev_bb_upper":  bb_upper_series[i - 1],
                "prev_bb_lower":  bb_lower_series[i - 1],
                # VWAP (VWAP_Reversion)
                "vwap":           vwap_series[i],
            }

            signal = strat.signal(market_data_bar)
            if signal:
                levels = strat.compute_levels(signal, price, current_params)
            else:
                levels = {}

        if signal and levels:
            risk_amount = balance * (risk_per_trade_pct / 100)
            sl_dist = abs(price - levels["sl"])
            lots = min(round(risk_amount / (sl_dist * 100000), 2), 10.0) if sl_dist > 0 else 0.01
            lots = max(0.01, lots)
            open_trade = {
                "entry": price,
                "sl": levels["sl"],
                "tp": levels["tp1"],
                "direction": signal,
                "lots": lots,
                "date": bar_date,
                "opened_at": bar_date,
                "atr_at_entry": atr_series[i],
                "params_snapshot": dict(current_params),
            }

        equity_curve.append({"date": bar_date, "equity": round(balance, 2)})

    # Close any remaining open trade at last bar price
    if open_trade:
        last_bar = ohlcv[-1]
        last_price = last_bar["close"]
        if open_trade["direction"] == "BUY":
            pnl = (last_price - open_trade["entry"]) * open_trade["lots"] * 100000
        else:
            pnl = (open_trade["entry"] - last_price) * open_trade["lots"] * 100000
        result = "WIN" if pnl > 0 else "LOSS"
        trades.append({**open_trade, "result": result, "pnl": round(pnl, 4), "exit": last_price})
        open_trade["result"] = result
        open_trade["exit_price"] = last_price
        open_trade["pnl"] = round(pnl, 4)
        open_trade["exit_reason"] = "EOD_CLOSE"
        open_trade["closed_at"] = last_bar.get("date", "")
        trade_log.append(_build_trade_log_entry(trade_index, open_trade, symbol, current_params))
        balance += pnl

    # ── Compute metrics ────────────────────────────────────────────────────────
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses)) or 1e-9
    profit_factor = gross_profit / gross_loss

    returns = [t["pnl"] / initial_balance for t in trades]
    avg_ret = sum(returns) / len(returns) if returns else 0
    std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0
    sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0

    downside = [r for r in returns if r < 0]
    std_down = math.sqrt(sum(r ** 2 for r in downside) / len(downside)) if downside else 0
    sortino = (avg_ret / std_down * math.sqrt(252)) if std_down > 0 else 0

    peak = initial_balance
    max_dd = 0.0
    cum = initial_balance
    for t in trades:
        cum += t["pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    max_dd_pct = (max_dd / initial_balance) * 100
    total_return_pct = ((balance - initial_balance) / initial_balance) * 100
    calmar = (total_return_pct / max_dd_pct) if max_dd_pct > 0 else 0

    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss_val = (gross_loss / len(losses)) if losses else 0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss_val

    max_consec_wins = max_consec_losses = 0
    cur_wins = cur_losses = 0
    for t in trades:
        if t["result"] == "WIN":
            cur_wins += 1; cur_losses = 0
        else:
            cur_losses += 1; cur_wins = 0
        max_consec_wins = max(max_consec_wins, cur_wins)
        max_consec_losses = max(max_consec_losses, cur_losses)

    monthly_breakdown = _compute_monthly_breakdown(trades)

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 3),
        "total_pnl": round(balance - initial_balance, 4),
        "total_return_pct": round(total_return_pct, 2),
        "roi_pct": round(total_return_pct, 2),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "calmar_ratio": round(calmar, 3),
        "expectancy": round(expectancy, 4),
        "avg_rr": round(
            sum(abs(t["tp"] - t["entry"]) / abs(t["entry"] - t["sl"])
                for t in trades if abs(t["entry"] - t["sl"]) > 0) / len(trades), 2
        ) if trades else 0,
        "consecutive_wins": max_consec_wins,
        "consecutive_losses": max_consec_losses,
        "final_balance": round(balance, 2),
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "monthly_breakdown": monthly_breakdown,
        "parameter_evolution_log": {"adaptation_events": adaptation_events},
        "drawdown_periods": drawdown_periods,
    }


def _build_trade_log_entry(
    trade_index: int,
    trade: dict,
    symbol: str,
    params: dict,
) -> dict:
    opened_at = trade.get("opened_at") or trade.get("date", "")
    closed_at = trade.get("closed_at", "")
    duration_minutes = None
    try:
        if opened_at and closed_at:
            opened_dt = datetime.fromisoformat(opened_at[:19].replace("T", " ").replace("Z", ""))
            closed_dt = datetime.fromisoformat(closed_at[:19].replace("T", " ").replace("Z", ""))
            duration_minutes = round((closed_dt - opened_dt).total_seconds() / 60, 1)
    except (ValueError, TypeError):
        pass

    return {
        "trade_index": trade_index + 1,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "symbol": symbol,
        "direction": trade.get("direction"),
        "entry_price": trade.get("entry"),
        "exit_price": trade.get("exit_price") or trade.get("exit"),
        "stop_loss": trade.get("sl"),
        "take_profit_1": trade.get("tp"),
        "lot_size": trade.get("lots", 0.01),
        "pnl": trade.get("pnl"),
        "result": trade.get("result"),
        "exit_reason": trade.get("exit_reason"),
        "duration_minutes": duration_minutes,
        "atr_at_entry": trade.get("atr_at_entry"),
        "params_version_at_open": None,
        "strategy_signals": [],
        "news_bias_at_open": None,
        "market_context": {
            "session": _session_name(opened_at),
            "day_of_week": _day_of_week(opened_at),
            "was_high_volatility": False,
        },
    }


def _compute_monthly_breakdown(trades: list[dict]) -> dict:
    breakdown: dict[str, dict] = {}
    for t in trades:
        date_str = t.get("date") or t.get("opened_at") or ""
        try:
            month_key = date_str[:7]
        except Exception:
            continue
        if not month_key or len(month_key) < 7:
            continue
        if month_key not in breakdown:
            breakdown[month_key] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("result") == "WIN":
            breakdown[month_key]["wins"] += 1
        else:
            breakdown[month_key]["losses"] += 1
        breakdown[month_key]["pnl"] = round(breakdown[month_key]["pnl"] + (t.get("pnl") or 0), 4)
    return breakdown


def _maybe_adapt(
    strat,
    current_params: dict,
    recent_trades: list[dict],
    initial_balance: float,
    trade_count: int,
    adaptation_events: list[dict],
    timestamp: str,
) -> list[dict]:
    if not hasattr(strat, "adapt"):
        return adaptation_events

    wins = [t for t in recent_trades if t.get("result") == "WIN"]
    losses = [t for t in recent_trades if t.get("result") == "LOSS"]
    win_rate = len(wins) / len(recent_trades) if recent_trades else 0
    gross_profit = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses)) or 1e-9
    profit_factor = gross_profit / gross_loss

    old_params = dict(current_params)
    composite_before = _compute_composite_score(win_rate * 100, profit_factor)

    try:
        new_params = strat.adapt(current_params, recent_trades)
        if new_params:
            deltas = {
                k: round(new_params[k] - old_params.get(k, 0), 6)
                for k in new_params
                if k in old_params and new_params[k] != old_params.get(k)
            }
            current_params.update(new_params)
            composite_after = _compute_composite_score(win_rate * 100, profit_factor)
            adaptation_events.append({
                "after_trade_index": trade_count,
                "timestamp": timestamp,
                "win_rate_at_time": round(win_rate, 4),
                "profit_factor_at_time": round(profit_factor, 4),
                "old_params": old_params,
                "new_params": dict(new_params),
                "param_deltas": deltas,
                "composite_score_before": round(composite_before, 4),
                "composite_score_after": round(composite_after, 4),
            })
    except Exception as e:
        logger.debug("Strategy adapt() raised: %s", e)

    return adaptation_events


def _compute_composite_score(win_rate_pct: float, profit_factor: float) -> float:
    wr_norm = min(win_rate_pct / 100.0, 1.0)
    pf_norm = min(profit_factor / 3.0, 1.0)
    return 0.6 * wr_norm + 0.4 * pf_norm


# ---------------------------------------------------------------------------
# Standalone sync function for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def run_backtest_sync_standalone(
    strategy_name: str,
    symbol: str,
    from_date: str,
    to_date: str,
    params: dict,
    initial_balance: float = 10000,
    leverage: int = 100,
    risk_per_trade_pct: float = 1.0,
    timeframe: str = "1d",
) -> dict:
    """
    Standalone sync function suitable for ProcessPoolExecutor.
    Fetches data via MT5 Bridge → yfinance → Alpha Vantage (all sync).
    Returns metrics dict; does NOT write to DB.
    """
    from .ohlcv import fetch_ohlcv_sync

    ohlcv: list[dict] = []

    try:
        ohlcv = fetch_ohlcv_sync(symbol, from_date, to_date, timeframe)
    except Exception as exc:
        logger.warning("run_backtest_sync_standalone fetch failed for %s: %s", symbol, exc)

    if not ohlcv:
        return {"error": f"No OHLCV data for {symbol} {from_date}–{to_date}"}

    # For MTF strategies (e.g. Alchemist), fetch sub-daily timeframes.
    # MT5 Bridge is tried first — it has full historical 1h/15m data.
    extra_ohlcv_by_tf: dict[str, list[dict]] = {}
    try:
        strat_check = get_strategy(strategy_name, params)
        if getattr(strat_check, "requires_mtf", False):
            for tf in ("1d", "4h", "1h", "15m"):
                try:
                    extra_ohlcv_by_tf[tf] = fetch_ohlcv_sync(symbol, from_date, to_date, tf)
                    logger.info("MTF standalone %s %s: %d bars", symbol, tf, len(extra_ohlcv_by_tf[tf]))
                except Exception as exc:
                    logger.warning("MTF standalone fetch failed %s %s: %s", symbol, tf, exc)
    except Exception as exc:
        logger.warning("MTF strategy check failed for %s: %s", strategy_name, exc)

    result = _run_backtest_sync(
        strategy_name, symbol, from_date, to_date,
        params, initial_balance, leverage, risk_per_trade_pct, ohlcv,
        extra_ohlcv_by_tf=extra_ohlcv_by_tf or None,
    )
    result.pop("equity_curve", None)
    result.pop("trade_log", None)
    result.pop("drawdown_periods", None)
    return result


# ---------------------------------------------------------------------------
# Single-run DB-backed backtest
# ---------------------------------------------------------------------------

def run_backtest(
    db: Session,
    strategy_name: str,
    symbol: str,
    from_date: str,
    to_date: str,
    params: dict,
    initial_balance: float = 10000,
    leverage: int = 100,
    risk_per_trade_pct: float = 1.0,
    batch_id: str | None = None,
) -> int:
    """
    Create a backtest record and run synchronously.
    Returns backtest ID.
    """
    from .ohlcv import fetch_ohlcv_sync

    adapt_every_n = int(crud.get_setting(db, "backtest_adapt_every_n_trades") or "20")
    av_key = crud.get_setting(db, "alphavantage_key") or ""

    # Try cache first
    cached = db.query(OHLCVCache).filter(
        OHLCVCache.symbol == symbol,
        OHLCVCache.from_date == from_date,
        OHLCVCache.to_date == to_date,
    ).first()

    ohlcv: list[dict] = []
    data_source = "cache"

    if cached:
        ohlcv = json.loads(cached.data_json)
    else:
        try:
            ohlcv = fetch_ohlcv_sync(symbol, from_date, to_date, "1d", av_key=av_key)
            data_source = "mt5_bridge_or_yfinance"
        except Exception as exc:
            logger.warning("Backtest fetch failed for %s: %s", symbol, exc)

        if ohlcv:
            cache_row = OHLCVCache(
                symbol=symbol,
                interval="1d",
                from_date=from_date,
                to_date=to_date,
                data_json=json.dumps(ohlcv),
                source=data_source,
            )
            db.add(cache_row)
            db.commit()

    # For MTF strategies, fetch all extra timeframes via MT5 Bridge first.
    extra_ohlcv_by_tf: dict[str, list[dict]] = {}
    strat_check = get_strategy(strategy_name, params)
    if getattr(strat_check, "requires_mtf", False):
        for tf in ("1d", "4h", "1h", "15m"):
            try:
                extra_ohlcv_by_tf[tf] = fetch_ohlcv_sync(symbol, from_date, to_date, tf, av_key=av_key)
                logger.info("MTF backtest %s %s: %d bars", symbol, tf, len(extra_ohlcv_by_tf[tf]))
            except Exception as exc:
                logger.warning("MTF backtest fetch failed %s %s: %s", symbol, tf, exc)

    result_row = BacktestResult(
        strategy_name=strategy_name,
        symbol=symbol,
        from_date=from_date,
        to_date=to_date,
        params_json=json.dumps(params),
        initial_balance=initial_balance,
        leverage=leverage,
        risk_per_trade_pct=risk_per_trade_pct,
        status="RUNNING" if ohlcv else "FAILED",
        batch_id=batch_id,
    )
    db.add(result_row)
    db.commit()
    db.refresh(result_row)

    if not ohlcv:
        result_row.metrics_json = json.dumps({"error": "No OHLCV data available"})
        result_row.status = "FAILED"
        db.commit()
        return result_row.id

    try:
        metrics = _run_backtest_sync(
            strategy_name, symbol, from_date, to_date,
            params, initial_balance, leverage, risk_per_trade_pct, ohlcv,
            adapt_every_n_trades=adapt_every_n,
            extra_ohlcv_by_tf=extra_ohlcv_by_tf or None,
        )
        equity_curve = metrics.pop("equity_curve", [])
        trade_log = metrics.pop("trade_log", [])
        monthly_breakdown = metrics.pop("monthly_breakdown", {})
        parameter_evolution_log = metrics.pop("parameter_evolution_log", {})
        drawdown_periods = metrics.pop("drawdown_periods", [])

        result_row.metrics_json = json.dumps(metrics)
        result_row.equity_curve_json = json.dumps(equity_curve)
        result_row.trade_log_json = trade_log
        result_row.monthly_breakdown_json = monthly_breakdown
        result_row.parameter_evolution_log_json = parameter_evolution_log
        result_row.drawdown_periods_json = drawdown_periods
        result_row.status = "COMPLETED"
        result_row.completed_at = datetime.utcnow()
    except Exception as exc:
        logger.exception("Backtest run failed for %s/%s", strategy_name, symbol)
        result_row.metrics_json = json.dumps({"error": str(exc)})
        result_row.status = "FAILED"

    db.commit()
    return result_row.id


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------

async def run_batch_backtest(
    db: Session,
    batch_id: str,
    runs: list[dict],
    shared_settings: dict,
    executor: ProcessPoolExecutor,
) -> None:
    """
    Background coroutine: runs all backtest jobs concurrently, then computes
    cross-analysis and pair analysis. Updates BacktestBatch record on completion.
    """
    loop = asyncio.get_event_loop()
    result_ids: list[int] = []
    failures: list[str] = []

    futures = []
    for run in runs:
        futures.append(
            loop.run_in_executor(
                executor,
                run_backtest_sync_standalone,
                run["strategy_name"],
                run["symbol"],
                run.get("from_date", shared_settings.get("from_date", "2024-01-01")),
                run.get("to_date", shared_settings.get("to_date", "2024-12-31")),
                run.get("params", {}),
                float(run.get("initial_balance", 10000)),
                int(run.get("leverage", 100)),
                float(run.get("risk_per_trade_pct", 1.0)),
                "1d",
            )
        )

    raw_results = await asyncio.gather(*futures, return_exceptions=True)

    from ..db import SessionLocal
    for run, result in zip(runs, raw_results):
        per_db = SessionLocal()
        try:
            if isinstance(result, Exception):
                error_msg = str(result)
                failures.append(f"{run['strategy_name']}: {error_msg}")
                result_row = BacktestResult(
                    strategy_name=run["strategy_name"],
                    symbol=run.get("symbol", "XAUUSD"),
                    from_date=run.get("from_date", shared_settings.get("from_date", "2024-01-01")),
                    to_date=run.get("to_date", shared_settings.get("to_date", "2024-12-31")),
                    params_json=json.dumps(run.get("params", {})),
                    initial_balance=float(run.get("initial_balance", 10000)),
                    leverage=int(run.get("leverage", 100)),
                    risk_per_trade_pct=float(run.get("risk_per_trade_pct", 1.0)),
                    metrics_json=json.dumps({"error": error_msg}),
                    status="FAILED",
                    batch_id=batch_id,
                )
                per_db.add(result_row)
                per_db.commit()
                per_db.refresh(result_row)
                result_ids.append(result_row.id)
            else:
                result_row = BacktestResult(
                    strategy_name=run["strategy_name"],
                    symbol=run.get("symbol", "XAUUSD"),
                    from_date=run.get("from_date", shared_settings.get("from_date", "2024-01-01")),
                    to_date=run.get("to_date", shared_settings.get("to_date", "2024-12-31")),
                    params_json=json.dumps(run.get("params", {})),
                    initial_balance=float(run.get("initial_balance", 10000)),
                    leverage=int(run.get("leverage", 100)),
                    risk_per_trade_pct=float(run.get("risk_per_trade_pct", 1.0)),
                    metrics_json=json.dumps({
                        k: v for k, v in result.items()
                        if k not in ("equity_curve", "trade_log", "drawdown_periods")
                    }),
                    equity_curve_json=json.dumps(result.get("equity_curve", [])),
                    trade_log_json=result.get("trade_log"),
                    monthly_breakdown_json=result.get("monthly_breakdown"),
                    parameter_evolution_log_json=result.get("parameter_evolution_log"),
                    drawdown_periods_json=result.get("drawdown_periods"),
                    status="COMPLETED",
                    completed_at=datetime.utcnow(),
                    batch_id=batch_id,
                )
                per_db.add(result_row)
                per_db.commit()
                per_db.refresh(result_row)
                result_ids.append(result_row.id)
        except Exception as e:
            logger.exception("Failed to persist batch result for %s", run.get("strategy_name"))
            failures.append(f"{run['strategy_name']}: persistence error {e}")
        finally:
            per_db.close()

    analysis_db = SessionLocal()
    try:
        bt_results = crud.get_backtest_results_for_batch(analysis_db, batch_id)
        cross_analysis = _compute_cross_analysis(bt_results)
        await _compute_pair_analyses(analysis_db, batch_id, bt_results)
        final_status = "PARTIAL_FAILURE" if failures else "COMPLETE"
        crud.update_backtest_batch(analysis_db, batch_id, final_status, cross_analysis)
        logger.info("Batch %s completed with status %s", batch_id, final_status)
    except Exception as e:
        logger.exception("Batch post-processing failed for batch %s", batch_id)
        crud.update_backtest_batch(analysis_db, batch_id, "PARTIAL_FAILURE", {})
    finally:
        analysis_db.close()


def _compute_cross_analysis(bt_results: list[BacktestResult]) -> dict:
    if not bt_results:
        return {}

    scored: list[dict] = []
    for r in bt_results:
        try:
            metrics = json.loads(r.metrics_json or "{}")
            wr = metrics.get("win_rate", 0)
            roi = metrics.get("roi_pct", 0)
            pf = metrics.get("profit_factor", 1)
            composite = _compute_composite_score(wr, pf)
            scored.append({
                "strategy_name": r.strategy_name,
                "composite_score": round(composite, 4),
                "win_rate": round(wr / 100, 4),
                "roi_pct": round(roi, 2),
                "profit_factor": round(pf, 3),
                "result_id": r.id,
                "trade_log": r.trade_log_json or [],
            })
        except Exception:
            continue

    ranked = sorted(scored, key=lambda x: x["composite_score"], reverse=True)

    correlation_matrix: dict[str, dict[str, float]] = {}
    for s in scored:
        correlation_matrix[s["strategy_name"]] = {}

    for s1, s2 in combinations(scored, 2):
        corr = _compute_trade_correlation(s1["trade_log"], s2["trade_log"])
        correlation_matrix[s1["strategy_name"]][s2["strategy_name"]] = round(corr, 3)
        correlation_matrix[s2["strategy_name"]][s1["strategy_name"]] = round(corr, 3)

    best_wr = max((s["win_rate"] for s in scored), default=0)
    complementary_pairs: list[dict] = []
    for s1, s2 in combinations(scored, 2):
        corr = correlation_matrix.get(s1["strategy_name"], {}).get(s2["strategy_name"], 1.0)
        combined_wr = (s1["win_rate"] + s2["win_rate"]) / 2
        if corr < 0.35 and combined_wr > best_wr:
            complementary_pairs.append({
                "pair": [s1["strategy_name"], s2["strategy_name"]],
                "combined_win_rate": round(combined_wr, 4),
                "correlation": round(corr, 3),
            })

    dominant = ranked[0]["strategy_name"] if ranked else None

    for s in ranked:
        s.pop("trade_log", None)
        s.pop("result_id", None)

    return {
        "ranked_by_composite_score": ranked,
        "correlation_matrix": correlation_matrix,
        "complementary_pairs": complementary_pairs,
        "dominant_strategy": dominant,
        "ensemble_simulation": {},
    }


def _compute_trade_correlation(trades_a: list[dict], trades_b: list[dict]) -> float:
    if not trades_a or not trades_b:
        return 0.0

    def parse_date(d: str | None) -> datetime | None:
        if not d:
            return None
        try:
            return datetime.fromisoformat(d[:10])
        except ValueError:
            return None

    windows_b: list[tuple[datetime, datetime]] = []
    for t in trades_b:
        o = parse_date(t.get("opened_at") or t.get("date"))
        c = parse_date(t.get("closed_at"))
        if o and c and c > o:
            windows_b.append((o, c))

    if not windows_b:
        return 0.0

    overlap_count = 0
    for t in trades_a:
        o = parse_date(t.get("opened_at") or t.get("date"))
        c = parse_date(t.get("closed_at"))
        if not (o and c and c > o):
            continue
        for wb_o, wb_c in windows_b:
            if o <= wb_c and c >= wb_o:
                overlap_count += 1
                break

    return overlap_count / len(trades_a) if trades_a else 0.0


async def _compute_pair_analyses(
    db: Session,
    batch_id: str,
    bt_results: list[BacktestResult],
) -> None:
    if len(bt_results) < 2:
        return

    result_map: dict[str, BacktestResult] = {r.strategy_name: r for r in bt_results}
    scored_map: dict[str, float] = {}
    for r in bt_results:
        try:
            metrics = json.loads(r.metrics_json or "{}")
            wr = metrics.get("win_rate", 0)
            pf = metrics.get("profit_factor", 1)
            scored_map[r.strategy_name] = _compute_composite_score(wr, pf)
        except Exception:
            scored_map[r.strategy_name] = 0.0

    groq_key = crud.get_setting(db, "groq_api_key") or ""

    strategy_names = list(result_map.keys())
    combo_sizes = [2]
    if len(strategy_names) <= 5:
        combo_sizes.append(3)

    for size in combo_sizes:
        combo_type = "pair" if size == 2 else "triple"
        for combo in combinations(strategy_names, size):
            combo_list = list(combo)
            results_for_combo = [result_map[n] for n in combo_list]

            pair_metrics = _simulate_pair_ensemble(results_for_combo)
            individual_scores = {n: round(scored_map.get(n, 0.0), 4) for n in combo_list}
            max_individual = max(individual_scores.values()) if individual_scores else 0.0
            synergy = (
                round(pair_metrics["combined_composite_score"] / max_individual, 4)
                if max_individual > 0 else 0.0
            )
            recommended = synergy > 1.05

            analysis_json = None
            if groq_key:
                try:
                    analysis_json = await _fetch_groq_narrative(
                        groq_key, combo_list, pair_metrics, individual_scores
                    )
                except Exception as e:
                    logger.warning("Groq narrative failed for %s: %s", combo_list, e)

            row = StrategyPairAnalysis(
                batch_id=batch_id,
                strategy_names_json=combo_list,
                combination_type=combo_type,
                combined_win_rate=pair_metrics.get("combined_win_rate"),
                combined_roi_pct=pair_metrics.get("combined_roi_pct"),
                combined_profit_factor=pair_metrics.get("combined_profit_factor"),
                combined_composite_score=pair_metrics.get("combined_composite_score"),
                individual_scores_json=individual_scores,
                agreement_rate=pair_metrics.get("agreement_rate"),
                disagreement_win_rate=pair_metrics.get("disagreement_win_rate"),
                correlation=pair_metrics.get("correlation"),
                synergy_score=synergy,
                recommended=recommended,
                analysis_json=analysis_json,
                computed_at=datetime.utcnow(),
            )
            crud.create_pair_analysis(db, row)


def _simulate_pair_ensemble(results: list[BacktestResult]) -> dict:
    all_metrics = []
    all_trade_logs = []
    for r in results:
        try:
            m = json.loads(r.metrics_json or "{}")
            all_metrics.append(m)
            all_trade_logs.append(r.trade_log_json or [])
        except Exception:
            continue

    if not all_metrics:
        return {
            "combined_win_rate": None,
            "combined_roi_pct": None,
            "combined_profit_factor": None,
            "combined_composite_score": None,
            "agreement_rate": None,
            "disagreement_win_rate": None,
            "correlation": None,
        }

    combined_wr = sum(m.get("win_rate", 0) / 100 for m in all_metrics) / len(all_metrics)
    combined_roi = sum(m.get("roi_pct", 0) for m in all_metrics) / len(all_metrics)
    combined_pf = sum(m.get("profit_factor", 1) for m in all_metrics) / len(all_metrics)
    combined_composite = _compute_composite_score(combined_wr * 100, combined_pf)

    agreement_rate = None
    disagreement_win_rate = None
    if len(all_trade_logs) == 2:
        logs_a, logs_b = all_trade_logs[0], all_trade_logs[1]
        if logs_a and logs_b:
            agree = 0
            disagree_wins = 0
            disagree_total = 0
            for ta in logs_a:
                for tb in logs_b:
                    ta_open = (ta.get("opened_at") or ta.get("date") or "")[:10]
                    tb_open = (tb.get("opened_at") or tb.get("date") or "")[:10]
                    if ta_open and ta_open == tb_open:
                        if ta.get("direction") == tb.get("direction"):
                            agree += 1
                        else:
                            disagree_total += 1
                            if ta.get("result") == "WIN" or tb.get("result") == "WIN":
                                disagree_wins += 1
                        break
            total_matched = agree + disagree_total
            if total_matched > 0:
                agreement_rate = round(agree / total_matched, 4)
                disagreement_win_rate = round(disagree_wins / disagree_total, 4) if disagree_total > 0 else 0.0

    if len(all_trade_logs) == 2:
        corr = _compute_trade_correlation(all_trade_logs[0], all_trade_logs[1])
    else:
        pairs = list(combinations(range(len(all_trade_logs)), 2))
        if pairs:
            corr = sum(
                _compute_trade_correlation(all_trade_logs[a], all_trade_logs[b])
                for a, b in pairs
            ) / len(pairs)
        else:
            corr = 0.0

    return {
        "combined_win_rate": round(combined_wr, 4),
        "combined_roi_pct": round(combined_roi, 2),
        "combined_profit_factor": round(combined_pf, 3),
        "combined_composite_score": round(combined_composite, 4),
        "agreement_rate": agreement_rate,
        "disagreement_win_rate": disagreement_win_rate,
        "correlation": round(corr, 3),
    }


async def _fetch_groq_narrative(
    api_key: str,
    strategy_names: list[str],
    pair_metrics: dict,
    individual_scores: dict[str, float],
) -> dict | None:
    try:
        import re
        from tenacity import retry, stop_after_attempt, wait_exponential

        @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
        async def _call() -> dict:
            system_prompt = (
                "You are a quantitative trading analyst. Given performance metrics for individual "
                "strategies and their combination, provide a concise analysis explaining: "
                "(1) why this pair works well or poorly together, "
                "(2) what market conditions favour this combination, "
                "(3) one specific risk to watch for. "
                'Respond ONLY in JSON: {"narrative": "...", "works_well_when": "...", "watch_out_for": "..."}'
            )
            user_prompt = (
                f"Strategies: {strategy_names}\n"
                f"Combined win rate: {pair_metrics.get('combined_win_rate')}\n"
                f"Combined ROI: {pair_metrics.get('combined_roi_pct')}\n"
                f"Synergy score: {pair_metrics.get('synergy_score')}\n"
                f"Agreement rate: {pair_metrics.get('agreement_rate')}\n"
                f"Individual scores: {individual_scores}"
            )
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.1-8b-instant",
                        "max_tokens": 1000,
                        "temperature": 0.1,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE).strip()
            return json.loads(text)

        return await _call()
    except Exception as e:
        logger.warning("Groq narrative call failed: %s", e)
        return None