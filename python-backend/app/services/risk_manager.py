from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from .. import crud

RISK_KEYS = [
    "account_balance",
    "leverage",
    "risk_per_trade_pct",
    "max_open_trades",
    "max_daily_loss_pct",
    "max_drawdown_pct",
    "lot_size_mode",
    "trading_halt",
    "symbol_exposure_limit",
]

RISK_DEFAULTS: dict[str, Any] = {
    "account_balance": 10.0,
    "leverage": 100,
    "risk_per_trade_pct": 1.0,
    "max_open_trades": 5,
    "max_daily_loss_pct": 5.0,
    "max_drawdown_pct": 20.0,
    "lot_size_mode": "FIXED",
    "trading_halt": False,
    "symbol_exposure_limit": 1.0,
}


def _alert(db: Database, level: str, event: str, message: str, context: dict | None = None) -> None:
    """Best-effort alert dispatch — never raises into risk logic."""
    try:
        from .alerts import dispatch_alert
        dispatch_alert(db, level, event, message, context)
    except Exception:
        pass


def get_live_balance() -> tuple[float | None, float | None]:
    """
    Fetch (balance, equity) from the MT5 account via the bridge.
    Returns (None, None) when the bridge is unavailable so callers can fall
    back to the stored setting. Never raises.
    """
    try:
        from .bridge_client import bridge_client
        acct = bridge_client.get_account()
        if acct and acct.get("balance") is not None:
            balance = float(acct["balance"])
            equity = float(acct.get("equity", balance))
            return balance, equity
    except Exception:
        pass
    return None, None


def get_effective_balance(db: Database, settings: dict | None = None) -> float:
    """
    The account balance to use for all risk/sizing math.

    The MT5 account is the source of truth — always prefer the live balance
    from the bridge. The stored ``account_balance`` setting is only a fallback
    for when the bridge is unreachable (it is NOT a user-authoritative value).
    """
    live, _ = get_live_balance()
    if live is not None:
        return live
    if settings is None:
        settings = get_risk_settings(db)
    return float(settings["account_balance"])


def get_risk_settings(db: Database) -> dict:
    stored = crud.get_settings(db, RISK_KEYS)
    result: dict[str, Any] = {}
    for key, default in RISK_DEFAULTS.items():
        raw = stored.get(key)
        if raw is None:
            result[key] = default
        elif isinstance(default, bool):
            result[key] = raw.lower() in ("true", "1", "yes")
        elif isinstance(default, int):
            result[key] = int(float(raw))
        elif isinstance(default, float):
            result[key] = float(raw)
        else:
            result[key] = raw
    return result


def update_risk_settings(db: Database, payload: dict) -> dict:
    current = get_risk_settings(db)
    merged = {**current, **payload}
    validated = {
        "account_balance": max(0.0, float(merged["account_balance"])),
        "leverage": min(max(int(merged["leverage"]), 1), 2000),
        "risk_per_trade_pct": min(max(float(merged["risk_per_trade_pct"]), 0.01), 100.0),
        "max_open_trades": min(max(int(merged["max_open_trades"]), 1), 100),
        "max_daily_loss_pct": min(max(float(merged["max_daily_loss_pct"]), 0.1), 50.0),
        "max_drawdown_pct": min(max(float(merged["max_drawdown_pct"]), 1.0), 100.0),
        "lot_size_mode": merged["lot_size_mode"] if merged["lot_size_mode"] in ("FIXED", "DYNAMIC") else "FIXED",
        "trading_halt": bool(merged["trading_halt"]),
        "symbol_exposure_limit": min(max(float(merged["symbol_exposure_limit"]), 0.01), 100.0),
    }
    for key, value in validated.items():
        crud.set_setting(db, key, str(value))
    return validated


def pip_value(symbol: str) -> float:
    """Returns pip value for common symbols."""
    symbol = symbol.upper()
    if "JPY" in symbol:
        return 0.01
    if symbol in ("XAUUSD", "GOLD"):
        return 0.1
    if symbol in ("XAGUSD", "SILVER"):
        return 0.001
    if symbol in ("US30", "NAS100", "SPX500"):
        return 1.0
    return 0.0001


def pip_cost_per_lot(symbol: str) -> float:
    """Returns approximate USD cost per pip per standard lot."""
    symbol = symbol.upper()
    if "JPY" in symbol:
        return 10.0
    if symbol in ("XAUUSD", "GOLD"):
        return 10.0
    if symbol in ("XAGUSD", "SILVER"):
        return 50.0
    return 10.0


def compute_dynamic_lot_size(
    account_balance: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss: float,
    symbol: str,
    leverage: int = 100,
) -> float:
    risk_amount = account_balance * (risk_per_trade_pct / 100.0)
    pv = pip_value(symbol)
    if pv == 0:
        pv = 0.0001
    sl_pips = abs(entry_price - stop_loss) / pv
    if sl_pips == 0:
        return 0.01
    cost = pip_cost_per_lot(symbol)
    lot_size = risk_amount / (sl_pips * cost)
    max_lot = (account_balance * leverage) / (entry_price * 100000) if entry_price > 0 else 10.0
    return round(max(0.01, min(lot_size, max_lot)), 2)


def check_and_compute_lot_size(
    db: Database,
    symbol: str,
    entry_price: float,
    stop_loss: float,
    default_lot_size: float = 0.01,
) -> tuple[float, str | None]:
    """
    Returns (lot_size, block_reason).
    block_reason is None if trade is allowed, string if blocked.
    """
    settings = get_risk_settings(db)

    if settings["trading_halt"]:
        return 0.0, "Trading halt is active"

    # ── Bridge-outage kill-switch (item 1) ────────────────────────────────
    # Don't open new entries while the MT5 bridge is known-down (circuit
    # breaker OPEN). Orders would fail anyway; this fails safe with a clear
    # reason instead of a thrown bridge error. No-op during normal operation
    # (breaker CLOSED). Disable by setting block_entries_on_bridge_outage=false.
    _block_on_outage = str(
        crud.get_setting(db, "block_entries_on_bridge_outage") or "true"
    ).lower() in ("true", "1", "yes", "on")
    if _block_on_outage:
        try:
            from .bridge_client import bridge_client
            if bridge_client.circuit_state == "OPEN":
                _alert(db, "warning", "bridge_outage",
                       "MT5 bridge circuit breaker OPEN — new entries paused", {"symbol": symbol})
                return 0.0, "Bridge unavailable (circuit breaker OPEN) — new entries paused"
        except Exception:
            pass

    # ── Spread guard (item 11, opt-in) ────────────────────────────────────
    # When max_spread_pips > 0, reject entries while the live bid/ask spread is
    # wider than the limit (news spikes, illiquid sessions). Default 0 = OFF, so
    # no extra bridge call and no behaviour change unless you enable it.
    _max_spread_pips = float(crud.get_setting(db, "max_spread_pips") or 0)
    if _max_spread_pips > 0:
        try:
            from .bridge_client import bridge_client
            tick = bridge_client.get_tick(symbol)
            pv = pip_value(symbol) or 0.0001
            spread_pips = abs(float(tick.get("spread") or 0.0)) / pv
            if spread_pips > _max_spread_pips:
                _alert(db, "warning", "spread_too_wide",
                       f"Spread {spread_pips:.1f}p > limit {_max_spread_pips:.1f}p for {symbol} — entry skipped",
                       {"symbol": symbol, "spread_pips": round(spread_pips, 2)})
                return 0.0, f"Spread too wide ({spread_pips:.1f} pips > {_max_spread_pips:.1f})"
        except Exception:
            pass  # tick unavailable — never block on a guard failure

    open_trades = crud.get_recent_trades(db, 1000)
    open_count = sum(1 for t in open_trades if t.result == "OPEN")
    if open_count >= settings["max_open_trades"]:
        return 0.0, f"Max open trades limit reached ({settings['max_open_trades']})"

    symbol_lots = sum(
        (t.lot_size or 0) for t in open_trades
        if t.result == "OPEN" and t.symbol.upper() == symbol.upper()
    )
    if symbol_lots >= settings["symbol_exposure_limit"]:
        return 0.0, f"Symbol exposure limit reached for {symbol}"

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    closed_trades = crud.get_closed_trades(db, 10000)
    today_pnl = sum(
        (t.pnl or 0) for t in closed_trades
        if t.closed_at and (t.closed_at if t.closed_at.tzinfo else t.closed_at.replace(tzinfo=timezone.utc)) >= today_start
    )
    balance = get_effective_balance(db, settings)
    if balance > 0:
        daily_loss_pct = (-today_pnl / balance) * 100
        if daily_loss_pct >= settings["max_daily_loss_pct"]:
            _alert(db, "critical", "daily_loss_limit",
                   f"Daily loss limit reached ({daily_loss_pct:.1f}%) — entries blocked",
                   {"daily_loss_pct": round(daily_loss_pct, 2)})
            return 0.0, f"Daily loss limit reached ({daily_loss_pct:.1f}%)"

    stats = crud.get_stats(db)
    if balance > 0 and stats.get("max_drawdown", 0) / balance * 100 >= settings["max_drawdown_pct"]:
        _alert(db, "critical", "drawdown_limit",
               "Max drawdown limit reached — entries blocked",
               {"max_drawdown": stats.get("max_drawdown", 0)})
        return 0.0, "Max drawdown limit reached"

    mode = settings["lot_size_mode"]
    if mode == "DYNAMIC":
        lot_size = compute_dynamic_lot_size(
            account_balance=balance,
            risk_per_trade_pct=settings["risk_per_trade_pct"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            symbol=symbol,
            leverage=int(settings["leverage"]),
        )
    else:
        lot_size = default_lot_size

    return lot_size, None


def get_risk_status(db: Database) -> dict:
    settings = get_risk_settings(db)
    open_trades = crud.get_recent_trades(db, 1000)
    open_count = sum(1 for t in open_trades if t.result == "OPEN")

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    closed_trades = crud.get_closed_trades(db, 10000)
    today_pnl = sum(
        (t.pnl or 0) for t in closed_trades
        if t.closed_at and (t.closed_at if t.closed_at.tzinfo else t.closed_at.replace(tzinfo=timezone.utc)) >= today_start
    )
    stats = crud.get_stats(db)

    # ── Live balance from MT5 bridge (the authoritative source) ───────────
    # Fall back to the stored setting only when the bridge is unreachable.
    live_balance, live_equity = get_live_balance()
    balance = live_balance if live_balance is not None else settings["account_balance"]

    return {
        "account_balance": live_balance if live_balance is not None else settings["account_balance"],
        "account_equity": live_equity,
        "balance_source": "mt5_bridge" if live_balance is not None else "settings",
        "open_trades_count": open_count,
        "daily_pnl": round(today_pnl, 4),
        "daily_loss_pct": round((-today_pnl / balance * 100) if balance > 0 else 0.0, 2),
        "current_drawdown": round(stats.get("max_drawdown", 0.0), 4),
        "current_drawdown_pct": round((stats.get("max_drawdown", 0.0) / balance * 100) if balance > 0 else 0.0, 2),
        "trading_halt": settings["trading_halt"],
        "settings": settings,
    }