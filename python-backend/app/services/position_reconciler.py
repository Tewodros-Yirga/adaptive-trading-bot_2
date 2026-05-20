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
2. Fetch all "open" trades in MongoDB (no closed_at / no result field).
3. Build a set of live MT5 ticket IDs.
4. For each DB-open trade with a `mt5_ticket` NOT in the live set:
   a. Attempt to fetch deal history from GET /deals/{ticket}.
   b. Extract the closing deal (entry=1, i.e. DEAL_ENTRY_OUT).
   c. Calculate PnL = profit + swap + commission (if available).
   d. Determine result: WIN if PnL > 0, LOSS if PnL <= 0.
   e. Call crud.close_trade() to persist the closure.
5. Log all actions. If bridge is unreachable, skip cycle without crashing.

This runs as a background asyncio loop in the backend (every 60s by default).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pymongo.database import Database

logger = logging.getLogger(__name__)

# MT5 deal entry type constant — 1 = DEAL_ENTRY_OUT (closing deal)
_DEAL_ENTRY_OUT = 1


def _get_open_trades_with_tickets(db: Database) -> list[dict]:
    """
    Return all MongoDB trades that are 'open' (no result, no closed_at)
    and have an mt5_ticket so they can be reconciled against live positions.
    """
    from ..db import COLL_TRADES
    docs = list(
        db[COLL_TRADES].find(
            {
                "result": {"$in": [None, "OPEN", ""]},
                "closed_at": {"$exists": False},
                "mt5_ticket": {"$exists": True, "$ne": None},
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


def reconcile_positions(db: Database) -> dict[str, int]:
    """
    Main reconciliation function. Call from the async loop via run_in_executor
    (this function is synchronous and safe for thread-pool execution).

    Returns a summary dict:
        {
            "checked": int,        # trades checked
            "ghost_found": int,    # ghost trades detected
            "closed": int,         # successfully marked closed
            "no_deal_data": int,   # ghost but no deal history available
            "errors": int,         # unexpected errors
        }
    """
    from ..services.bridge_client import bridge_client
    from .. import crud

    summary = {
        "checked": 0,
        "ghost_found": 0,
        "closed": 0,
        "no_deal_data": 0,
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

    # ── 2. Fetch open DB trades with tickets ────────────────────────────
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

        except Exception as exc:
            summary["errors"] += 1
            logger.error(
                "Position reconciler: unexpected error processing trade %s: %s",
                trade_doc.get("_id"), exc, exc_info=True,
            )

    if summary["ghost_found"] > 0 or summary["errors"] > 0:
        logger.info(
            "Position reconciler: checked=%d ghost=%d closed=%d no_data=%d errors=%d",
            summary["checked"], summary["ghost_found"],
            summary["closed"], summary["no_deal_data"], summary["errors"],
        )

    return summary
