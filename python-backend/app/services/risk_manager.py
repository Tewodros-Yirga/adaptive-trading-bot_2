import re
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from .. import crud

RISK_KEYS = [
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


def _parse_balance(value) -> float | None:
    """Parse an account balance into a plain USD-equivalent float.

    The account currency (USD, USDC, USDT, ...) is deliberately IGNORED — a
    USDC-denominated account is sized exactly like a USD account (1:1), so
    195 USDC and 195 USD produce identical lot sizes. Handles brokers that
    return the balance as a number, a numeric string, or a string carrying a
    currency label (e.g. ``"195.00 USDC"`` or ``"$195.00"``). Returns None
    when the value cannot be parsed.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    # Keep digits, separators, minus/plus and exponent markers; drop currency
    # labels (USD, USDC, $, ...) so any account currency sizes like the dollar.
    cleaned = re.sub(r"[^\d.,eE+\-]", "", text).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def get_live_balance() -> tuple[float | None, float | None]:
    """
    Fetch (balance, equity) from the MT5 account via the bridge.

    The account currency (USD/USDC/USDT/...) is treated as USD-equivalent 1:1
    for sizing — a 195 USDC account behaves exactly like a 195 USD account.
    Returns (None, None) when the bridge is unavailable or the balance cannot
    be parsed, so callers can fall back to the stored setting. Never raises.
    """
    try:
        from .bridge_client import bridge_client
        acct = bridge_client.get_account()
        if acct and acct.get("balance") is not None:
            balance = _parse_balance(acct.get("balance"))
            equity = _parse_balance(acct.get("equity"))
            if balance is not None:
                return balance, (equity if equity is not None else balance)
    except Exception:
        pass
    return None, None


def get_effective_balance(db: Database) -> float:
    """
    The account balance used for ALL risk/sizing math — always the live MT5
    balance from the bridge (USDC treated as USD 1:1; see get_live_balance).

    There is intentionally NO stored-balance fallback: the MT5 account is the
    only source of truth. Returns 0.0 when the bridge is unreachable and no
    cached account is available, which safely degrades all risk math (the
    dynamic lot formula then floors at the 0.01 minimum).
    """
    live, _ = get_live_balance()
    return live if live is not None else 0.0


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


def _normalize_symbol(symbol: str) -> str:
    """Normalize broker symbol names for pip classification.

    MT5 often appends a contract/CFD suffix to the base instrument
    (e.g. ``XAUUSDc``, ``US30.c``). Stripping a trailing ``c`` / ``.c`` lets
    the pip math classify the symbol correctly — otherwise ``XAUUSDc`` would
    fall through to the 0.0001 forex pip and mis-size stops and lots.
    """
    s = (symbol or "").upper().strip()
    if s.endswith(".C"):
        s = s[:-2]
    elif s.endswith("C"):
        s = s[:-1]
    return s


def pip_value(symbol: str) -> float:
    """Returns pip value for common symbols (handles CFD suffixes like ``XAUUSDc``)."""
    s = _normalize_symbol(symbol)
    if "JPY" in s:
        return 0.01
    if s in ("XAUUSD", "GOLD") or s.startswith("XAU"):
        return 0.1
    if s in ("XAGUSD", "SILVER") or s.startswith("XAG"):
        return 0.001
    if s in ("US30", "NAS100", "SPX500", "US500", "DJ30", "NDX100"):
        return 1.0
    return 0.0001


def pip_cost_per_lot(symbol: str) -> float:
    """Returns approximate USD cost per pip per standard lot (handles CFD suffixes)."""
    s = _normalize_symbol(symbol)
    if "JPY" in s:
        return 10.0
    if s in ("XAUUSD", "GOLD") or s.startswith("XAU"):
        return 10.0
    if s in ("XAGUSD", "SILVER") or s.startswith("XAG"):
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
    """DYNAMIC position sizing.

    User-selected formula:  lot = (balance * risk% * 0.1) / (6 * stop distance)
    where the stop distance is in DOLLARS (price units). The 6 divisor keeps
    positions small (risk%/100 * 0.1 / 6 = risk% * 0.0167% of balance), so the
    lot grows proportionally with the account balance while staying low for
    small accounts.

    Example: $100 balance, 20% risk, $15 stop (150-pip cap on gold) →
             (100 * 0.2 * 0.1) / (6 * 15) = 0.02 lots.

    Hard rules:
      - Accounts under $50 always trade the 0.01 broker-minimum lot.
      - 0.01 is the absolute floor (broker minimum lot).

    The previous margin-based ceiling ``(balance*leverage)/(entry*100000)`` is
    deliberately NOT applied — it forced every sub-$2,200 account to 0.01 lots.
    An optional hard ceiling can be enforced via the ``max_lot_size`` setting
    (applied in ``check_and_compute_lot_size``).
    """
    if account_balance < 50.0:
        return 0.01
    risk_amount = account_balance * (risk_per_trade_pct / 100.0) * 0.1
    sl_distance = abs(entry_price - stop_loss)  # stop distance in dollars (price units)
    if sl_distance == 0:
        return 0.01
    lot_size = risk_amount / (6.0 * sl_distance)
    return round(max(0.01, lot_size), 2)


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
    balance = get_effective_balance(db)
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
        # Optional hard ceiling — user-set max_lot_size wins over DYNAMIC sizing.
        try:
            _max_lot = float(crud.get_setting(db, "max_lot_size") or 0) or 0.0
        except (TypeError, ValueError):
            _max_lot = 0.0
        if _max_lot > 0:
            lot_size = min(lot_size, _max_lot)
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

    # ── Live balance from MT5 bridge (the ONLY source — no stored fallback) ─
    live_balance, live_equity = get_live_balance()
    balance = live_balance if live_balance is not None else 0.0

    return {
        "account_balance": live_balance,
        "account_equity": live_equity,
        "balance_source": "mt5_bridge" if live_balance is not None else "unavailable",
        "open_trades_count": open_count,
        "daily_pnl": round(today_pnl, 4),
        "daily_loss_pct": round((-today_pnl / balance * 100) if balance > 0 else 0.0, 2),
        "current_drawdown": round(stats.get("max_drawdown", 0.0), 4),
        "current_drawdown_pct": round((stats.get("max_drawdown", 0.0) / balance * 100) if balance > 0 else 0.0, 2),
        "trading_halt": settings["trading_halt"],
        "settings": settings,
    }