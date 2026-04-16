import json
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import AdaptationLog, AppSetting, ParameterVersion, Trade


def log_trade(db: Session, fields: dict) -> Trade:
    trade = Trade(**fields)
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def close_trade(db: Session, trade_id: int, exit_price: float, pnl: float, result: str) -> Trade | None:
    trade = db.get(Trade, trade_id)
    if not trade:
        return None
    now = datetime.utcnow()
    duration_mins = ((now - trade.opened_at).total_seconds() / 60.0) if trade.opened_at else None
    trade.exit_price = exit_price
    trade.pnl = pnl
    trade.result = result
    trade.closed_at = now
    trade.duration_mins = round(duration_mins, 1) if duration_mins is not None else None
    db.commit()
    db.refresh(trade)
    return trade


def get_recent_trades(db: Session, limit: int = 50) -> list[Trade]:
    return list(db.scalars(select(Trade).order_by(desc(Trade.opened_at)).limit(limit)).all())


def get_closed_trades(db: Session, limit: int = 100) -> list[Trade]:
    q = select(Trade).where(Trade.result.in_(["WIN", "LOSS"])).order_by(desc(Trade.closed_at)).limit(limit)
    return list(db.scalars(q).all())


def get_stats(db: Session) -> dict:
    trades = get_closed_trades(db, 1000)
    if not trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "avg_rr": 0.0,
        }
    wins = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    gross_profit = sum(t.pnl or 0 for t in wins)
    gross_loss = abs(sum(t.pnl or 0 for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
    cumulative, peak, max_drawdown = 0.0, 0.0, 0.0
    for t in reversed(trades):
        cumulative += t.pnl or 0
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    rr_values = []
    for t in trades:
        if t.entry_price and t.stop_loss and t.take_profit:
            risk = abs(t.entry_price - t.stop_loss)
            reward = abs(t.take_profit - t.entry_price)
            if risk > 0:
                rr_values.append(reward / risk)
    avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0.0
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(trades)) * 100, 2),
        "profit_factor": round(profit_factor, 3),
        "total_pnl": round(sum(t.pnl or 0 for t in trades), 4),
        "max_drawdown": round(max_drawdown, 4),
        "avg_rr": round(avg_rr, 2),
    }


def save_params(
    db: Session,
    params: dict,
    reason: str = "",
    trigger: str = "AUTO",
    confidence_score: float | None = None,
    delta_magnitude: float | None = None,
    rollback_from_version: int | None = None,
) -> ParameterVersion:
    last = db.scalar(select(ParameterVersion).order_by(desc(ParameterVersion.version)).limit(1))
    version = (last.version + 1) if last else 1
    row = ParameterVersion(
        version=version,
        params_json=json.dumps(params),
        reason=reason,
        trigger=trigger,
        confidence_score=confidence_score,
        delta_magnitude=delta_magnitude,
        rollback_from_version=rollback_from_version,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_current_params(db: Session) -> dict | None:
    last = db.scalar(select(ParameterVersion).order_by(desc(ParameterVersion.version)).limit(1))
    return json.loads(last.params_json) if last else None


def get_params_history(db: Session, limit: int = 30) -> list[ParameterVersion]:
    return list(db.scalars(select(ParameterVersion).order_by(desc(ParameterVersion.version)).limit(limit)).all())


def log_adaptation(db: Session, fields: dict) -> AdaptationLog:
    row = AdaptationLog(**fields, evaluated_at=datetime.utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_setting(db: Session, key: str) -> str | None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key).limit(1))
    return row.value if row else None


def set_setting(db: Session, key: str, value: str) -> AppSetting:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key).limit(1))
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        row = AppSetting(key=key, value=value, updated_at=datetime.utcnow())
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_settings(db: Session, keys: list[str]) -> dict[str, str]:
    rows = list(db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))).all())
    return {r.key: r.value for r in rows}
