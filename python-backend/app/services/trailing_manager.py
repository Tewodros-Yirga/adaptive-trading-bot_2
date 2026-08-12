"""
Trailing-stop / break-even manager (item 12).

OPT-IN and OFF by default — the loop runs but does nothing until
``trailing_stop_enabled`` is set truthy in AppSettings, so adding it cannot
change a running system. When enabled it polls open positions from the MT5
bridge and tightens stops:

  - Break-even: once a position's open profit reaches ``breakeven_trigger_pct``
    of the entry price, move the stop to the entry (+ a small buffer) so the
    trade can no longer lose.
  - Trailing:   beyond that, keep the stop ``trailing_distance_pct`` behind the
    current price, only ever moving it in the favourable direction.

Stops are only ever moved to *reduce* risk (up for BUY, down for SELL); the
take-profit is left untouched. All bridge interaction is best-effort and never
raises into the loop.

Settings (AppSettings keys):
  trailing_stop_enabled        false/true        (default false → inert)
  trailing_poll_seconds        loop interval     (default 15)
  breakeven_trigger_pct        e.g. 0.3 (= 0.3%) (default 0.3)
  breakeven_buffer_pct         e.g. 0.02         (default 0.02)
  trailing_distance_pct        e.g. 0.4 (= 0.4%) (default 0.4)

TP-ladder handoff:
  This manager runs alongside the TP-ladder executor (ladder_manager). A trade
  with an attached ladder plan is handled by the %-trailing here ONLY until its
  stop reaches break-even — once the ladder owns the stop (stop at/beyond
  entry, or the ladder has already done its break-even/trailing steps) this
  loop leaves the trade alone and the ladder's partial closes + ATR-trail take
  over.
"""
from __future__ import annotations

import asyncio
import logging

from .bridge_client import bridge_client
from .. import crud
from ..db import COLL_TRADES, get_database

logger = logging.getLogger(__name__)


def _truthy(v) -> bool:
    return str(v or "").lower() in ("1", "true", "yes", "on")


def _num(md: dict, *keys: str) -> float | None:
    """First numeric value found among the given keys."""
    for k in keys:
        if k in md and md[k] is not None:
            try:
                return float(md[k])
            except (TypeError, ValueError):
                continue
    return None


def _ladder_owns_stop(pos: dict, plan: dict | None) -> bool:
    """
    True once the TP-ladder has taken over this position's stop, so the
    %-trailing manager must leave it alone:
      - the ladder explicitly finished its break-even / ATR-trailing steps, or
      - the current stop is already at/beyond break-even (entry) — the user's
        requested handoff point: trail until the SL is at break-even, then the
        ladder takes off.
    """
    if not plan:
        return False
    if plan.get("breakeven_done") or plan.get("trailing_active"):
        return True
    side = str(pos.get("type") or pos.get("direction") or "").upper()
    if side not in ("BUY", "SELL"):
        return False
    entry = _num(pos, "openPrice", "open_price", "price_open")
    cur_sl = _num(pos, "sl", "stopLoss", "stop_loss")
    if entry is None or entry <= 0 or cur_sl is None or cur_sl <= 0:
        return False
    return cur_sl >= entry if side == "BUY" else cur_sl <= entry


def _desired_stop(pos: dict, settings: dict) -> float | None:
    """Compute a new, strictly-safer stop-loss for a position, or None."""
    side = str(pos.get("type") or pos.get("direction") or "").upper()
    entry = _num(pos, "openPrice", "open_price", "price_open")
    price = _num(pos, "priceCurrent", "price_current", "currentPrice", "current_price")
    cur_sl = _num(pos, "sl", "stopLoss", "stop_loss") or 0.0
    if entry is None or price is None or entry <= 0 or price <= 0:
        return None
    if side not in ("BUY", "SELL"):
        return None

    be_trigger = settings["breakeven_trigger_pct"] / 100.0
    be_buffer = settings["breakeven_buffer_pct"] / 100.0
    trail = settings["trailing_distance_pct"] / 100.0

    if side == "BUY":
        profit_frac = (price - entry) / entry
        if profit_frac < be_trigger:
            return None
        breakeven = entry * (1.0 + be_buffer)
        trailing = price * (1.0 - trail)
        target = max(breakeven, trailing)
        # only move the stop UP (safer) and only if meaningfully better
        if target > cur_sl and target < price and (cur_sl == 0.0 or target - cur_sl > entry * 1e-5):
            return round(target, 5)
        return None
    else:  # SELL
        profit_frac = (entry - price) / entry
        if profit_frac < be_trigger:
            return None
        breakeven = entry * (1.0 - be_buffer)
        trailing = price * (1.0 + trail)
        target = min(breakeven, trailing)
        # only move the stop DOWN (safer)
        if (cur_sl == 0.0 or target < cur_sl) and target > price and (cur_sl == 0.0 or cur_sl - target > entry * 1e-5):
            return round(target, 5)
        return None


def _load_settings(db) -> dict:
    return {
        "breakeven_trigger_pct": float(crud.get_setting(db, "breakeven_trigger_pct") or 0.3),
        "breakeven_buffer_pct": float(crud.get_setting(db, "breakeven_buffer_pct") or 0.02),
        "trailing_distance_pct": float(crud.get_setting(db, "trailing_distance_pct") or 0.4),
    }


def _run_once(db) -> int:
    """One pass over open positions. Returns count of stops moved."""
    settings = _load_settings(db)
    try:
        positions = bridge_client.get_positions() or []
    except Exception as exc:
        logger.debug("trailing_manager: get_positions failed: %s", exc)
        return 0

    # Map MT5 ticket → ladder plan so the %-trailing can hand off to the TP
    # ladder once a trade's stop reaches break-even.
    ladder_by_ticket: dict[int, dict] = {}
    try:
        cursor = db[COLL_TRADES].find(
            {"ladder.enabled": True, "mt5_ticket": {"$exists": True}, "closed_at": {"$exists": False}},
            {"ladder": 1, "mt5_ticket": 1},
        )
        for _t in cursor:
            try:
                ladder_by_ticket[int(_t.get("mt5_ticket"))] = _t.get("ladder") or {}
            except (TypeError, ValueError):
                continue
    except Exception as exc:
        logger.debug("trailing_manager: ladder plan lookup failed: %s", exc)

    moved = 0
    for pos in positions:
        try:
            ticket = pos.get("ticket") or pos.get("orderId")
            if ticket is None:
                continue
            try:
                _ladder_plan = ladder_by_ticket.get(int(ticket))
            except (TypeError, ValueError):
                _ladder_plan = None
            if _ladder_owns_stop(pos, _ladder_plan):
                continue  # the TP ladder owns this stop now
            new_sl = _desired_stop(pos, settings)
            if new_sl is None:
                continue
            tp = _num(pos, "tp", "takeProfit", "take_profit")
            bridge_client.modify_position(int(ticket), stop_loss=new_sl, take_profit=tp)
            moved += 1
            try:
                from .alerts import notify_trade_operation
                notify_trade_operation(
                    db, "modify",
                    symbol=pos.get("symbol"),
                    stop_loss=new_sl,
                    take_profit=tp,
                    ticket=int(ticket),
                    extra={"source": "trailing_stop"},
                )
            except Exception:
                pass
            logger.info(
                "trailing_manager: moved SL for ticket %s (%s) -> %.5f",
                ticket, pos.get("symbol"), new_sl,
            )
        except Exception as exc:
            logger.debug("trailing_manager: modify failed for %s: %s", pos.get("ticket"), exc)
    return moved


async def trailing_stop_loop() -> None:
    """Background loop. Inert until trailing_stop_enabled is truthy."""
    logger.info("Trailing-stop manager loop started (inert until trailing_stop_enabled=true).")
    while True:
        interval = 15.0
        try:
            db = get_database()
            interval = float(crud.get_setting(db, "trailing_poll_seconds") or 15)
            # Runs alongside the TP-ladder executor: per-trade handoff inside
            # _run_once (via _ladder_owns_stop) decides who owns each stop —
            # %-trailing until the SL reaches break-even, ladder after that.
            if _truthy(crud.get_setting(db, "trailing_stop_enabled")):
                # Run the blocking bridge calls off the event loop.
                moved = await asyncio.to_thread(_run_once, db)
                if moved:
                    logger.info("trailing_manager: %d stop(s) tightened.", moved)
        except Exception as exc:
            logger.debug("trailing_stop_loop iteration error: %s", exc)
        await asyncio.sleep(max(5.0, interval))
