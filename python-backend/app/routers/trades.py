"""
Trades router — exposes trade queries and trade-management actions to the frontend.

Changes (Model 1):
- GET /trades         now returns *open* trades only (closed_at $exists: false).
- GET /trades/stats   returns today_pnl, open_trades, today_trades, win_rate as decimal.
- GET /trades/{id}    new: single trade lookup.
- POST /trades/{id}/modify       new: adjust SL/TP via bridge.
- POST /trades/{id}/close-partial new: partial close via bridge.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from ..db import get_db, COLL_TRADES
from .. import crud
from ..models import Trade
from ..services.bridge_client import bridge_client

router = APIRouter(prefix="/trades", tags=["trades"])


# ---------------------------------------------------------------------------
# Helper: resolve a trade document to dict for API responses
# ---------------------------------------------------------------------------

def _trade_to_dict(t: Trade) -> dict:
    return {
        "id": t.id,
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "lot_size": t.lot_size,
        "pnl": t.pnl,
        "result": t.result,
        "duration_mins": t.duration_mins,
        "atr_at_entry": t.atr_at_entry,
        "strategy_name": t.strategy_name,
        "params_version": t.params_version,
        "opened_at": t.opened_at,
        "closed_at": t.closed_at,
    }


# ---------------------------------------------------------------------------
# GET /trades  — open positions only
# ---------------------------------------------------------------------------

@router.get("")
def list_trades(
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
):
    """
    Return currently open trades (those with no closed_at).

    The ``result`` field is included so the frontend can distinguish
    ``"OPEN"`` from ``"BLOCKED"`` positions at a glance.
    """
    trades = crud.get_recent_trades(db, limit)
    return [_trade_to_dict(t) for t in trades]


# ---------------------------------------------------------------------------
# GET /trades/stats
# ---------------------------------------------------------------------------

@router.get("/stats")
def trade_stats(db: Database = Depends(get_db)):
    """
    Aggregate trade statistics.

    win_rate is returned as a decimal (0.0–1.0).  Multiply by 100 in the
    frontend to display as a percentage.

    Additional fields vs. the original endpoint:
    - today_pnl   — realised PnL for trades closed today (UTC)
    - open_trades — count of currently open positions
    - today_trades — count of trades opened today (UTC)
    """
    return crud.get_stats(db)


# ---------------------------------------------------------------------------
# GET /trades/closed
# ---------------------------------------------------------------------------

@router.get("/closed")
def closed_trades(
    limit: int = Query(100, ge=1, le=1000),
    db: Database = Depends(get_db),
):
    trades = crud.get_closed_trades(db, limit)
    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_loss": t.stop_loss,
            "take_profit": t.take_profit,
            "lot_size": t.lot_size,
            "pnl": t.pnl,
            "result": t.result,
            "duration_mins": t.duration_mins,
            "strategy_name": t.strategy_name,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
        }
        for t in trades
    ]


# ---------------------------------------------------------------------------
# GET /trades/{trade_id}  — single trade lookup
# ---------------------------------------------------------------------------

@router.get("/{trade_id}")
def get_trade(trade_id: int, db: Database = Depends(get_db)):
    """Return a single trade by its integer _id."""
    doc = db[COLL_TRADES].find_one({"_id": trade_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Trade #{trade_id} not found")
    t = Trade.from_doc(doc)
    return _trade_to_dict(t)


# ---------------------------------------------------------------------------
# GET /trades/analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
def trade_analytics(
    days: int = Query(30, ge=1, le=365),
    strategy_name: Optional[str] = None,
    db: Database = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query: dict = {
        "result": {"$in": ["WIN", "LOSS"]},
        "closed_at": {"$gte": cutoff},
    }
    if strategy_name:
        query["strategy_name"] = strategy_name

    docs = db[COLL_TRADES].find(query).sort("closed_at", 1)
    trades = [Trade.from_doc(d) for d in docs]

    # ── by_strategy ────────────────────────────────────────────────────────
    strat_buckets: dict[str, list] = defaultdict(list)
    for t in trades:
        strat_buckets[t.strategy_name or "unknown"].append(t)

    by_strategy = {}
    for sname, strades in strat_buckets.items():
        wins = [t for t in strades if t.result == "WIN"]
        losses = [t for t in strades if t.result == "LOSS"]
        gross_loss = abs(sum(t.pnl or 0 for t in losses)) or 1e-9
        by_strategy[sname] = {
            "trades": len(strades),
            "win_rate": round(len(wins) / len(strades) * 100, 2) if strades else 0,
            "profit_factor": round(sum(t.pnl or 0 for t in wins) / gross_loss, 3),
            "avg_duration_mins": round(
                sum(t.duration_mins or 0 for t in strades) / len(strades), 1
            ) if strades else 0,
            "total_pnl": round(sum(t.pnl or 0 for t in strades), 4),
        }

    # ── by_hour_of_day ────────────────────────────────────────────────────
    hour_buckets: dict[int, list] = defaultdict(list)
    for t in trades:
        if t.closed_at:
            hour_buckets[t.closed_at.hour].append(t)

    by_hour_of_day = {}
    for hr in range(24):
        bucket = hour_buckets.get(hr, [])
        wins = [t for t in bucket if t.result == "WIN"]
        by_hour_of_day[str(hr)] = {
            "trades": len(bucket),
            "win_rate": round(len(wins) / len(bucket) * 100, 1) if bucket else 0.0,
        }

    # ── by_day_of_week ────────────────────────────────────────────────────
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_buckets: dict[int, list] = defaultdict(list)
    for t in trades:
        if t.closed_at:
            day_buckets[t.closed_at.weekday()].append(t)

    by_day_of_week = {}
    for i, name in enumerate(day_names):
        bucket = day_buckets.get(i, [])
        wins = [t for t in bucket if t.result == "WIN"]
        by_day_of_week[name] = {
            "trades": len(bucket),
            "win_rate": round(len(wins) / len(bucket) * 100, 1) if bucket else 0.0,
        }

    # ── by_direction ──────────────────────────────────────────────────────
    by_direction: dict[str, dict] = {}
    for direction in ("BUY", "SELL"):
        bucket = [t for t in trades if t.direction == direction]
        wins = [t for t in bucket if t.result == "WIN"]
        by_direction[direction] = {
            "trades": len(bucket),
            "win_rate": round(len(wins) / len(bucket) * 100, 1) if bucket else 0.0,
        }

    # ── drawdown_curve ────────────────────────────────────────────────────
    drawdown_curve = []
    running_pnl = 0.0
    peak = 0.0
    for t in trades:
        running_pnl += t.pnl or 0
        if running_pnl > peak:
            peak = running_pnl
        drawdown_pct = ((peak - running_pnl) / abs(peak) * 100) if peak > 0 else 0.0
        drawdown_curve.append({
            "date": t.closed_at.strftime("%Y-%m-%d") if t.closed_at else None,
            "drawdown_pct": round(drawdown_pct, 2),
        })

    # ── streak_analysis ───────────────────────────────────────────────────
    max_win = max_loss = cur = 0
    cur_type = None
    for t in trades:
        r = t.result
        if r == cur_type:
            cur += 1
        else:
            cur = 1
            cur_type = r
        if r == "WIN":
            max_win = max(max_win, cur)
        else:
            max_loss = max(max_loss, cur)

    return {
        "period_days": days,
        "by_strategy": by_strategy,
        "by_hour_of_day": by_hour_of_day,
        "by_day_of_week": by_day_of_week,
        "by_direction": by_direction,
        "drawdown_curve": drawdown_curve,
        "streak_analysis": {
            "current_streak": cur,
            "current_streak_type": cur_type or "NONE",
            "max_win_streak": max_win,
            "max_loss_streak": max_loss,
        },
    }


# ---------------------------------------------------------------------------
# POST /trades/{trade_id}/modify  — adjust SL / TP on a live position
# ---------------------------------------------------------------------------

@router.post("/{trade_id}/modify")
def modify_trade(
    trade_id: int,
    payload: dict,
    db: Database = Depends(get_db),
):
    """
    Modify the stop-loss and/or take-profit of an open position via the MT5 bridge.

    Request body (all fields optional):
        { "stop_loss": 1890.0, "take_profit": 1950.0 }

    The trade's ``mt5_ticket`` field is used as the bridge ticket.
    After a successful bridge call the trade document is updated in MongoDB.
    """
    doc = db[COLL_TRADES].find_one({"_id": trade_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Trade #{trade_id} not found")

    mt5_ticket: int | None = doc.get("mt5_ticket")
    if mt5_ticket is None:
        raise HTTPException(
            status_code=422,
            detail=f"Trade #{trade_id} has no mt5_ticket — cannot modify via bridge",
        )

    new_sl: float | None = payload.get("stop_loss")
    new_tp: float | None = payload.get("take_profit")

    if new_sl is None and new_tp is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of stop_loss or take_profit must be provided",
        )

    # Call bridge
    result = bridge_client.modify_position(
        ticket=int(mt5_ticket),
        stop_loss=new_sl,
        take_profit=new_tp,
    )

    # Persist changes to the trade document
    update_fields: dict = {}
    if new_sl is not None:
        update_fields["stop_loss"] = new_sl
    if new_tp is not None:
        update_fields["take_profit"] = new_tp

    if update_fields:
        db[COLL_TRADES].update_one({"_id": trade_id}, {"$set": update_fields})

    return {
        "status": "ok",
        "trade_id": trade_id,
        "mt5_ticket": mt5_ticket,
        "bridge_response": result,
        **update_fields,
    }


# ---------------------------------------------------------------------------
# POST /trades/{trade_id}/close-partial  — partially close a live position
# ---------------------------------------------------------------------------

@router.post("/{trade_id}/close-partial")
def close_partial_trade(
    trade_id: int,
    payload: dict,
    db: Database = Depends(get_db),
):
    """
    Partially close an open MT5 position.

    Request body:
        { "volume": 0.005 }

    The trade's ``mt5_ticket`` field is used as the bridge ticket.
    Returns the raw bridge response including ``remainVolume``.
    """
    volume: float | None = payload.get("volume")
    if volume is None or float(volume) <= 0:
        raise HTTPException(status_code=400, detail="volume must be a positive number")

    doc = db[COLL_TRADES].find_one({"_id": trade_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Trade #{trade_id} not found")

    mt5_ticket: int | None = doc.get("mt5_ticket")
    if mt5_ticket is None:
        raise HTTPException(
            status_code=422,
            detail=f"Trade #{trade_id} has no mt5_ticket — cannot close via bridge",
        )

    result = bridge_client.close_position(
        ticket=int(mt5_ticket),
        lot_size=float(volume),
        partial=True,
    )

    return {
        "status": "ok",
        "trade_id": trade_id,
        "mt5_ticket": mt5_ticket,
        **result,
    }