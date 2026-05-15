"""
Trades router — exposes trade queries to the frontend.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from ..db import get_db, COLL_TRADES
from .. import crud
from ..models import Trade

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
def list_trades(limit: int = Query(50, ge=1, le=500), db: Database = Depends(get_db)):
    trades = crud.get_recent_trades(db, limit)
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
            "atr_at_entry": t.atr_at_entry,
            "strategy_name": t.strategy_name,
            "params_version": t.params_version,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
        }
        for t in trades
    ]


@router.get("/stats")
def trade_stats(db: Database = Depends(get_db)):
    return crud.get_stats(db)


@router.get("/closed")
def closed_trades(limit: int = Query(100, ge=1, le=1000), db: Database = Depends(get_db)):
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