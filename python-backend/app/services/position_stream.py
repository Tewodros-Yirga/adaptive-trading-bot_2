"""
services/position_stream.py — Position Status Streaming Service

Polls the MT5 bridge every POLL_INTERVAL seconds and compares against the
previous snapshot to detect:

  - NEW positions opened (by MT5 directly or by the bot)
  - CLOSED positions (TP/SL hit, manual close, margin call)
  - PARTIAL closes (volume decreased since last snapshot)
  - MODIFIED positions (SL/TP changed)

On each detected event:
  1. Broadcasts a WebSocket event to all connected frontend clients.
  2. For CLOSE / PARTIAL events: auto-reconciles the MongoDB trade record
     via crud.close_trade() — so trades closed by TP/SL are always reflected
     in the DB even without the reconciler loop.

The service is resilient to bridge unavailability: when the circuit breaker
is OPEN it skips the poll silently and tries again after the next interval.

Usage (in main.py lifespan):
    from .services.position_stream import start_position_stream
    bg_tasks.append(asyncio.create_task(start_position_stream()))
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# How often to poll MT5 bridge for position changes (seconds)
POLL_INTERVAL: float = 5.0

# Minimum volume change fraction to be considered a partial close
_PARTIAL_CLOSE_THRESHOLD = 0.001


def _pos_key(pos: dict) -> int:
    """Unique identifier for a position (MT5 ticket number)."""
    return int(pos.get("ticket") or pos.get("orderId") or 0)


def _pos_volume(pos: dict) -> float:
    return float(pos.get("volume") or pos.get("lots") or 0.0)


async def _broadcast(event_type: str, data: dict) -> None:
    try:
        from ..routers.websocket import broadcast
        await broadcast(event_type, data)
    except Exception as exc:
        logger.debug("WebSocket broadcast failed: %s", exc)


async def _reconcile_closed_trade(
    db,
    ticket: int,
    pos_snapshot: dict,
    reason: str,
) -> None:
    """
    Find the MongoDB trade with this mt5_ticket and close it.
    Fetches deal history from the bridge to get exact PnL + exit price.
    """
    from .. import crud
    from ..db import COLL_TRADES
    from ..services.bridge_client import bridge_client

    trade_doc = db[COLL_TRADES].find_one({"mt5_ticket": ticket, "closed_at": {"$exists": False}})
    if not trade_doc:
        # Fallback: match by symbol+direction for the most recently opened open trade.
        # Build symbol variants to handle broker suffixes (XAUUSDm ↔ XAUUSD).
        raw_symbol = (pos_snapshot.get("symbol") or "").upper()
        direction = (pos_snapshot.get("type") or "").upper()
        if raw_symbol and direction:
            sym_variants = [raw_symbol]
            if raw_symbol.endswith("M"):
                sym_variants.append(raw_symbol[:-1])
            else:
                sym_variants.append(raw_symbol + "M")
            if raw_symbol.endswith("m"):
                sym_variants.append(raw_symbol[:-1])
            else:
                sym_variants.append(raw_symbol + "m")

            trade_doc = db[COLL_TRADES].find_one(
                {
                    "symbol": {"$in": sym_variants},
                    "direction": direction,
                    "closed_at": {"$exists": False},
                    "result": {"$in": ["OPEN", None]},
                },
                sort=[("opened_at", -1)],  # most recent first
            )

    if not trade_doc:
        logger.debug(
            "position_stream: no open DB trade found for closed ticket=%d", ticket
        )
        return

    trade_id = trade_doc["_id"]

    # Try to get deal history for accurate PnL
    pnl = 0.0
    exit_price = float(pos_snapshot.get("openPrice") or pos_snapshot.get("price") or 0.0)
    result = "LOSS"

    try:
        deals = bridge_client.get_deals(ticket=ticket, lookback_days=7)
        # Find the closing deal (entry=1 means DEAL_ENTRY_OUT)
        closing_deals = [d for d in deals if d.get("entry") == 1]
        if closing_deals:
            close_deal = closing_deals[-1]
            profit = float(close_deal.get("profit") or 0.0)
            swap = float(close_deal.get("swap") or 0.0)
            commission = float(close_deal.get("commission") or 0.0)
            pnl = profit + swap + commission
            exit_price = float(close_deal.get("price") or exit_price)
        elif deals:
            # fallback: last deal
            last = deals[-1]
            pnl = float(last.get("profit") or 0.0)
            exit_price = float(last.get("price") or exit_price)
    except Exception as exc:
        logger.warning("position_stream: could not fetch deals for ticket=%d: %s", ticket, exc)

    result = "WIN" if pnl > 0 else "LOSS"

    try:
        crud.close_trade(
            db,
            trade_id=trade_id,
            exit_price=exit_price,
            pnl=pnl,
            result=result,
            mt5_ticket=ticket,
        )
        logger.info(
            "position_stream: auto-closed trade db_id=%s ticket=%d "
            "exit=%.5f pnl=%.2f result=%s reason=%s",
            trade_id, ticket, exit_price, pnl, result, reason,
        )

        # Trade-close learning: strategy score feedback + news intelligence.
        try:
            from ..services.score_feedback import run_trade_close_hooks
            from ..models import Trade

            updated_doc = db[COLL_TRADES].find_one({"_id": trade_id})
            if updated_doc:
                trade = Trade.from_doc(updated_doc)
                run_trade_close_hooks(db, trade)
        except Exception as exc:
            logger.debug("position_stream: trade-close hooks failed: %s", exc)

    except Exception as exc:
        logger.error(
            "position_stream: failed to close trade db_id=%s ticket=%d: %s",
            trade_id, ticket, exc,
        )


async def _handle_partial_close(
    db,
    ticket: int,
    old_pos: dict,
    new_pos: dict,
) -> None:
    """
    Log a partial close event. The position remains open so we just update
    the lot_size in MongoDB and broadcast the event.
    """
    from .. import crud
    from ..db import COLL_TRADES

    old_vol = _pos_volume(old_pos)
    new_vol = _pos_volume(new_pos)
    closed_vol = round(old_vol - new_vol, 8)

    logger.info(
        "position_stream: PARTIAL CLOSE ticket=%d %s → %s (closed=%.4f)",
        ticket, old_vol, new_vol, closed_vol,
    )

    # Update lot_size in MongoDB
    db[COLL_TRADES].update_one(
        {"mt5_ticket": ticket, "closed_at": {"$exists": False}},
        {"$set": {"lot_size": new_vol}},
    )

    await _broadcast(
        "position_partial_close",
        {
            "ticket": ticket,
            "symbol": new_pos.get("symbol"),
            "direction": new_pos.get("type"),
            "old_volume": old_vol,
            "new_volume": new_vol,
            "closed_volume": closed_vol,
            "current_profit": new_pos.get("profit"),
            "sl": new_pos.get("sl"),
            "tp": new_pos.get("tp"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _process_snapshot_diff(
    db,
    prev: dict[int, dict],
    curr: dict[int, dict],
) -> None:
    """
    Compare previous and current position snapshots and emit events for
    any changes detected.
    """
    prev_tickets = set(prev.keys())
    curr_tickets = set(curr.keys())

    # ── New positions ───────────────────────────────────────────────────
    for ticket in curr_tickets - prev_tickets:
        pos = curr[ticket]
        logger.info(
            "position_stream: NEW position ticket=%d %s %s %.4f @ %.5f",
            ticket,
            pos.get("type", "?"),
            pos.get("symbol", "?"),
            _pos_volume(pos),
            float(pos.get("openPrice") or 0),
        )
        # Backfill mt5_ticket on the most recent ticketless DB trade for this
        # symbol+direction so reconciler can match by ticket going forward.
        try:
            from ..db import COLL_TRADES
            symbol_up = (pos.get("symbol") or "").upper()
            direction_up = (pos.get("type") or "").upper()
            if symbol_up and direction_up:
                open_time_raw = pos.get("openTime") or pos.get("time")
                if open_time_raw is not None:
                    if isinstance(open_time_raw, (int, float)):
                        from datetime import datetime as _dt
                        pos_open_dt = _dt.utcfromtimestamp(open_time_raw)
                    else:
                        from datetime import datetime as _dt
                        pos_open_dt = _dt.fromisoformat(
                            str(open_time_raw).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    from datetime import timedelta as _td
                    cutoff = pos_open_dt - _td(minutes=5)
                    ticketless = db[COLL_TRADES].find_one(
                        {
                            "symbol": {"$in": [symbol_up, symbol_up.rstrip("M"), symbol_up + "m",
                                               symbol_up.rstrip("m"), symbol_up + "M"]},
                            "direction": direction_up,
                            "closed_at": {"$exists": False},
                            "mt5_ticket": {"$exists": False},
                            "opened_at": {"$gte": cutoff},
                        },
                        sort=[("opened_at", -1)],
                    )
                    if ticketless:
                        db[COLL_TRADES].update_one(
                            {"_id": ticketless["_id"]},
                            {"$set": {"mt5_ticket": int(ticket)}},
                        )
                        logger.info(
                            "position_stream: backfilled mt5_ticket=%d on trade db_id=%s",
                            ticket, ticketless["_id"],
                        )
        except Exception as _bf_exc:
            logger.debug("position_stream: mt5_ticket backfill failed for ticket=%d: %s", ticket, _bf_exc)

        await _broadcast(
            "position_opened",
            {
                "ticket": ticket,
                "symbol": pos.get("symbol"),
                "direction": pos.get("type"),
                "volume": _pos_volume(pos),
                "open_price": pos.get("openPrice"),
                "sl": pos.get("sl"),
                "tp": pos.get("tp"),
                "profit": pos.get("profit"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # ── Closed positions ────────────────────────────────────────────────
    for ticket in prev_tickets - curr_tickets:
        pos = prev[ticket]
        logger.info(
            "position_stream: CLOSED position ticket=%d %s %s",
            ticket, pos.get("type", "?"), pos.get("symbol", "?"),
        )
        # Auto-reconcile the MongoDB trade — call directly (already in async context).
        try:
            await _reconcile_closed_trade(db, ticket, pos, "stream_detected_close")
        except Exception as _rec_exc:
            logger.warning("position_stream: reconcile error for ticket=%d: %s", ticket, _rec_exc)
        await _broadcast(
            "position_closed",
            {
                "ticket": ticket,
                "symbol": pos.get("symbol"),
                "direction": pos.get("type"),
                "volume": _pos_volume(pos),
                "open_price": pos.get("openPrice"),
                "last_profit": pos.get("profit"),
                "close_reason": "tp_sl_or_manual",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # ── Modified / partially closed positions ────────────────────────────
    for ticket in prev_tickets & curr_tickets:
        old = prev[ticket]
        new = curr[ticket]

        old_vol = _pos_volume(old)
        new_vol = _pos_volume(new)

        if old_vol - new_vol > _PARTIAL_CLOSE_THRESHOLD:
            # Volume decreased — partial close
            await _handle_partial_close(db, ticket, old, new)

        elif old.get("sl") != new.get("sl") or old.get("tp") != new.get("tp"):
            # SL/TP modified
            await _broadcast(
                "position_modified",
                {
                    "ticket": ticket,
                    "symbol": new.get("symbol"),
                    "direction": new.get("type"),
                    "old_sl": old.get("sl"),
                    "new_sl": new.get("sl"),
                    "old_tp": old.get("tp"),
                    "new_tp": new.get("tp"),
                    "profit": new.get("profit"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        # Always broadcast profit updates for open positions (throttle: only on changes)
        old_profit = old.get("profit") or 0.0
        new_profit = new.get("profit") or 0.0
        if abs(new_profit - old_profit) > 0.01:
            await _broadcast(
                "position_profit_update",
                {
                    "ticket": ticket,
                    "symbol": new.get("symbol"),
                    "direction": new.get("type"),
                    "volume": new_vol,
                    "profit": new_profit,
                    "sl": new.get("sl"),
                    "tp": new.get("tp"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )


async def _async_reconcile_in_thread(db, ticket: int, pos: dict, reason: str) -> None:
    """Helper to call the async reconcile from a thread executor context."""
    await _reconcile_closed_trade(db, ticket, pos, reason)


def _truthy(v) -> bool:
    return str(v or "").lower() in ("1", "true", "yes", "on")


async def _run_sse_stream(db) -> None:
    """Consume the bridge's /stream/positions SSE (item 4, opt-in).

    Drives the SAME diff/reconcile/broadcast path as polling, but reacts to
    bridge pushes instead of a fixed 5s poll. Maintains a local prev-snapshot
    from each event's `positions` array so all existing logic is reused.
    Raises on connection loss so the caller can reconnect.
    """
    import json as _json
    import httpx
    from ..config import settings

    base = settings.mt_bridge_url.rstrip("/")
    headers = {"X-Bridge-Secret": settings.mt_bridge_secret}
    if settings.mt_bridge_hf_token:
        headers["Authorization"] = f"Bearer {settings.mt_bridge_hf_token}"
    url = f"{base}/stream/positions"

    prev_snapshot: dict[int, dict] = {}
    # read=None → never time out the long-lived stream
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            logger.info("position_stream: SSE connected to %s", url)
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    payload = _json.loads(line[5:].strip())
                except Exception:
                    continue
                positions = payload.get("positions")
                if positions is None:
                    continue
                curr_snapshot = {_pos_key(p): p for p in positions if _pos_key(p)}
                if curr_snapshot != prev_snapshot:
                    await _process_snapshot_diff(db, prev_snapshot, curr_snapshot)
                prev_snapshot = curr_snapshot


async def start_position_stream() -> None:
    """
    Background asyncio task — emits position change events.

    Default: polls the bridge every POLL_INTERVAL seconds. When the AppSetting
    ``use_sse_position_stream`` is truthy, consumes the bridge SSE stream
    instead (lower latency). Starts after a 30-second delay to let the bridge
    warm up.
    """
    await asyncio.sleep(30)

    from ..db import get_database
    from ..services.bridge_client import bridge_client, BridgeUnavailableError
    from .. import crud

    # ── Item 4: opt-in SSE mode (default OFF → unchanged polling behaviour) ─
    try:
        if _truthy(crud.get_setting(get_database(), "use_sse_position_stream")):
            logger.info("position_stream: started in SSE mode")
            while True:
                try:
                    await _run_sse_stream(get_database())
                except asyncio.CancelledError:
                    logger.info("position_stream: cancelled")
                    return
                except Exception as exc:
                    logger.warning("position_stream: SSE dropped (%s) — reconnecting in 5s", exc)
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("position_stream: SSE setup failed (%s) — falling back to polling", exc)

    logger.info("position_stream: started (poll_interval=%.0fs)", POLL_INTERVAL)

    prev_snapshot: dict[int, dict] = {}
    consecutive_errors = 0

    while True:
        try:
            db = get_database()

            if not bridge_client.is_available:
                # Circuit breaker open — skip silently
                await asyncio.sleep(POLL_INTERVAL)
                continue

            loop = asyncio.get_event_loop()
            try:
                raw_positions: list[dict] = await loop.run_in_executor(
                    None, bridge_client.get_positions
                )
            except BridgeUnavailableError:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    logger.warning("position_stream: poll error: %s", exc)
                await asyncio.sleep(POLL_INTERVAL)
                continue

            consecutive_errors = 0
            curr_snapshot: dict[int, dict] = {
                _pos_key(p): p for p in raw_positions if _pos_key(p)
            }

            if curr_snapshot != prev_snapshot:
                await _process_snapshot_diff(db, prev_snapshot, curr_snapshot)

            prev_snapshot = curr_snapshot

        except asyncio.CancelledError:
            logger.info("position_stream: cancelled")
            return
        except Exception as exc:
            logger.error("position_stream: unexpected error: %s", exc, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)