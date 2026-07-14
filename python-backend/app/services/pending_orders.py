"""
Pending limit-order placement, monitoring and cancellation.

The orchestrator decides whether a strategy-driven signal should rest as a
pending limit order (entry away from market) instead of a market fill. This
module owns:

  * the limit-vs-market decision helper (`decide_pending_entry`)
  * placement + DB recording (`record_and_place_pending`)
  * cancellation with a recorded reason (`cancel_pending`, `cancel_opposite_pendings`)
  * the per-cycle monitor that fills / expires / cancels pendings
    (`run_pending_order_monitor_once`) and its background loop
    (`pending_order_monitor_loop`).

All bridge calls here are synchronous; the async loop offloads the per-cycle
pass to a thread (mirroring the position-reconciliation loop in main.py).

Cancellation reasons (stored on the pending_orders doc as `cancel_reason`,
surfaced in the Trades report):
  * ENSEMBLE_REVERSAL          — ensemble resolved an opposite-direction signal
  * NEWS_OPPOSED               — opposing news-driven signal arrived
  * MISSED_ENTRY_TP_PROGRESS   — price ran toward TP without filling (missed entry)
  * MAX_AGE                    — exceeded the configured max age
  * BROKER_REMOVED             — order left the broker book (manual/expiry) unfilled
  * SUPERSEDED_HIGHER_CONFIDENCE — a fresher same-direction signal with higher
                                   confidence replaced this resting order
"""
import asyncio
import logging
from datetime import datetime

from pymongo.database import Database

from .. import crud
from ..db import get_database
from ..services.bridge_client import (
    bridge_client,
    BridgeUnavailableError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_pending_settings(db: Database) -> dict:
    """Read + type-coerce the pending-order settings (all stored as strings)."""
    def _f(key: str, default: float) -> float:
        try:
            return float(crud.get_setting(db, key))
        except (TypeError, ValueError):
            return default

    def _b(key: str, default: bool) -> bool:
        val = crud.get_setting(db, key)
        if val is None:
            return default
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    return {
        "enabled":               _b("pending_orders_enabled", False),
        "min_distance_atr":      _f("pending_entry_min_distance_atr", 0.25),
        "entry_offset_atr":      _f("pending_entry_offset_atr", 0.5),
        "miss_entry_fraction":   _f("pending_miss_entry_fraction", 0.40),
        "monitor_interval":      _f("pending_order_monitor_interval_seconds", 15),
        "max_age_minutes":       _f("pending_order_max_age_minutes", 0),
        "cancel_on_opposite":    _b("pending_cancel_on_opposite_signal", True),
        "cancel_on_opposing_news": _b("pending_cancel_on_opposing_news", True),
    }


# ---------------------------------------------------------------------------
# Decision: limit vs market
# ---------------------------------------------------------------------------

def decide_pending_entry(
    direction: str,
    price: float,
    proposed_entry: float | None,
    atr: float | None,
    settings: dict,
) -> float | None:
    """
    Return a limit price if this signal should rest as a pending limit order,
    else None (caller falls back to a market order).

    A BUY_LIMIT rests *below* market and a SELL_LIMIT rests *above* market.
    If the strategy supplied a favourable `proposed_entry` (below market for a
    BUY, above for a SELL) we use it; otherwise we synthesise a pullback entry
    `entry_offset_atr * atr` away from market. Either way the chosen limit must
    sit at least `min_distance_atr * atr` from price on the favourable side,
    else we return None so the caller market-fills.
    """
    if not settings.get("enabled"):
        return None
    if not price or not atr or atr <= 0:
        return None

    min_dist = settings["min_distance_atr"] * atr
    offset = settings["entry_offset_atr"] * atr

    if direction == "BUY":
        # Prefer a strategy entry strictly below market; else pull back by offset.
        if proposed_entry is not None and proposed_entry < price:
            limit = proposed_entry
        else:
            limit = price - offset
        if limit < price and (price - limit) >= min_dist:
            return round(limit, 6)
    elif direction == "SELL":
        if proposed_entry is not None and proposed_entry > price:
            limit = proposed_entry
        else:
            limit = price + offset
        if limit > price and (limit - price) >= min_dist:
            return round(limit, 6)
    return None


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def record_and_place_pending(
    db: Database,
    *,
    symbol: str,
    direction: str,
    limit_price: float,
    lot_size: float,
    stop_loss: float | None,
    tp1: float | None,
    tp2: float | None,
    strategy_name: str | None,
    params_version: int | None,
    contributing_news_ids: list | None,
    ensemble_decision_id: int | None,
    confidence: float | None = None,
) -> dict | None:
    """
    Place a BUY_LIMIT/SELL_LIMIT via the bridge and record it in the
    pending_orders collection. Returns a small status dict, or None if the
    bridge placement failed (caller may then fall back to market).
    """
    order_type = "BUY_LIMIT" if direction == "BUY" else "SELL_LIMIT"
    try:
        resp = bridge_client.place_limit_order(
            symbol=symbol,
            order_type=order_type,
            volume=lot_size,
            price=limit_price,
            stop_loss=stop_loss,
            take_profit=tp1,
        )
    except Exception as exc:
        logger.warning(
            "Pending limit placement failed for %s %s @ %.5f: %s",
            symbol, order_type, limit_price, exc,
        )
        return None

    ticket: int | None = None
    try:
        raw_ticket = resp.get("orderId") or resp.get("ticket")
        if raw_ticket is not None:
            ticket = int(raw_ticket)
    except (ValueError, TypeError):
        ticket = None

    record = crud.create_pending_order(db, {
        "symbol": symbol,
        "direction": direction,
        "order_type": order_type,
        "limit_price": limit_price,
        "lot_size": lot_size,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "mt5_ticket": ticket,
        "strategy_name": strategy_name,
        "confidence": confidence,
        "params_version": params_version,
        "contributing_news_ids": list(contributing_news_ids or []),
        "ensemble_decision_id": ensemble_decision_id,
    })
    logger.info(
        "Placed pending %s for %s @ %.5f ticket=%s (sl=%s tp1=%s)",
        order_type, symbol, limit_price, ticket, stop_loss, tp1,
    )
    return {
        "status": "PENDING_PLACED",
        "order_type": order_type,
        "symbol": symbol,
        "limit_price": limit_price,
        "ticket": ticket,
        "pending_order_id": record.id,
    }


def _replacement_rank(
    direction: str,
    confidence: float | None,
    limit_price: float | None,
    tp1: float | None,
    stop_loss: float | None,
) -> tuple[float, float, float, float]:
    """
    Build an orientation-normalised ranking key for one pending order so that
    a plain tuple comparison (higher is better) picks the best entry.

    Components, in priority order:
      1. confidence — ensemble-combined confidence (missing → 0.0, so any
         confidence-tagged signal outranks an untagged legacy order).
      2. entry — SELL prefers a higher limit, BUY a lower limit; we negate the
         BUY price so "higher tuple" is always better.
      3. reward — signed distance entry→tp1 in the trade's favour (bigger TP
         target = more room to run). Missing tp1 → 0.0.
      4. risk — negated stop distance so a TIGHTER stop ranks higher. Missing
         stop_loss → treated as an infinitely wide stop (worst).
    """
    conf = confidence if confidence is not None else 0.0
    price = limit_price if limit_price is not None else 0.0

    if direction == "SELL":
        entry_rank = price                       # higher sell price is better
        reward = (price - tp1) if tp1 is not None else 0.0        # tp1 below entry
        risk = (stop_loss - price) if stop_loss is not None else float("inf")  # sl above
    else:  # BUY
        entry_rank = -price                      # lower buy price is better
        reward = (tp1 - price) if tp1 is not None else 0.0        # tp1 above entry
        risk = (price - stop_loss) if stop_loss is not None else float("inf")  # sl below

    return (conf, entry_rank, reward, -risk)


def place_or_replace_pending(
    db: Database,
    *,
    symbol: str,
    direction: str,
    limit_price: float,
    lot_size: float,
    stop_loss: float | None,
    tp1: float | None,
    tp2: float | None,
    strategy_name: str | None,
    params_version: int | None,
    contributing_news_ids: list | None,
    ensemble_decision_id: int | None,
    confidence: float | None = None,
) -> dict | None:
    """
    Enforce **at most one active pending per symbol+direction**.

    On each fresh same-direction signal we rank the new signal against the
    resting order(s) with a lexicographic key
    `(confidence, entry, reward, risk)` where every component is oriented so
    "higher is better":

      * confidence — the ensemble-combined confidence (Σ weightᵢ·confidenceᵢ for
        the winning direction); higher wins.
      * entry — better fill price: for a SELL the higher limit, for a BUY the
        lower limit.
      * reward (TP) — larger distance from entry to tp1 (more profit room).
      * risk (SL) — tighter stop, i.e. smaller distance from entry to the stop.

    Ties fall through to the next component. If an existing same-direction
    pending is at least as good as the new signal on this key, keep it and skip
    placement (returns PENDING_KEPT). Otherwise cancel every existing
    same-direction pending and place the new one (PENDING_PLACED, tagged
    PENDING_REPLACED when it displaced an older order).

    This prevents the book from stacking dozens of near-identical limit orders
    in one direction; only the single best entry rests at any time.
    """
    existing = [
        po for po in crud.get_active_pending_orders(db, symbol)
        if po.direction == direction
    ]

    new_key = _replacement_rank(direction, confidence, limit_price, tp1, stop_loss)

    # Keep a resting order if it ranks at least as high as the new signal.
    for po in existing:
        po_key = _replacement_rank(
            direction, po.confidence, po.limit_price, po.tp1, po.stop_loss
        )
        if new_key <= po_key:  # existing is at least as good → keep it
            logger.info(
                "Keeping resting %s pending ticket=%s rank=%s over new rank=%s; "
                "skipping duplicate placement",
                po.order_type, po.mt5_ticket, po_key, new_key,
            )
            return {
                "status": "PENDING_KEPT",
                "symbol": symbol,
                "direction": direction,
                "kept_ticket": po.mt5_ticket,
                "kept_confidence": po_key[0],
            }

    # New signal beats every resting one on the rank key — cancel them.
    replaced = 0
    for po in existing:
        if cancel_pending(db, po, "SUPERSEDED_HIGHER_CONFIDENCE"):
            replaced += 1

    result = record_and_place_pending(
        db,
        symbol=symbol,
        direction=direction,
        limit_price=limit_price,
        lot_size=lot_size,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        strategy_name=strategy_name,
        params_version=params_version,
        contributing_news_ids=contributing_news_ids,
        ensemble_decision_id=ensemble_decision_id,
        confidence=confidence,
    )
    if result is not None and replaced:
        result["status"] = "PENDING_REPLACED"
        result["replaced_count"] = replaced
    return result


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def cancel_pending(db: Database, pending_order, reason: str) -> bool:
    """Cancel one pending order at the broker and record the reason. Returns
    True on success. Safe to call from a sync (executor) context."""
    ticket = pending_order.mt5_ticket
    try:
        if ticket is not None:
            bridge_client.cancel_order(int(ticket))
    except Exception as exc:
        logger.warning(
            "Bridge cancel failed for pending ticket=%s (%s): %s",
            ticket, reason, exc,
        )
        return False

    crud.resolve_pending_order(db, int(pending_order.id), "CANCELLED", reason)
    try:
        from .alerts import notify_trade_operation
        notify_trade_operation(
            db, "cancel",
            symbol=pending_order.symbol,
            direction=pending_order.order_type,
            ticket=int(ticket) if ticket is not None else None,
            trade_id=int(pending_order.id),
            extra={"reason": reason},
        )
    except Exception:
        pass
    logger.info(
        "Cancelled pending %s %s ticket=%s reason=%s",
        pending_order.symbol, pending_order.order_type, ticket, reason,
    )
    return True


def cancel_opposite_pendings(
    db: Database, symbol: str, keep_direction: str, reason: str
) -> int:
    """Cancel all active pendings for `symbol` whose direction opposes
    `keep_direction`. Returns the number cancelled. Used by the orchestrator
    when the ensemble/news resolves a fresh signal."""
    cancelled = 0
    for po in crud.get_active_pending_orders(db, symbol):
        if po.direction and po.direction != keep_direction:
            if cancel_pending(db, po, reason):
                cancelled += 1
    return cancelled


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

def _miss_entry_progress(po, price: float) -> float | None:
    """Fraction (0..1+) of the limit→tp1 distance that price has travelled
    in-favour without filling. None if not computable."""
    tp1 = po.tp1
    limit = po.limit_price
    if tp1 is None or limit is None:
        return None
    if po.direction == "BUY":
        span = tp1 - limit
        if span <= 0:
            return None
        return (price - limit) / span
    else:  # SELL
        span = limit - tp1
        if span <= 0:
            return None
        return (limit - price) / span


def run_pending_order_monitor_once(db: Database) -> dict:
    """
    One monitor pass. For each active pending order:
      * filled  → log a trade + mark FILLED
      * gone from broker book (unfilled) → mark EXPIRED (BROKER_REMOVED)
      * still resting → evaluate miss-entry and max-age cancels
    Returns a small summary dict.
    """
    summary = {"checked": 0, "filled": 0, "cancelled": 0, "expired": 0, "errors": 0}
    settings = get_pending_settings(db)

    active = crud.get_active_pending_orders(db)
    if not active:
        return summary

    # Live broker state, keyed by ticket.
    try:
        positions = bridge_client.get_positions() or []
        orders = bridge_client.get_orders() or []
    except BridgeUnavailableError:
        logger.debug("Pending monitor: bridge unavailable; skipping pass")
        return summary
    except Exception as exc:
        logger.warning("Pending monitor: failed to read broker state: %s", exc)
        return summary

    pos_by_ticket = {}
    for p in positions:
        try:
            pos_by_ticket[int(p.get("ticket"))] = p
        except (ValueError, TypeError):
            continue
    order_tickets = set()
    for o in orders:
        try:
            order_tickets.add(int(o.get("ticket")))
        except (ValueError, TypeError):
            continue

    now = datetime.utcnow()

    for po in active:
        summary["checked"] += 1
        ticket = po.mt5_ticket
        try:
            # 1. Filled — pending became a position (MT5 keeps the same ticket).
            if ticket is not None and int(ticket) in pos_by_ticket:
                pos = pos_by_ticket[int(ticket)]
                fill_price = float(pos.get("openPrice") or po.limit_price)
                crud.log_trade(db, {
                    "symbol": po.symbol,
                    "direction": po.direction,
                    "entry_price": fill_price,
                    "stop_loss": po.stop_loss,
                    "take_profit": po.tp1,
                    "lot_size": po.lot_size,
                    "result": "OPEN",
                    "strategy_name": po.strategy_name or "ENSEMBLE",
                    "params_version": po.params_version,
                    "mt5_ticket": int(ticket),
                    "contributing_news_ids": po.contributing_news_ids,
                })
                crud.resolve_pending_order(db, int(po.id), "FILLED")
                summary["filled"] += 1
                logger.info(
                    "Pending %s %s ticket=%s FILLED @ %.5f",
                    po.symbol, po.order_type, ticket, fill_price,
                )
                continue

            # 2. Gone from the broker book without filling → broker removed.
            if ticket is not None and int(ticket) not in order_tickets:
                crud.resolve_pending_order(db, int(po.id), "EXPIRED", "BROKER_REMOVED")
                summary["expired"] += 1
                continue

            # 3. Still resting — evaluate cancel conditions.
            # Max-age.
            if settings["max_age_minutes"] > 0 and po.created_at is not None:
                age_min = (now - po.created_at).total_seconds() / 60.0
                if age_min >= settings["max_age_minutes"]:
                    if cancel_pending(db, po, "MAX_AGE"):
                        summary["cancelled"] += 1
                    continue

            # Miss-entry: price ran toward TP without filling.
            try:
                tick = bridge_client.get_tick(po.symbol)
                price = (float(tick.get("bid", 0)) + float(tick.get("ask", 0))) / 2.0
            except Exception:
                price = 0.0
            if price > 0:
                progress = _miss_entry_progress(po, price)
                if progress is not None and progress >= settings["miss_entry_fraction"]:
                    if cancel_pending(db, po, "MISSED_ENTRY_TP_PROGRESS"):
                        summary["cancelled"] += 1
                    continue

        except Exception as exc:
            summary["errors"] += 1
            logger.warning("Pending monitor: error on order id=%s: %s", po.id, exc)

    return summary


async def pending_order_monitor_loop():
    """Background loop — inert unless `pending_orders_enabled` is true. Mirrors
    the position-reconciliation loop: offloads the sync pass to a thread."""
    await asyncio.sleep(20)  # let the bridge warm up

    while True:
        interval = 15.0
        try:
            db = get_database()
            settings = get_pending_settings(db)
            interval = max(5.0, settings["monitor_interval"])

            if not settings["enabled"]:
                await asyncio.sleep(interval)
                continue

            if not bridge_client.is_available:
                await asyncio.sleep(interval)
                continue

            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(None, run_pending_order_monitor_once, db)
            if summary.get("filled") or summary.get("cancelled") or summary.get("expired"):
                logger.info(
                    "Pending monitor: checked=%d filled=%d cancelled=%d expired=%d errors=%d",
                    summary["checked"], summary["filled"], summary["cancelled"],
                    summary["expired"], summary["errors"],
                )
        except Exception as exc:
            logger.warning("Pending order monitor loop error: %s", exc)

        await asyncio.sleep(interval)
