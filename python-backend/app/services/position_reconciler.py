"""
python-backend/app/services/position_reconciler.py

MT5 Position Reconciliation Service
====================================

Detects "ghost" open trades in MongoDB — positions that were closed externally
by the MT5 terminal (stop-loss hit, take-profit hit, margin call, manual close)
but whose MongoDB record was never updated because the backend only observes
the trade at open-time.

Algorithm
---------
1. Fetch all live MT5 positions via the bridge GET /positions endpoint.
2. Fetch all "open" trades in MongoDB (no closed_at).
3. PRIMARY PASS — trades WITH an mt5_ticket:
   Build a set of live MT5 ticket IDs. For each DB-open trade whose ticket is
   NOT in the live set, the position has been closed externally.

4. SECONDARY PASS — trades WITHOUT an mt5_ticket (opened_at within last 24h):
   Attempt to match to a live MT5 position by symbol + direction + open-time
   proximity (≤ 5 minutes). If matched, write the mt5_ticket back to the
   trade document so future cycles can use the primary pass.

5. For each ghost detected (primary pass):
   a. Attempt to fetch deal history from GET /deals/{ticket}.
   b. Extract the closing deal (entry=1, i.e. DEAL_ENTRY_OUT).
   c. Calculate PnL = profit + swap + commission (if available).
   d. Determine result: WIN if PnL > 0, LOSS if PnL <= 0.
   e. Call crud.close_trade() to persist the closure.
   f. Run trade-close hooks (score feedback + news learning).

6. Log all actions. If bridge is unreachable, skip cycle without crashing.

This runs as a background asyncio loop in the backend (every 60s by default).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database

logger = logging.getLogger(__name__)

# MT5 deal entry type constant — 1 = DEAL_ENTRY_OUT (closing deal)
_DEAL_ENTRY_OUT = 1

# Tolerance for secondary match: open-time difference must be within this window
_OPEN_TIME_TOLERANCE_SECS = 300  # 5 minutes

# How far back to look for ticketless open trades in the secondary pass
_SECONDARY_PASS_LOOKBACK_HOURS = 24


def _get_open_trades_with_tickets(db: Database) -> list[dict]:
    """
    Return all MongoDB trades that are 'open' (no closed_at) and have an
    mt5_ticket so they can be reconciled against live MT5 positions via the
    primary pass.
    """
    from ..db import COLL_TRADES
    docs = list(
        db[COLL_TRADES].find(
            {
                "closed_at": {"$exists": False},
                "mt5_ticket": {"$exists": True, "$ne": None},
            }
        )
    )
    return docs


def _get_open_trades_without_tickets(db: Database) -> list[dict]:
    """
    Return open trades (no closed_at, no mt5_ticket) opened within the last
    ``_SECONDARY_PASS_LOOKBACK_HOURS`` hours.  These are candidates for the
    secondary symbol+direction+time-proximity matching pass.
    """
    from ..db import COLL_TRADES
    cutoff = datetime.utcnow() - timedelta(hours=_SECONDARY_PASS_LOOKBACK_HOURS)
    docs = list(
        db[COLL_TRADES].find(
            {
                "closed_at": {"$exists": False},
                "$or": [
                    {"mt5_ticket": {"$exists": False}},
                    {"mt5_ticket": None},
                ],
                "opened_at": {"$gte": cutoff},
            }
        )
    )
    return docs


def _extract_close_deal(deals: list[dict]) -> dict | None:
    """
    From a list of deal records for a position, find the closing deal.
    The closing deal has entry=1 (DEAL_ENTRY_OUT).
    If there are multiple, return the last one (most recent close).
    """
    closing = [d for d in deals if d.get("entry") == _DEAL_ENTRY_OUT]
    if not closing:
        # Fallback: if no entry-out deal found, return the last deal with non-zero profit
        with_profit = [d for d in deals if d.get("profit") is not None]
        return with_profit[-1] if with_profit else (deals[-1] if deals else None)
    return closing[-1]


def _trigger_trade_close_hooks(db: Database, trade_id: int, trade_result: str) -> None:
    """
    Run trade-close learning hooks after a ghost trade is closed: strategy
    score feedback + news intelligence learning.
    Silently skips if the trade can't be loaded.
    """
    try:
        from ..services.score_feedback import run_trade_close_hooks
        from ..db import COLL_TRADES
        from ..models import Trade

        trade_doc = db[COLL_TRADES].find_one({"_id": trade_id})
        if not trade_doc:
            return

        trade = Trade.from_doc(trade_doc)
        run_trade_close_hooks(db, trade)
        logger.info(
            "Position reconciler: trade-close hooks ran for ghost trade db_id=%s result=%s",
            trade_id, trade_result,
        )
    except Exception as exc:
        logger.warning(
            "Position reconciler: trade-close hooks failed for trade %s: %s",
            trade_id, exc,
        )


def _num(v):
    """Coerce a broker numeric field to float, or None if not parseable."""
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _setting_float(db: Database, key: str, default: float) -> float:
    """Read a float AppSetting via crud, falling back to ``default``."""
    from .. import crud
    try:
        raw = crud.get_setting(db, key)
        if raw is not None and str(raw).strip() != "":
            return float(raw)
    except Exception:
        pass
    return default


def _parse_position_open_dt(pos: dict) -> datetime | None:
    """Parse an MT5 position's open time to naive UTC, or None if unavailable."""
    raw = pos.get("openTime") or pos.get("time")
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.utcfromtimestamp(float(raw))
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    except Exception:
        return None


def _open_ticket_set(db: Database) -> set[int]:
    """Tickets of all currently-open DB trades — used to detect orphan positions
    (live positions with no backing trade record)."""
    tickets: set[int] = set()
    for t in _get_open_trades_with_tickets(db):
        try:
            tickets.add(int(t["mt5_ticket"]))
        except (TypeError, ValueError, KeyError):
            continue
    return tickets


def adopt_orphan_position(
    db: Database, pos: dict, protective_sl_pct: float, summary: dict
) -> bool:
    """
    Adopt a single live MT5 position that has no open DB trade backing it and put
    it under full bot management, so *every* position on the connected account is
    the bot's responsibility:

      1. Record it as an ``ADOPTED`` open trade (idempotent — see
         ``crud.adopt_orphan_trade``).
      2. If it has no stop-loss, set a protective SL at ``protective_sl_pct`` on
         the safe side and push it to the broker.
      3. Attach a ladder plan (R-multiple TPs synthesised from ``R = |entry-sl|``
         when the position has none) so the existing ladder_manager partial-
         closes, moves to break-even, and ATR-trails it exactly like a
         bot-opened trade.

    Human-set SL/TP are respected — they are only ever set when missing.

    Returns True when a new adoption record was created.
    """
    from ..services.bridge_client import bridge_client
    from ..services.orchestrator import build_ladder_plan
    from .. import crud

    ticket = pos.get("ticket")
    if ticket is None:
        return False
    ticket = int(ticket)

    direction = (pos.get("type") or "").upper()
    symbol = pos.get("symbol")
    entry = _num(pos.get("openPrice")) or _num(pos.get("price"))
    lot = _num(pos.get("volume")) or _num(pos.get("lots")) or 0.0
    sl = _num(pos.get("sl")) or 0.0   # 0.0 == no stop set
    tp = _num(pos.get("tp")) or 0.0   # 0.0 == no target set

    if direction not in ("BUY", "SELL") or not symbol or not entry or entry <= 0 or lot <= 0:
        logger.warning(
            "Position reconciler: cannot adopt ticket=%s — incomplete/invalid "
            "position data (symbol=%s dir=%s entry=%s lot=%s)",
            ticket, symbol, direction, entry, lot,
        )
        summary["errors"] += 1
        return False

    sign = 1 if direction == "BUY" else -1

    # ── Protective SL when the position has none ─────────────────────────
    protected = False
    if sl <= 0:
        sl = round(entry * (1 - sign * protective_sl_pct), 5)
        try:
            bridge_client.modify_position(ticket, stop_loss=sl)
            protected = True
            summary["adopt_protected"] += 1
            logger.info(
                "Position reconciler: set protective SL on orphan ticket=%d "
                "%s %s entry=%.5f sl=%.5f (%.2f%%)",
                ticket, direction, symbol, entry, sl, protective_sl_pct * 100,
            )
        except Exception as exc:
            logger.warning(
                "Position reconciler: protective SL modify failed ticket=%d: %s",
                ticket, exc,
            )
            # Fall through — still adopt/track it even if the broker modify failed.

    # ── R-multiple targets + ladder plan ─────────────────────────────────
    r = abs(entry - sl)
    if r <= 0:
        r = entry * protective_sl_pct  # degenerate guard (SL ~= entry)
    atr = r / 1.5  # ATR proxy so the ladder trail has a sane step (no candle fetch)

    if tp > 0:
        tp_levels = [tp, None, None, None]  # respect the human's single TP
        hard_tp = tp
    else:
        tp_levels = [round(entry + sign * k * r, 5) for k in (1, 2, 3, 4)]
        hard_tp = tp_levels[-1]
        try:
            bridge_client.modify_position(ticket, take_profit=hard_tp)
            logger.info(
                "Position reconciler: set hard-TP on orphan ticket=%d tp=%.5f (4R)",
                ticket, hard_tp,
            )
        except Exception as exc:
            logger.warning(
                "Position reconciler: hard-TP modify failed ticket=%d: %s",
                ticket, exc,
            )

    ladder = build_ladder_plan(direction, entry, sl, lot, tp_levels, atr)
    opened_at = _parse_position_open_dt(pos) or datetime.utcnow()

    fields = {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "lot_size": lot,
        "stop_loss": sl,
        "take_profit": hard_tp,
        "mt5_ticket": ticket,
        "atr_at_entry": atr,
        "opened_at": opened_at,
        "ladder": ladder,
    }

    try:
        _trade, created = crud.adopt_orphan_trade(db, fields)
    except Exception as exc:
        logger.error(
            "Position reconciler: adopt_orphan_trade failed ticket=%d: %s",
            ticket, exc, exc_info=True,
        )
        summary["errors"] += 1
        return False

    if created:
        summary["adopted"] += 1
        logger.warning(
            "Position reconciler: ADOPTED orphan position ticket=%d %s %s "
            "lot=%.2f entry=%.5f sl=%.5f tp=%.5f protective_sl=%s — now bot-managed",
            ticket, direction, symbol, lot, entry, sl, hard_tp, protected,
        )
    return created


def reconcile_positions(db: Database) -> dict[str, int]:
    """
    Main reconciliation function. Call from the async loop via run_in_executor
    (this function is synchronous and safe for thread-pool execution).

    Returns a summary dict:
        {
            "checked": int,          # primary-pass trades checked
            "ghost_found": int,      # ghost trades detected
            "closed": int,           # successfully marked closed
            "no_deal_data": int,     # ghost but no deal history available
            "ticket_matched": int,   # secondary-pass trades matched and ticket written back
            "adopted": int,          # orphan positions adopted into management
            "adopt_protected": int,  # orphans that were given a protective SL
            "errors": int,           # unexpected errors
        }
    """
    from ..services.bridge_client import bridge_client
    from .. import crud
    from ..db import COLL_TRADES

    summary = {
        "checked": 0,
        "ghost_found": 0,
        "closed": 0,
        "no_deal_data": 0,
        "ticket_matched": 0,
        "adopted": 0,
        "adopt_protected": 0,
        "errors": 0,
    }

    # ── 1. Fetch live MT5 positions ─────────────────────────────────────
    try:
        live_positions: list[dict] = bridge_client.get_positions()
    except Exception as exc:
        logger.warning(
            "Position reconciler: could not fetch live positions (bridge unreachable?): %s", exc
        )
        return summary  # skip cycle — don't crash the loop

    live_tickets: set[int] = {
        int(p["ticket"]) for p in live_positions if p.get("ticket") is not None
    }
    logger.debug(
        "Position reconciler: %d live MT5 positions: %s",
        len(live_tickets), sorted(live_tickets),
    )

    # ── SECONDARY PASS — match ticketless DB trades to live positions ────
    try:
        ticketless_trades = _get_open_trades_without_tickets(db)
        for trade_doc in ticketless_trades:
            trade_id = trade_doc["_id"]
            trade_symbol = (trade_doc.get("symbol") or "").upper()
            trade_direction = (trade_doc.get("direction") or "").upper()
            trade_opened_at: datetime | None = trade_doc.get("opened_at")

            if not trade_symbol or not trade_direction or trade_opened_at is None:
                continue

            # Normalise trade_opened_at to naive UTC for comparison.
            if hasattr(trade_opened_at, "tzinfo") and trade_opened_at.tzinfo is not None:
                trade_opened_at = trade_opened_at.replace(tzinfo=None)

            # Build set of acceptable symbol variants (handles XAUUSDm ↔ XAUUSD etc.)
            sym_variants: set[str] = {trade_symbol}
            if trade_symbol.endswith("M"):
                sym_variants.add(trade_symbol[:-1])
            else:
                sym_variants.add(trade_symbol + "M")
            if trade_symbol.endswith("m"):
                sym_variants.add(trade_symbol[:-1])
            else:
                sym_variants.add(trade_symbol + "m")

            # Try to find a matching live MT5 position
            for pos in live_positions:
                pos_symbol = (pos.get("symbol") or "").upper()
                pos_type = (pos.get("type") or "").upper()
                pos_ticket = pos.get("ticket")

                if pos_symbol not in sym_variants or pos_type != trade_direction:
                    continue

                # Compare open times within tolerance
                pos_open_raw = pos.get("openTime") or pos.get("time")
                if pos_open_raw is None:
                    continue

                try:
                    if isinstance(pos_open_raw, (int, float)):
                        pos_open_dt = datetime.utcfromtimestamp(float(pos_open_raw))
                    else:
                        pos_open_dt = datetime.fromisoformat(
                            str(pos_open_raw).replace("Z", "+00:00")
                        )
                        if pos_open_dt.tzinfo is not None:
                            pos_open_dt = pos_open_dt.replace(tzinfo=None)
                except Exception:
                    continue

                diff_secs = abs((trade_opened_at - pos_open_dt).total_seconds())
                if diff_secs <= _OPEN_TIME_TOLERANCE_SECS:
                    # Match found — write the ticket back
                    db[COLL_TRADES].update_one(
                        {"_id": trade_id},
                        {"$set": {"mt5_ticket": int(pos_ticket)}},
                    )
                    summary["ticket_matched"] += 1
                    logger.info(
                        "Position reconciler: secondary match — "
                        "db_id=%s matched to ticket=%s (%s %s, Δt=%.0fs)",
                        trade_id, pos_ticket, trade_direction, trade_symbol, diff_secs,
                    )
                    break  # move to next trade_doc

    except Exception as exc:
        logger.warning("Position reconciler: secondary pass failed: %s", exc)

    # ── ORPHAN ADOPTION PASS — every account position is the bot's job ───
    # Any live position with no open DB trade backing it (a manual order, an
    # un-recorded duplicate, or a failed post-order insert) is adopted and put
    # under full management. Runs AFTER the secondary pass so a position that
    # was just matched to a ticketless trade is not mistaken for an orphan.
    try:
        min_age = _setting_float(db, "orphan_adopt_min_age_secs", 45.0)
        sl_pct = _setting_float(db, "orphan_protective_sl_pct", 0.01)
        tracked = _open_ticket_set(db)
        now = datetime.utcnow()
        for pos in live_positions:
            tkt = pos.get("ticket")
            if tkt is None:
                continue
            try:
                if int(tkt) in tracked:
                    continue
            except (TypeError, ValueError):
                continue
            # Min-age guard: don't race the orchestrator's own just-placed order
            # whose DB insert may still be in flight.
            opened = _parse_position_open_dt(pos)
            if opened is not None and (now - opened).total_seconds() < min_age:
                logger.debug(
                    "Position reconciler: skipping young orphan ticket=%s (age < %.0fs)",
                    tkt, min_age,
                )
                continue
            adopt_orphan_position(db, pos, sl_pct, summary)
    except Exception as exc:
        logger.warning("Position reconciler: orphan adoption pass failed: %s", exc)

    # ── 2. Fetch open DB trades WITH tickets ────────────────────────────
    try:
        open_trades = _get_open_trades_with_tickets(db)
    except Exception as exc:
        logger.warning("Position reconciler: DB query failed: %s", exc)
        return summary

    summary["checked"] = len(open_trades)
    if not open_trades:
        return summary

    # ── 3. Identify ghost trades ────────────────────────────────────────
    for trade_doc in open_trades:
        try:
            trade_id   = trade_doc["_id"]
            mt5_ticket = int(trade_doc["mt5_ticket"])

            if mt5_ticket in live_tickets:
                continue  # still open in MT5 — not a ghost

            # ── Ghost detected ──────────────────────────────────────────
            summary["ghost_found"] += 1
            logger.info(
                "Position reconciler: ghost trade detected — "
                "db_id=%s mt5_ticket=%d not in live positions (%d live)",
                trade_id, mt5_ticket, len(live_tickets),
            )

            # ── 4. Fetch deal history to get closing price + PnL ────────
            deals = bridge_client.get_deals(ticket=mt5_ticket, lookback_days=14)

            if not deals:
                # No deal data — mark as CLOSED with unknown PnL (0)
                # Using "LOSS" as a conservative default so it doesn't
                # inflate win-rate statistics.
                logger.warning(
                    "Position reconciler: no deal history for ticket=%d — "
                    "marking closed with PnL=0 (LOSS)",
                    mt5_ticket,
                )
                summary["no_deal_data"] += 1
                crud.close_trade(
                    db,
                    trade_id=trade_id,
                    exit_price=float(trade_doc.get("entry_price") or 0.0),
                    pnl=0.0,
                    result="LOSS",
                )
                summary["closed"] += 1
                _trigger_trade_close_hooks(db, trade_id, "LOSS")
                continue

            close_deal = _extract_close_deal(deals)
            if close_deal is None:
                logger.warning(
                    "Position reconciler: could not extract close deal for ticket=%d",
                    mt5_ticket,
                )
                summary["no_deal_data"] += 1
                continue

            # ── 5. Compute PnL and result ────────────────────────────────
            profit     = float(close_deal.get("profit")     or 0.0)
            swap       = float(close_deal.get("swap")       or 0.0)
            commission = float(close_deal.get("commission") or 0.0)
            pnl        = profit + swap + commission
            exit_price = float(close_deal.get("price")      or 0.0)
            result     = "WIN" if pnl > 0 else "LOSS"

            logger.info(
                "Position reconciler: closing ghost trade db_id=%s ticket=%d "
                "exit_price=%.5f pnl=%.2f result=%s",
                trade_id, mt5_ticket, exit_price, pnl, result,
            )

            crud.close_trade(
                db,
                trade_id=trade_id,
                exit_price=exit_price,
                pnl=pnl,
                result=result,
            )
            summary["closed"] += 1

            # ── 6. Run trade-close hooks (score feedback + news learning) ─
            _trigger_trade_close_hooks(db, trade_id, result)

        except Exception as exc:
            summary["errors"] += 1
            logger.error(
                "Position reconciler: unexpected error processing trade %s: %s",
                trade_doc.get("_id"), exc, exc_info=True,
            )

    if (summary["ghost_found"] > 0 or summary["errors"] > 0
            or summary["ticket_matched"] > 0 or summary["adopted"] > 0):
        logger.info(
            "Position reconciler: checked=%d ghost=%d closed=%d "
            "no_data=%d ticket_matched=%d adopted=%d adopt_protected=%d errors=%d",
            summary["checked"], summary["ghost_found"],
            summary["closed"], summary["no_deal_data"],
            summary["ticket_matched"], summary["adopted"],
            summary["adopt_protected"], summary["errors"],
        )

    return summary