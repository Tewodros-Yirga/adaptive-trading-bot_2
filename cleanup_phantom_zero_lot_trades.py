"""
Clean up phantom zero-lot OPEN trades.

A zero-lot order can never fill at the broker, but older builds still recorded
an OPEN trade for it (FIXED sizing had no floor, and the news-caution multiplier
could round a 0.01 base lot down to 0.00). Those rows show up in Live Trades as
positions the account never actually held, and — being ticketless — the position
reconciler never closes them. The code path is now fixed; this script clears the
rows that were already written.

Targets exactly what the Live Trades view shows (no ``closed_at``) with a
zero/missing lot, and marks each as ``CANCELLED`` (kept, not deleted) so it
leaves the open list without polluting WIN/LOSS/BLOCKED analytics.

Usage:
    python cleanup_phantom_zero_lot_trades.py            # dry run — report only
    python cleanup_phantom_zero_lot_trades.py --apply    # actually mark them CANCELLED

Connection (override via env if your setup differs):
    MONGODB_URI   default mongodb://localhost:27017/
    DB_NAME       default trading_bot
"""
import os
import sys
from datetime import datetime

from pymongo import MongoClient

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")
DB_NAME = os.environ.get("DB_NAME", "trading_bot")

# A trade is "open" in the Live Trades view when it has no closed_at (see
# crud.get_recent_trades). A phantom is such a trade whose lot is zero/missing.
QUERY = {
    "closed_at": {"$exists": False},
    "$or": [
        {"lot_size": {"$lte": 0}},
        {"lot_size": None},
        {"lot_size": {"$exists": False}},
    ],
}


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    docs = list(db.trades.find(
        QUERY,
        {"_id": 1, "symbol": 1, "direction": 1, "lot_size": 1,
         "result": 1, "mt5_ticket": 1, "opened_at": 1, "strategy_name": 1},
    ).sort("opened_at", -1))

    print("=" * 80)
    print(f"PHANTOM ZERO-LOT OPEN TRADES  ({DB_NAME} @ {MONGODB_URI})")
    print("=" * 80)

    if not docs:
        print("\n  ✓ None found — nothing to clean up.")
        return 0

    print(f"\n  Found {len(docs)} phantom trade(s):\n")
    print(f"  {'id':<26} {'symbol':<10} {'dir':<5} {'lot':>5} {'ticket':>10}  opened_at")
    print("  " + "-" * 78)
    for d in docs:
        tkt = d.get("mt5_ticket")
        print(
            f"  {str(d.get('_id')):<26} {str(d.get('symbol') or '?'):<10} "
            f"{str(d.get('direction') or '?'):<5} {float(d.get('lot_size') or 0):>5.2f} "
            f"{('none' if tkt is None else str(tkt)):>10}  {d.get('opened_at')}"
        )

    if not apply:
        print(f"\n  DRY RUN — nothing changed. Re-run with --apply to mark these "
              f"{len(docs)} trade(s) as CANCELLED.")
        return 0

    now = datetime.utcnow()
    res = db.trades.update_many(
        QUERY,
        {"$set": {
            "result": "CANCELLED",
            "closed_at": now,
            "pnl": 0.0,
            "cancel_reason": "phantom zero-lot entry (order never filled) — cleaned up",
        }},
    )
    print(f"\n  ✓ APPLIED — marked {res.modified_count} trade(s) as CANCELLED "
          f"(closed_at={now.isoformat()}Z). They no longer appear in Live Trades "
          f"and are excluded from WIN/LOSS/BLOCKED analytics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
