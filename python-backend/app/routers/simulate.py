import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..models import AdaptationLog, ParameterVersion, Trade
from ..services.adaptation import run_adaptation
from ..strategy.dtc import DEFAULT_PARAMS, compute_levels, resolve_params

router = APIRouter()
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]


@router.post("/simulate/batch")
def simulate_batch(
    count: int = Query(20, ge=1, le=200),
    win_rate_pct: float = Query(52, ge=0, le=100),
    db: Session = Depends(get_db),
):
    params_raw = crud.get_current_params(db)
    if not params_raw:
        crud.save_params(db, DEFAULT_PARAMS.copy(), reason="Initial defaults", trigger="SYSTEM")
        params_raw = DEFAULT_PARAMS.copy()
    params = resolve_params(params_raw)
    latest = db.scalar(select(ParameterVersion).order_by(desc(ParameterVersion.version)).limit(1))
    version = latest.version if latest else 1

    base_price = 1.08500
    trade_ids = []
    for _ in range(count):
        symbol = random.choice(SYMBOLS)
        direction = "BUY" if random.random() > 0.5 else "SELL"
        price = round(base_price + random.uniform(-0.0020, 0.0020), 5)
        base_price = price
        levels = compute_levels(direction, price, params)
        is_win = random.random() * 100 < win_rate_pct
        exit_price = levels["tp1"] if is_win else levels["sl"]
        pnl = (abs(exit_price - price) * params.lot_size * 100000) * (1 if is_win else -1)
        opened_at = datetime.utcnow() - timedelta(minutes=random.randint(5, 1200))
        closed_at = opened_at + timedelta(minutes=random.randint(10, 240))
        trade = crud.log_trade(
            db,
            {
                "symbol": symbol,
                "direction": direction,
                "entry_price": price,
                "exit_price": exit_price,
                "stop_loss": levels["sl"],
                "take_profit": levels["tp1"],
                "lot_size": params.lot_size,
                "pnl": round(pnl, 2),
                "result": "WIN" if is_win else "LOSS",
                "duration_mins": round((closed_at - opened_at).total_seconds() / 60.0, 1),
                "atr_at_entry": round(random.uniform(0.0005, 0.0025), 5),
                "ema_fast_at_entry": round(price - random.uniform(0.0001, 0.0008), 5),
                "ema_slow_at_entry": round(price - random.uniform(0.0003, 0.0012), 5),
                "params_version": version,
                "opened_at": opened_at,
                "closed_at": closed_at,
            },
        )
        trade_ids.append(trade.id)

    adapt_result = run_adaptation(db, window=min(count, settings_for_window()))
    return {"simulated": count, "trade_ids": trade_ids, "adaptation": adapt_result, "stats": crud.get_stats(db)}


def settings_for_window() -> int:
    return 20


@router.delete("/simulate/reset")
def simulate_reset(db: Session = Depends(get_db)):
    db.execute(delete(AdaptationLog))
    db.execute(delete(ParameterVersion))
    db.execute(delete(Trade))
    db.commit()
    return {"status": "reset complete"}
