from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WebhookPayload(BaseModel):
    secret: str = ""
    signal: str
    symbol: str | None = None
    price: float = 0
    atr: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None


class TradeOut(BaseModel):
    id: int
    symbol: str
    direction: str
    entry_price: float
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    lot_size: float
    pnl: float | None = None
    result: str | None = None
    duration_mins: float | None = None
    atr_at_entry: float | None = None
    ema_fast_at_entry: float | None = None
    ema_slow_at_entry: float | None = None
    params_version: int | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None


class ParamsHistoryOut(BaseModel):
    version: int
    params: dict[str, Any]
    reason: str | None = None
    trigger: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Continuous Backtest / Candidate schemas
# ---------------------------------------------------------------------------

class BacktestCandidateOut(BaseModel):
    id: int
    strategy_name: str
    params_json: dict[str, Any]
    search_context_json: dict[str, Any]
    win_rate: float | None = None
    roi_pct: float | None = None
    profit_factor: float | None = None
    composite_score: float | None = None
    qualified: bool = False
    promoted: bool = False
    score_delta: float = 0.0
    backtest_result_id: int | None = None
    evaluated_at: datetime | None = None

    class Config:
        from_attributes = True


class SearchStatusOut(BaseModel):
    strategy_name: str
    phase: int
    iteration: int
    best_score: float
    best_params: dict[str, Any]
    is_running: bool
    is_paused: bool


class SearchSettingsIn(BaseModel):
    qualify_threshold_win_rate: float | None = None
    score_weight_win_rate: float | None = None
    score_weight_roi: float | None = None
    backtest_interval_seconds: float | None = None
    backtest_timeframes: list[str] | None = None
    backtest_symbols: list[str] | None = None
    param_step_size: float | None = None
    range_expansion_months: int | None = None
    max_history_months: int | None = None


# ---------------------------------------------------------------------------
# Per-trade detail (trade_log_json entries)
# ---------------------------------------------------------------------------

class MarketContextDetail(BaseModel):
    session: str | None = None
    day_of_week: str | None = None
    was_high_volatility: bool = False


class PerTradeDetail(BaseModel):
    trade_index: int
    opened_at: str | None = None
    closed_at: str | None = None
    symbol: str
    direction: str
    entry_price: float
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    lot_size: float
    pnl: float | None = None
    result: str | None = None
    exit_reason: str | None = None
    duration_minutes: float | None = None
    atr_at_entry: float | None = None
    params_version_at_open: int | None = None
    strategy_signals: list[dict[str, Any]] = []
    news_bias_at_open: float | None = None
    market_context: MarketContextDetail | None = None


# ---------------------------------------------------------------------------
# Backtest result output
# ---------------------------------------------------------------------------

class BacktestResultOut(BaseModel):
    id: int
    strategy_name: str
    symbol: str
    from_date: str
    to_date: str
    params: dict[str, Any] = {}
    initial_balance: float
    leverage: int
    risk_per_trade_pct: float
    status: str
    metrics: dict[str, Any] = {}
    equity_curve: list[dict[str, Any]] = []
    batch_id: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None

    class Config:
        from_attributes = True


class BacktestReportOut(BaseModel):
    """Full extended report combining all sub-schemas for a single BacktestResult."""
    id: int
    strategy_name: str
    symbol: str
    from_date: str
    to_date: str
    params: dict[str, Any] = {}
    initial_balance: float
    leverage: int
    risk_per_trade_pct: float
    status: str
    metrics: dict[str, Any] = {}
    equity_curve: list[dict[str, Any]] = []
    batch_id: str | None = None
    trade_log: list[PerTradeDetail] = []
    monthly_breakdown: dict[str, Any] = {}
    parameter_evolution_log: dict[str, Any] = {}
    drawdown_periods: list[dict[str, Any]] = []
    strategy_performance_timeline: dict[str, Any] = {}
    strategy_signals_log: list[dict[str, Any]] = []
    created_at: datetime | None = None
    completed_at: datetime | None = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Batch backtest schemas
# ---------------------------------------------------------------------------

class SingleBacktestRequest(BaseModel):
    strategy_name: str = "DTC"
    symbol: str = "XAUUSD"
    from_date: str = "2024-01-01"
    to_date: str = "2024-12-31"
    params: dict[str, Any] = {}
    initial_balance: float = 10000.0
    leverage: int = 100
    risk_per_trade_pct: float = 1.0


class BatchBacktestRequest(BaseModel):
    runs: list[SingleBacktestRequest]
    shared_settings: dict[str, Any] = {}


class BatchBacktestResponse(BaseModel):
    batch_id: str
    run_count: int


class BacktestBatchOut(BaseModel):
    id: int
    batch_id: str
    strategy_names: list[str]
    shared_settings_json: dict[str, Any] | None = None
    status: str
    cross_analysis_json: dict[str, Any] | None = None
    results: list[BacktestResultOut] = []
    created_at: datetime | None = None
    completed_at: datetime | None = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Pair analysis schemas
# ---------------------------------------------------------------------------

class StrategyPairAnalysisOut(BaseModel):
    id: int
    batch_id: str
    strategy_names_json: list[str]
    combination_type: str
    combined_win_rate: float | None = None
    combined_roi_pct: float | None = None
    combined_profit_factor: float | None = None
    combined_composite_score: float | None = None
    individual_scores_json: dict[str, Any] | None = None
    agreement_rate: float | None = None
    disagreement_win_rate: float | None = None
    correlation: float | None = None
    synergy_score: float | None = None
    recommended: bool = False
    analysis_json: dict[str, Any] | None = None
    computed_at: datetime | None = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Ensemble decision schemas
# ---------------------------------------------------------------------------

class StrategyVoteDetail(BaseModel):
    strategy_name: str
    direction: str | None = None
    confidence: float = 0.0
    weight: float = 0.0
    weighted_vote: float = 0.0
    proposed_entry: float | None = None
    proposed_sl: float | None = None
    proposed_tp1: float | None = None
    was_agreeing: bool = False
    contributed_to_levels: bool = False


class EnsembleDecisionOut(BaseModel):
    id: int
    symbol: str
    timestamp: datetime
    resolved_direction: str | None = None
    resolved_confidence: float | None = None
    trade_id: int | None = None
    strategy_votes_json: list[dict[str, Any]] | None = None
    final_entry: float | None = None
    final_sl: float | None = None
    final_tp1: float | None = None
    final_tp2: float | None = None
    final_tp3: float | None = None
    final_tp4: float | None = None
    news_bias: float | None = None
    news_blocked: bool = False
    risk_blocked: bool = False
    block_reason: str | None = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Strategy Picker schemas
# ---------------------------------------------------------------------------

class PickerScoreComponentsOut(BaseModel):
    """Per-factor breakdown for a single strategy in a picker decision."""
    recent_win_rate: float = 0.0
    profit_factor: float = 0.0
    backtest_composite_score: float = 0.0
    drawdown: float = 0.0
    signal_confidence: float = 0.0
    recency_of_last_win: float = 0.0
    parameter_freshness: float = 0.0


class PickerStrategyScoreOut(BaseModel):
    """One strategy's full score entry inside a StrategyPickerDecisionOut."""
    strategy_name: str
    total_score: float
    score_components: PickerScoreComponentsOut
    selected: bool = False


class StrategyPickerDecisionOut(BaseModel):
    id: int
    symbol: str
    timestamp: datetime
    strategy_scores_json: list[dict[str, Any]] | None = None
    selected_strategies_json: list[str] | None = None
    ensemble_weights_used_json: dict[str, Any] | None = None
    trade_id: int | None = None
    news_influence_json: dict[str, Any] | None = None
    picker_confidence: float | None = None
    reasoning: str | None = None

    class Config:
        from_attributes = True


class PickerWeightHistoryOut(BaseModel):
    id: int
    trade_id: int
    weights_before_json: dict[str, Any] | None = None
    weights_after_json: dict[str, Any] | None = None
    weight_deltas_json: dict[str, Any] | None = None
    trade_result: str
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class PickerSettingsIn(BaseModel):
    """Payload for POST /picker/settings — all fields optional."""
    picker_max_simultaneous_strategies: int | None = None
    picker_min_score: float | None = None
    picker_secondary_threshold: float | None = None
    picker_lookback_trades: int | None = None
    picker_min_trades_for_scoring: int | None = None
    picker_learning_rate: float | None = None
    picker_recency_lambda: float | None = None
    picker_news_bias_threshold: float | None = None
    picker_news_bonus: float | None = None
    picker_news_penalty: float | None = None
    picker_news_veto_threshold: float | None = None
    # Factor weights — normalised server-side before persisting
    picker_weight_recent_win_rate: float | None = None
    picker_weight_profit_factor: float | None = None
    picker_weight_backtest_composite_score: float | None = None
    picker_weight_drawdown: float | None = None
    picker_weight_signal_confidence: float | None = None
    picker_weight_recency_of_last_win: float | None = None
    picker_weight_parameter_freshness: float | None = None
    picker_weight_context_fit: float | None = None


class PickerStatusOut(BaseModel):
    """Current picker configuration and rolling 7-day selection stats."""
    active_weights: dict[str, float]
    settings: dict[str, Any]
    stats_7d: dict[str, Any]


class PickerPerformanceOut(BaseModel):
    """Aggregate meta-performance of the picker over a rolling window."""
    total_decisions: int
    decisions_with_trades: int
    total_wins: int
    total_losses: int
    win_rate_pct: float
    avg_picker_confidence: float
    weight_update_count: int


# ---------------------------------------------------------------------------
# Alchemist Strategy Parameter Schema
# ---------------------------------------------------------------------------

class AlchemistParamsSchema(BaseModel):
    """
    Typed Pydantic model of Alchemist.DEFAULT_PARAMS.
    Used for frontend parameter editor validation and API request bodies
    sent to /strategies/Alchemist/params.
    """

    # Session / timing
    killzone_filter_enabled: bool = True
    active_killzones: list[str] = ["london_open", "ny_open", "overlap"]
    judas_swing_filter: bool = True

    # MSNR zone detection
    ocl_lookback_candles: int = 5
    ob_lookback_candles: int = 20
    qml_swing_lookback: int = 10
    zone_tolerance_pct: float = 0.0015

    # CRT parameters
    crt_signal_timeframe: str = "1h"
    crt_entry_timeframe: str = "15m"
    crt_sweep_min_pips: float = 3.0
    crt_close_back_required: bool = True

    # HTF bias
    htf_bias_timeframe: str = "4h"
    htf_ema_fast: int = 20
    htf_ema_slow: int = 50

    # Entry precision
    fib_entry_enabled: bool = True
    fib_entry_level: float = 0.618
    fib_tp3_extension: float = 1.272
    fib_tp4_extension: float = 1.618

    # Risk parameters
    atr_sl_buffer: float = 0.5
    atr_period: int = 14
    min_rr_ratio: float = 1.5
    min_confidence_threshold: float = 0.55

    # SMT divergence
    smt_filter_enabled: bool = False
    smt_correlated_symbol: str = "XAUUSD"

    # Structure
    structure_shift_candles: int = 3

    class Config:
        # Allow the model to be constructed from ORM objects or raw dicts
        from_attributes = True




 
class NewsLearningTriggerOut(BaseModel):
    """Response schema for POST /news/learn-now."""
    updated_items: int
    trades_processed: int
 