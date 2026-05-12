import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BacktestResult
from ..services.backtester import run_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("/run")
def run(body: dict, db: Session = Depends(get_db)):
    strategy_name = body.get("strategy_name", "DTC")
    symbol = body.get("symbol", "XAUUSD")
    from_date = body.get("from_date", "2024-01-01")
    to_date = body.get("to_date", "2024-12-31")
    params = body.get("params", {})
    initial_balance = float(body.get("initial_balance", 10000))
    leverage = int(body.get("leverage", 100))
    risk_per_trade_pct = float(body.get("risk_per_trade_pct", 1.0))

    bt_id = run_backtest(
        db,
        strategy_name=strategy_name,
        symbol=symbol,
        from_date=from_date,
        to_date=to_date,
        params=params,
        initial_balance=initial_balance,
        leverage=leverage,
        risk_per_trade_pct=risk_per_trade_pct,
    )
    return {"backtest_id": bt_id, "status": "started"}


@router.get("/results")
def list_results(limit: int = 20, db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(BacktestResult)
            .order_by(BacktestResult.created_at.desc())
            .limit(limit)
        ).all()
    )
    return [
        {
            "id": r.id,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "from_date": r.from_date,
            "to_date": r.to_date,
            "initial_balance": r.initial_balance,
            "leverage": r.leverage,
            "risk_per_trade_pct": r.risk_per_trade_pct,
            "status": r.status,
            "metrics": json.loads(r.metrics_json or "{}"),
            "created_at": r.created_at,
            "completed_at": r.completed_at,
        }
        for r in rows
    ]


@router.get("/results/{bt_id}")
def get_result(bt_id: int, db: Session = Depends(get_db)):
    row = db.get(BacktestResult, bt_id)
    if not row:
        raise HTTPException(404, "Backtest not found")
    return {
        "id": row.id,
        "strategy_name": row.strategy_name,
        "symbol": row.symbol,
        "from_date": row.from_date,
        "to_date": row.to_date,
        "params": json.loads(row.params_json or "{}"),
        "initial_balance": row.initial_balance,
        "leverage": row.leverage,
        "risk_per_trade_pct": row.risk_per_trade_pct,
        "status": row.status,
        "metrics": json.loads(row.metrics_json or "{}"),
        "equity_curve": json.loads(row.equity_curve_json or "[]"),
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


@router.post("/compare")
def compare(body: dict, db: Session = Depends(get_db)):
    ids = body.get("ids", [])
    results = []
    for bt_id in ids:
        row = db.get(BacktestResult, bt_id)
        if row:
            results.append({
                "id": row.id,
                "strategy_name": row.strategy_name,
                "symbol": row.symbol,
                "from_date": row.from_date,
                "to_date": row.to_date,
                "metrics": json.loads(row.metrics_json or "{}"),
                "equity_curve": json.loads(row.equity_curve_json or "[]"),
            })
    return {"comparisons": results}