"""
app/services/ladder_manager.py — live TP-ladder executor.

Mirrors the backtester's exit ladder for LIVE positions so the params the
continuous backtester optimizes actually pay off in real trading. Per open
laddered trade it:

  • partial-closes the scheduled fraction at each TP level (TP1..TP4),
  • moves the stop to break-even once TP1 is reached,
  • activates an ATR trailing stop (1.5×ATR) once the stop reaches break-even
    (TP1 hit, or the %-trailing manager already moved it there), only ever
    moving the stop in the loss-reducing direction,

exactly as `_run_backtest_sync` simulates. The per-trade `ladder` plan
(attached at order placement) records the schedule and all progress, so the loop
is fully IDEMPOTENT — a missed, repeated, or post-restart poll cannot
double-close or move a stop backwards.

A ladder plan is attached at order placement (orchestrator) whenever the trade
opens with lot > 0.01 or `tp_ladder_enabled` is set, so this loop simply
processes every open trade that carries a `ladder` plan.

Handoff with the %-based trailing manager (trailing_manager): trailing owns the
stop until it reaches break-even; once the ladder has moved the stop to
break-even (or the stop is already there) the ladder owns it — the trailing
manager skips such trades via `_ladder_owns_stop`.

Lot tiers (broker min lot 0.01) are decided at plan-build time in
orchestrator._ladder_schedule:
  0.01 → no partials, trail only;  0.02 → 50% at TP1;  0.04+ → 25% ladder.
"""
from __future__ import annotations

import asyncio
import functools
import logging

from .. import crud
from ..db import COLL_TRADES, get_database
from .bridge_client import bridge_client

logger = logging.getLogger(__name__)

_TRAILING_ATR_MULT = 1.5
_LOT_STEP = 0.01
_POLL_DEFAULT = 10.0


def _hit(direction: str, price: float, level: float | None) -> bool:
    """Has price reached a TP in the profit direction?"""
    if level is None:
        return False
    return price >= level if direction == "BUY" else price <= level


def _trail_stop(direction: str, price: float, atr: float) -> float:
    return price - _TRAILING_ATR_MULT * atr if direction == "BUY" else price + _TRAILING_ATR_MULT * atr


def _reduces_loss(direction: str, new_sl: float, cur_sl: float | None) -> bool:
    """True only when new_sl moves the stop TOWARD price (up for BUY, down for
    SELL) — i.e. it can only ever tighten, never widen, the stop."""
    if cur_sl is None or cur_sl <= 0:
        return True
    return new_sl > cur_sl if direction == "BUY" else new_sl < cur_sl


def _process_trade(db, trade: dict, by_ticket: dict) -> int:
    """Advance one laddered trade. Returns the number of bridge actions taken."""
    L = trade.get("ladder") or {}
    try:
        ticket = int(trade.get("mt5_ticket"))
    except (TypeError, ValueError):
        return 0

    pos = by_ticket.get(ticket)
    if pos is None:
        # Position is gone (hit SL/TP, or closed manually). Retire the ladder;
        # position_stream / the reconciler own the close-out accounting.
        db[COLL_TRADES].update_one({"_id": trade["_id"]}, {"$set": {"ladder.enabled": False}})
        return 0

    direction = (L.get("direction") or pos.get("type") or "").upper()
    price = float(pos.get("currentPrice") or 0.0)
    if price <= 0 or direction not in ("BUY", "SELL"):
        return 0

    entry     = float(L.get("entry") or pos.get("openPrice") or 0.0)
    atr       = float(L.get("atr_at_entry") or 0.0)
    tp_levels = (L.get("tp_levels") or [None, None, None, None])[:4]
    tp_levels = tp_levels + [None] * (4 - len(tp_levels))
    tp_hit    = (L.get("tp_hit") or [False, False, False, False])[:4]
    tp_hit    = tp_hit + [False] * (4 - len(tp_hit))
    sched_by_idx = {int(s["tp_index"]): float(s["lots"]) for s in (L.get("schedule") or [])}

    remaining = float(pos.get("volume") or 0.0)
    cur_sl    = float(pos.get("sl") or 0.0) or None

    updates: dict = {}
    actions = 0

    # ── Partial closes at each newly-reached TP ──────────────────────────────
    for i in range(4):
        tp = tp_levels[i]
        if tp is None or tp_hit[i] or not _hit(direction, price, tp):
            continue
        lots = sched_by_idx.get(i)
        if lots:
            close_lots = round(min(lots, remaining), 2)
            if close_lots >= _LOT_STEP:
                try:
                    bridge_client.close_position(ticket, close_lots, partial=True)
                    remaining = round(remaining - close_lots, 2)
                    actions += 1
                    logger.info(
                        "ladder: ticket=%d partial close %.2f lot at TP%d (%.5f), remaining=%.2f",
                        ticket, close_lots, i + 1, tp, remaining,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ladder: partial close failed ticket=%d: %s", ticket, exc)
                    continue  # leave tp_hit[i] False so it retries next poll
        tp_hit[i] = True
        updates["ladder.tp_hit"] = tp_hit

    # ── Break-even after TP1 ─────────────────────────────────────────────────
    if tp_hit[0] and not L.get("breakeven_done"):
        if _reduces_loss(direction, entry, cur_sl):
            try:
                bridge_client.modify_position(ticket, stop_loss=round(entry, 5))
                cur_sl = entry
                actions += 1
                updates["ladder.breakeven_done"] = True
                logger.info("ladder: ticket=%d SL -> break-even %.5f", ticket, entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ladder: break-even modify failed ticket=%d: %s", ticket, exc)
        else:
            # Stop is already at/beyond break-even (e.g. trailing got there first).
            updates["ladder.breakeven_done"] = True

    # ── ATR trailing after break-even ─────────────────────────────────────────
    # Handoff with the %-trailing manager: %-trailing owns the stop until it
    # reaches break-even; from break-even (TP1 hit, or the stop already at/
    # beyond entry) the ladder's ATR trail takes over so the remaining position
    # stays protected between TP levels — including small lots (0.02) whose
    # schedule has no TP2 partial. Monotonic: the trail only ever tightens.
    trailing_active = bool(L.get("trailing_active"))
    stop_at_breakeven = cur_sl is not None and (
        cur_sl >= entry if direction == "BUY" else cur_sl <= entry
    )
    if (tp_hit[0] or stop_at_breakeven) and not trailing_active:
        trailing_active = True
        updates["ladder.trailing_active"] = True

    if trailing_active and atr > 0:
        new_sl = round(_trail_stop(direction, price, atr), 5)
        if _reduces_loss(direction, new_sl, cur_sl):
            try:
                bridge_client.modify_position(ticket, stop_loss=new_sl)
                actions += 1
                logger.info("ladder: ticket=%d trail SL -> %.5f", ticket, new_sl)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ladder: trail modify failed ticket=%d: %s", ticket, exc)

    if updates:
        db[COLL_TRADES].update_one({"_id": trade["_id"]}, {"$set": updates})
    return actions


def _run_once(db) -> int:
    """One pass over all open laddered trades. Synchronous (bridge client is
    sync); run inside an executor so the event loop is never blocked."""
    try:
        positions = bridge_client.get_positions()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ladder_manager: get_positions failed: %s", exc)
        return 0

    by_ticket: dict = {}
    for p in positions or []:
        try:
            by_ticket[int(p.get("ticket") or 0)] = p
        except (TypeError, ValueError):
            continue

    actions = 0
    cursor = db[COLL_TRADES].find({
        "ladder.enabled": True,
        "mt5_ticket": {"$exists": True},
        "closed_at": {"$exists": False},
    })
    for trade in cursor:
        try:
            actions += _process_trade(db, trade, by_ticket)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ladder_manager: trade %s error: %s", trade.get("_id"), exc)
    return actions


async def ladder_manager_loop() -> None:
    """Background loop. Processes open trades that carry an attached ladder plan
    (attached when lot > 0.01 or tp_ladder_enabled is set). No-op when no
    laddered trades are open."""
    logger.info("Ladder manager loop started (processes open laddered trades).")
    await asyncio.sleep(20)  # let startup settle
    while True:
        interval = _POLL_DEFAULT
        try:
            db = get_database()
            interval = float(crud.get_setting(db, "ladder_poll_seconds") or _POLL_DEFAULT)
            loop = asyncio.get_event_loop()
            moved = await loop.run_in_executor(None, functools.partial(_run_once, db))
            if moved:
                logger.info("ladder_manager: %d action(s) executed.", moved)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ladder_manager loop iteration error: %s", exc)
        await asyncio.sleep(interval)
