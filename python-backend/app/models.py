from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_size: Mapped[float] = mapped_column(Float, default=0.01)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_mins: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_fast_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_slow_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    params_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_name: Mapped[str | None] = mapped_column(String, nullable=True, default="DTC")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ParameterVersion(Base):
    __tablename__ = "parameter_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    rollback_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_name: Mapped[str | None] = mapped_column(String, nullable=True, default="DTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdaptationLog(Base):
    __tablename__ = "adaptation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trades_evaluated: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    profit_factor: Mapped[float] = mapped_column(Float)
    avg_atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    actions_taken: Mapped[str] = mapped_column(Text)
    new_params_version: Mapped[int] = mapped_column(Integer)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    rollback_triggered: Mapped[int] = mapped_column(Integer, default=0)
    strategy_name: Mapped[str | None] = mapped_column(String, nullable=True, default="DTC")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ShadowSignal(Base):
    __tablename__ = "shadow_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    would_have_resulted_in: Mapped[str | None] = mapped_column(String, nullable=True)
    actual_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_hypothetical: Mapped[float | None] = mapped_column(Float, nullable=True)


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    from_date: Mapped[str] = mapped_column(String, nullable=False)
    to_date: Mapped[str] = mapped_column(String, nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    initial_balance: Mapped[float] = mapped_column(Float, default=10000)
    leverage: Mapped[int] = mapped_column(Integer, default=100)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=1.0)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    equity_curve_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Extended report columns (added in migration 007) ──────────────────────
    strategy_performance_timeline_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parameter_evolution_log_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trade_log_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    monthly_breakdown_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    drawdown_periods_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    strategy_signals_log_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class OHLCVCache(Base):
    __tablename__ = "ohlcv_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    interval: Mapped[str] = mapped_column(String, nullable=False)
    from_date: Mapped[str] = mapped_column(String, nullable=False)
    to_date: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    symbols_mentioned: Mapped[str] = mapped_column(Text, default="[]")
    raw_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_sentiment_label: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_impact_predicted: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_impact_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    impact_learning_weight: Mapped[float] = mapped_column(Float, default=1.0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="viewer")
    full_access: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestCandidate(Base):
    """Stores every parameter candidate evaluated by the continuous backtest engine."""
    __tablename__ = "backtest_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    search_context_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    score_delta: Mapped[float] = mapped_column(Float, default=0.0)
    backtest_result_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("backtest_results.id"), nullable=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Batch Backtesting Models
# ---------------------------------------------------------------------------

class BacktestBatch(Base):
    """Groups multiple BacktestResult records from a single batch run request."""
    __tablename__ = "backtest_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    strategy_names: Mapped[list] = mapped_column(JSON, nullable=False)
    shared_settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # PENDING / RUNNING / COMPLETE / PARTIAL_FAILURE
    status: Mapped[str] = mapped_column(String, default="PENDING")
    cross_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StrategyPairAnalysis(Base):
    """Stores pairwise (or triple) combination analysis for a batch backtest."""
    __tablename__ = "strategy_pair_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # e.g. ["RSI_Reversal", "MACD_Momentum"]
    strategy_names_json: Mapped[list] = mapped_column(JSON, nullable=False)
    # "pair" / "triple" / "all"
    combination_type: Mapped[str] = mapped_column(String, nullable=False)
    combined_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    combined_roi_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    combined_profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    combined_composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    individual_scores_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agreement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    disagreement_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlation: Mapped[float | None] = mapped_column(Float, nullable=True)
    synergy_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    # Claude-generated narrative JSON: {"narrative": "...", "works_well_when": "...", "watch_out_for": "..."}
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Ensemble Decision Logging
# ---------------------------------------------------------------------------

class EnsembleDecision(Base):
    """
    Logs every orchestrator signal evaluation — whether or not a trade opens.

    strategy_votes_json shape (list of dicts):
      {
        "strategy_name": str,
        "direction": "BUY" | "SELL" | null,
        "confidence": float,
        "weight": float,
        "weighted_vote": float,          # weight * confidence
        "proposed_entry": float | null,
        "proposed_sl": float | null,
        "proposed_tp1": float | null,
        "was_agreeing": bool,            # agreed with resolved_direction
        "contributed_to_levels": bool,   # was_agreeing and weight > 0
      }
    """
    __tablename__ = "ensemble_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_direction: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("trades.id"), nullable=True
    )
    # Per-strategy vote detail list (see docstring above)
    strategy_votes_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    final_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_tp2: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_tp3: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_tp4: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_bias: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# Strategy Picker Models (added in migration 005)
# ---------------------------------------------------------------------------

class StrategyPickerDecision(Base):
    """
    Records every picker evaluation — which strategies were scored and selected,
    the ensemble weights used, and the optional Claude-generated reasoning.

    strategy_scores_json shape (list of dicts):
      {
        "strategy_name": str,
        "total_score": float,
        "score_components": {
            "recent_win_rate": float,
            "profit_factor": float,
            "backtest_composite_score": float,
            "drawdown": float,
            "signal_confidence": float,
            "recency_of_last_win": float,
            "parameter_freshness": float,
        },
        "selected": bool,
      }
    """
    __tablename__ = "strategy_picker_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # Full scored list for all evaluated strategies
    strategy_scores_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Names of the strategies that were actually selected to trade
    selected_strategies_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Normalised weights used for the ensemble vote  {strategy_name: weight}
    ensemble_weights_used_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # FK to the trade that was opened (None if no trade opened)
    trade_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("trades.id"), nullable=True
    )
    # News bias / adjustment detail
    news_influence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Confidence of the resolved direction (0.0–1.0)
    picker_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Optional Claude-generated 1-2 sentence explanation
    reasoning: Mapped[str | None] = mapped_column(String, nullable=True)


class PickerWeightHistory(Base):
    """
    Audit trail of online weight updates after each closed trade.
    One row per trade that triggered a weight update.
    """
    __tablename__ = "picker_weight_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trades.id"), nullable=False, index=True
    )
    weights_before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weights_after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weight_deltas_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # "WIN" or "LOSS"
    trade_result: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)