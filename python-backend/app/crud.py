import json
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import (
    AdaptationLog, AppSetting, BacktestBatch, BacktestCandidate,
    BacktestResult, EnsembleDecision, ParameterVersion, PickerWeightHistory,
    StrategyPairAnalysis, StrategyPickerDecision, Strategy, Trade,
)


# ---------------------------------------------------------------------------
# Trade CRUD
# ---------------------------------------------------------------------------

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


def get_recent_closed_trades_for_strategy(
    db: Session, strategy_name: str, limit: int = 20
) -> list[Trade]:
    """Return the most recent closed trades for a specific strategy."""
    q = (
        select(Trade)
        .where(Trade.result.in_(["WIN", "LOSS"]))
        .where(Trade.strategy_name == strategy_name)
        .order_by(desc(Trade.closed_at))
        .limit(limit)
    )
    return list(db.scalars(q).all())


# Alias used by startup_checks and other services
get_recent_closed_trades = get_recent_closed_trades_for_strategy


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


# ---------------------------------------------------------------------------
# Parameter CRUD
# ---------------------------------------------------------------------------

def save_params(
    db: Session,
    params: dict,
    reason: str = "",
    trigger: str = "AUTO",
    confidence_score: float | None = None,
    delta_magnitude: float | None = None,
    rollback_from_version: int | None = None,
    strategy_name: str | None = None,
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
        strategy_name=strategy_name,
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


def get_latest_param_version_for_strategy(
    db: Session, strategy_name: str
) -> ParameterVersion | None:
    """Return the most recently created ParameterVersion for a specific strategy."""
    return db.scalar(
        select(ParameterVersion)
        .where(ParameterVersion.strategy_name == strategy_name)
        .order_by(desc(ParameterVersion.version))
        .limit(1)
    )


# Alias used by startup_checks and other services
get_latest_param_version = get_latest_param_version_for_strategy


# ---------------------------------------------------------------------------
# Adaptation Log CRUD
# ---------------------------------------------------------------------------

def log_adaptation(db: Session, fields: dict) -> AdaptationLog:
    row = AdaptationLog(**fields, evaluated_at=datetime.utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# AppSetting CRUD
# ---------------------------------------------------------------------------

def get_setting(db: Session, key: str) -> str | None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key).limit(1))
    return row.value if row else None


# Synchronous alias (used inside background tasks that already have a db session)
get_setting_sync = get_setting


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


# ---------------------------------------------------------------------------
# Strategy CRUD helpers
# ---------------------------------------------------------------------------

def get_strategy_by_name(db: Session, name: str) -> Strategy | None:
    return db.scalar(select(Strategy).where(Strategy.name == name))


def get_active_strategies(db: Session) -> list[Strategy]:
    return list(db.scalars(select(Strategy).where(Strategy.is_active == True)).all())  # noqa: E712


def get_all_strategies(db: Session) -> list[Strategy]:
    """Return all strategy rows regardless of active state."""
    return list(db.scalars(select(Strategy)).all())


def update_strategy_params(db: Session, strategy_name: str, params: dict) -> Strategy | None:
    row = get_strategy_by_name(db, strategy_name)
    if not row:
        return None
    row.params_json = json.dumps(params)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# BacktestResult CRUD (extended)
# ---------------------------------------------------------------------------

def get_backtest_result(db: Session, bt_id: int) -> BacktestResult | None:
    """Fetch a single BacktestResult by primary key."""
    return db.get(BacktestResult, bt_id)


def get_backtest_results_by_batch(db: Session, batch_id: str) -> list[BacktestResult]:
    """Return all BacktestResult rows for a given batch_id."""
    return list(
        db.scalars(
            select(BacktestResult)
            .where(BacktestResult.batch_id == batch_id)
            .order_by(BacktestResult.created_at)
        ).all()
    )


# ---------------------------------------------------------------------------
# BacktestCandidate CRUD
# ---------------------------------------------------------------------------

def create_backtest_candidate(db: Session, candidate: BacktestCandidate) -> BacktestCandidate:
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def get_backtest_candidates(
    db: Session,
    strategy_name: str,
    page: int = 1,
    limit: int = 50,
    qualified_only: bool = False,
) -> list[BacktestCandidate]:
    q = (
        select(BacktestCandidate)
        .where(BacktestCandidate.strategy_name == strategy_name)
    )
    if qualified_only:
        q = q.where(BacktestCandidate.qualified == True)  # noqa: E712
    q = q.order_by(desc(BacktestCandidate.evaluated_at)).offset((page - 1) * limit).limit(limit)
    return list(db.scalars(q).all())


def get_best_backtest_candidate(db: Session, strategy_name: str) -> BacktestCandidate | None:
    return db.scalar(
        select(BacktestCandidate)
        .where(BacktestCandidate.strategy_name == strategy_name)
        .where(BacktestCandidate.qualified == True)  # noqa: E712
        .order_by(desc(BacktestCandidate.composite_score))
        .limit(1)
    )


# ---------------------------------------------------------------------------
# BacktestBatch CRUD
# ---------------------------------------------------------------------------

def create_backtest_batch(
    db: Session,
    batch_id: str,
    strategy_names: list[str],
    shared_settings: dict | None = None,
) -> BacktestBatch:
    row = BacktestBatch(
        batch_id=batch_id,
        strategy_names=strategy_names,
        shared_settings_json=shared_settings or {},
        status="RUNNING",
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_backtest_batch(db: Session, batch_id: str) -> BacktestBatch | None:
    return db.scalar(select(BacktestBatch).where(BacktestBatch.batch_id == batch_id))


def update_backtest_batch(
    db: Session,
    batch_id: str,
    status: str,
    cross_analysis_json: dict | None = None,
) -> BacktestBatch | None:
    row = get_backtest_batch(db, batch_id)
    if not row:
        return None
    row.status = status
    if cross_analysis_json is not None:
        row.cross_analysis_json = cross_analysis_json
    if status in ("COMPLETE", "PARTIAL_FAILURE"):
        row.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


# Alias used in some services
update_backtest_batch_status = update_backtest_batch


def get_backtest_results_for_batch(db: Session, batch_id: str) -> list[BacktestResult]:
    return list(
        db.scalars(
            select(BacktestResult)
            .where(BacktestResult.batch_id == batch_id)
            .order_by(BacktestResult.created_at)
        ).all()
    )


# ---------------------------------------------------------------------------
# StrategyPairAnalysis CRUD
# ---------------------------------------------------------------------------

def create_pair_analysis(db: Session, row: StrategyPairAnalysis) -> StrategyPairAnalysis:
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_pair_analyses_for_batch(
    db: Session,
    batch_id: str,
) -> list[StrategyPairAnalysis]:
    return list(
        db.scalars(
            select(StrategyPairAnalysis)
            .where(StrategyPairAnalysis.batch_id == batch_id)
            .order_by(desc(StrategyPairAnalysis.synergy_score))
        ).all()
    )


# Alias
get_pair_analyses = get_pair_analyses_for_batch


# ---------------------------------------------------------------------------
# EnsembleDecision CRUD
# ---------------------------------------------------------------------------

def create_ensemble_decision(db: Session, fields: dict) -> EnsembleDecision:
    row = EnsembleDecision(**fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_ensemble_decision_trade_id(
    db: Session, decision_id: int, trade_id: int
) -> EnsembleDecision | None:
    row = db.get(EnsembleDecision, decision_id)
    if not row:
        return None
    row.trade_id = trade_id
    db.commit()
    db.refresh(row)
    return row


def get_ensemble_decisions(
    db: Session,
    page: int = 1,
    limit: int = 50,
    symbol: str | None = None,
) -> list[EnsembleDecision]:
    q = select(EnsembleDecision).order_by(desc(EnsembleDecision.timestamp))
    if symbol:
        q = q.where(EnsembleDecision.symbol == symbol)
    q = q.offset((page - 1) * limit).limit(limit)
    return list(db.scalars(q).all())


def get_ensemble_decision(db: Session, decision_id: int) -> EnsembleDecision | None:
    return db.get(EnsembleDecision, decision_id)


# ---------------------------------------------------------------------------
# StrategyPickerDecision CRUD
# ---------------------------------------------------------------------------

def create_picker_decision(db: Session, row: StrategyPickerDecision) -> StrategyPickerDecision:
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_picker_decision_trade_id(
    db: Session, decision_id: int, trade_id: int
) -> StrategyPickerDecision | None:
    row = db.get(StrategyPickerDecision, decision_id)
    if not row:
        return None
    row.trade_id = trade_id
    db.commit()
    db.refresh(row)
    return row


def get_picker_decisions(
    db: Session,
    page: int = 1,
    limit: int = 50,
    symbol: str | None = None,
) -> list[StrategyPickerDecision]:
    q = select(StrategyPickerDecision).order_by(desc(StrategyPickerDecision.timestamp))
    if symbol:
        q = q.where(StrategyPickerDecision.symbol == symbol)
    q = q.offset((page - 1) * limit).limit(limit)
    return list(db.scalars(q).all())


def get_picker_decision(db: Session, decision_id: int) -> StrategyPickerDecision | None:
    return db.get(StrategyPickerDecision, decision_id)


def get_picker_decisions_for_trade(
    db: Session, trade_id: int
) -> list[StrategyPickerDecision]:
    return list(
        db.scalars(
            select(StrategyPickerDecision)
            .where(StrategyPickerDecision.trade_id == trade_id)
        ).all()
    )


# ---------------------------------------------------------------------------
# PickerWeightHistory CRUD
# ---------------------------------------------------------------------------

def create_picker_weight_history(db: Session, row: PickerWeightHistory) -> PickerWeightHistory:
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_picker_weight_history(
    db: Session, limit: int = 50
) -> list[PickerWeightHistory]:
    return list(
        db.scalars(
            select(PickerWeightHistory)
            .order_by(desc(PickerWeightHistory.updated_at))
            .limit(limit)
        ).all()
    )


# ---------------------------------------------------------------------------
# Seed default AppSettings
# ---------------------------------------------------------------------------

def seed_default_settings(db: Session) -> None:
    """
    Insert default AppSetting rows for all subsystems.
    Skips keys that already exist (never overwrites).
    Called once from startup.
    """
    from .strategy.registry import STRATEGY_REGISTRY

    # ── Risk management ───────────────────────────────────────────────────
    risk_defaults: dict[str, str] = {
        "account_balance": "10000.0",
        "leverage": "100",
        "risk_per_trade_pct": "1.0",
        "max_open_trades": "5",
        "max_daily_loss_pct": "5.0",
        "max_drawdown_pct": "20.0",
        "lot_size_mode": "FIXED",
        "trading_halt": "false",
        "symbol_exposure_limit": "1.0",
    }

    # ── News intelligence ─────────────────────────────────────────────────
    news_defaults: dict[str, str] = {
        "newsapi_key": "",
        "alphavantage_key": "",
        "finnhub_key": "",
        "groq_api_key": "",
        "twelve_data_key": "",
        "news_lookback_hours": "4",
        "news_block_threshold": "0.7",
        "news_caution_factor": "0.5",
        "retrospective_learning_interval_hours": "4",
        "global_context_interval_minutes": "30",
        "news_analysis_system_prompt": "",
        "global_market_context": "{}",
    }

    # ── Backtest engine ────────────────────────────────────────────────────
    backtest_globals: dict[str, str] = {
        "backtest_adapt_every_n_trades": "20",
    }

    # ── Global picker ──────────────────────────────────────────────────────
    picker_defaults: dict[str, str] = {
        "picker_max_simultaneous_strategies": "1",
        "picker_min_score": "0.3",
        "picker_secondary_threshold": "0.85",
        "picker_lookback_trades": "20",
        "picker_min_trades_for_scoring": "5",
        "picker_learning_rate": "0.05",
        "picker_recency_lambda": "0.1",
        "picker_news_bias_threshold": "0.5",
        "picker_news_bonus": "0.15",
        "picker_news_penalty": "0.15",
        "picker_news_veto_threshold": "0.85",
        "picker_weight_recent_win_rate": "0.25",
        "picker_weight_profit_factor": "0.20",
        "picker_weight_backtest_composite_score": "0.20",
        "picker_weight_drawdown": "0.15",
        "picker_weight_signal_confidence": "0.10",
        "picker_weight_recency_of_last_win": "0.05",
        "picker_weight_parameter_freshness": "0.05",
    }

    # ── Live trading loop ──────────────────────────────────────────────────
    live_trading_defaults: dict[str, str] = {
        "live_trading_interval_seconds": "60",   # poll every 60 s
        "live_trading_symbols": "XAUUSD",        # comma-separated symbols
    }

    per_strategy_defaults: dict[str, str] = {
        "qualify_threshold_win_rate": "55.0",
        "score_weight_win_rate": "0.6",
        "score_weight_roi": "0.4",
        "backtest_interval_seconds": "300",
        "backtest_timeframes": '["1h","4h","1d"]',
        "backtest_symbols": '["XAUUSD","EURUSD"]',
        "param_step_size": "0.05",
        "range_expansion_months": "6",
        "max_history_months": "36",
    }

    rows_to_insert: list[AppSetting] = []

    def _maybe_add(key: str, value: str) -> None:
        existing = db.scalar(select(AppSetting).where(AppSetting.key == key))
        if not existing:
            rows_to_insert.append(
                AppSetting(key=key, value=value, updated_at=datetime.utcnow())
            )

    for key, value in risk_defaults.items():
        _maybe_add(key, value)

    for key, value in news_defaults.items():
        _maybe_add(key, value)

    for key, value in backtest_globals.items():
        _maybe_add(key, value)

    for key, value in picker_defaults.items():
        _maybe_add(key, value)

    for key, value in live_trading_defaults.items():
        _maybe_add(key, value)

    for strategy_name in STRATEGY_REGISTRY.keys():
        for suffix, value in per_strategy_defaults.items():
            _maybe_add(f"{strategy_name}_{suffix}", value)

    if rows_to_insert:
        db.bulk_save_objects(rows_to_insert)
        db.commit()