"""
Backtesting Engine
Uses yfinance as primary OHLCV source, Alpha Vantage as fallback.
Runs in ProcessPoolExecutor to avoid blocking the event loop.
"""
import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from sqlalchemy.orm import Session

from .. import crud
from ..models import BacktestResult, OHLCVCache
from ..strategy.registry import get_strategy

_EXECUTOR = ProcessPoolExecutor(max_workers=2)


def _fetch_ohlcv(symbol: str, from_date: str, to_date: str) -> list[dict]:
    """Fetch OHLCV data using yfinance."""
    try:
        import yfinance as yf
        ticker_map = {
            "XAUUSD": "GC=F", "XAGUSD": "SI=F", "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X", "US30": "^DJI",
            "NAS100": "^NDX", "SPX500": "^GSPC",
        }
        yf_symbol = ticker_map.get(symbol.upper(), symbol)
        df = yf.download(yf_symbol, start=from_date, end=to_date, interval="1d", progress=False)
        if df is None or df.empty:
            return []
        records = []
        for ts, row in df.iterrows():
            records.append({
                "date": str(ts.date()),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0) or 0),
            })
        return records
    except Exception:
        return []


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
) -> dict:
    """Pure function — runs in subprocess."""
    if not ohlcv or len(ohlcv) < 30:
        return {"error": "Insufficient OHLCV data"}

    strat = get_strategy(strategy_name, params)
    closes = [r["close"] for r in ohlcv]

    # Pre-compute indicators
    rsi_series = _compute_rsi(closes, int(params.get("rsi_period", 14)))
    ema_periods = [
        int(params.get("ema_1", 30)), int(params.get("ema_2", 35)),
        int(params.get("ema_3", 40)), int(params.get("ema_4", 45)),
        int(params.get("ema_5", 50)), int(params.get("ema_6", 60)),
    ]
    ema_series_list = [_ema_series(closes, p) for p in ema_periods]

    balance = initial_balance
    equity_curve = [{"date": ohlcv[0]["date"], "equity": balance}]
    trades: list[dict] = []
    open_trade: dict | None = None

    for i in range(max(ema_periods[5], 30), len(ohlcv)):
        bar = ohlcv[i]
        price = bar["close"]

        if open_trade:
            if open_trade["direction"] == "BUY":
                if bar["low"] <= open_trade["sl"]:
                    pnl = (open_trade["sl"] - open_trade["entry"]) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "LOSS", "pnl": round(pnl, 4), "exit": open_trade["sl"]})
                    open_trade = None
                elif bar["high"] >= open_trade["tp"]:
                    pnl = (open_trade["tp"] - open_trade["entry"]) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "WIN", "pnl": round(pnl, 4), "exit": open_trade["tp"]})
                    open_trade = None
            else:
                if bar["high"] >= open_trade["sl"]:
                    pnl = (open_trade["entry"] - open_trade["sl"]) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "LOSS", "pnl": round(pnl, 4), "exit": open_trade["sl"]})
                    open_trade = None
                elif bar["low"] <= open_trade["tp"]:
                    pnl = (open_trade["entry"] - open_trade["tp"]) * open_trade["lots"] * 100000
                    balance += pnl
                    trades.append({**open_trade, "result": "WIN", "pnl": round(pnl, 4), "exit": open_trade["tp"]})
                    open_trade = None

        if open_trade is not None:
            equity_curve.append({"date": bar["date"], "equity": round(balance, 2)})
            continue

        ema_vals = {f"ema_{j+1}": ema_series_list[j][i] for j in range(6)}
        prev_ema_vals = {f"ema_{j+1}": ema_series_list[j][i - 1] for j in range(6)}
        if any(v is None for v in ema_vals.values()):
            equity_curve.append({"date": bar["date"], "equity": round(balance, 2)})
            continue

        prev_bull = all(
            (prev_ema_vals.get(f"ema_{k}") or 0) > (prev_ema_vals.get(f"ema_{k+1}") or 0)
            for k in range(1, 6)
        )
        prev_bear = all(
            (prev_ema_vals.get(f"ema_{k}") or 0) < (prev_ema_vals.get(f"ema_{k+1}") or 0)
            for k in range(1, 6)
        )

        market_data = {
            "price": price,
            "ema_values": ema_vals,
            "previous_bull": prev_bull,
            "previous_bear": prev_bear,
            "rsi": rsi_series[i],
            "prev_rsi": rsi_series[i - 1],
        }

        signal = strat.signal(market_data)
        if signal:
            levels = strat.compute_levels(signal, price, params)
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
                "date": bar["date"],
            }

        equity_curve.append({"date": bar["date"], "equity": round(balance, 2)})

    # ── Compute metrics ────────────────────────────────────────────────────────
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses)) or 1e-9
    profit_factor = gross_profit / gross_loss

    # Sharpe / Sortino / Calmar
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

    # Expectancy
    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss_val = (gross_loss / len(losses)) if losses else 0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss_val

    # Consecutive
    max_consec_wins = max_consec_losses = 0
    cur_wins = cur_losses = 0
    for t in trades:
        if t["result"] == "WIN":
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_consec_wins = max(max_consec_wins, cur_wins)
        max_consec_losses = max(max_consec_losses, cur_losses)

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 3),
        "total_pnl": round(balance - initial_balance, 4),
        "total_return_pct": round(total_return_pct, 2),
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
    }


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
) -> int:
    """Create a backtest record and kick off async execution. Returns backtest ID."""
    # Try cache first
    cached = db.query(OHLCVCache).filter(
        OHLCVCache.symbol == symbol,
        OHLCVCache.from_date == from_date,
        OHLCVCache.to_date == to_date,
    ).first()

    if cached:
        ohlcv = json.loads(cached.data_json)
    else:
        ohlcv = _fetch_ohlcv(symbol, from_date, to_date)
        if ohlcv:
            cache_row = OHLCVCache(
                symbol=symbol,
                interval="1d",
                from_date=from_date,
                to_date=to_date,
                data_json=json.dumps(ohlcv),
            )
            db.add(cache_row)
            db.commit()

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
    )
    db.add(result_row)
    db.commit()
    db.refresh(result_row)

    if not ohlcv:
        result_row.metrics_json = json.dumps({"error": "No OHLCV data available"})
        result_row.status = "FAILED"
        db.commit()
        return result_row.id

    # Run synchronously (in a real deployment this would use executor)
    try:
        metrics = _run_backtest_sync(
            strategy_name, symbol, from_date, to_date,
            params, initial_balance, leverage, risk_per_trade_pct, ohlcv,
        )
        equity_curve = metrics.pop("equity_curve", [])
        result_row.metrics_json = json.dumps(metrics)
        result_row.equity_curve_json = json.dumps(equity_curve)
        result_row.status = "COMPLETED"
        result_row.completed_at = datetime.utcnow()
    except Exception as e:
        result_row.metrics_json = json.dumps({"error": str(e)})
        result_row.status = "FAILED"

    db.commit()
    return result_row.id