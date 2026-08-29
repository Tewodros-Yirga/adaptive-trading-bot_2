// ── Continuous Backtest ───────────────────────────────────────────────────
export interface BacktestCandidate {
    id: number
    strategy_name: string
    params_json: Record<string, unknown>
    search_context_json: {
        timeframe: string
        date_range: [string, string]
        symbol: string
        iteration_number: number
        generation_method: string
    }
    win_rate: number
    roi_pct: number
    profit_factor: number
    composite_score: number
    qualified: boolean
    promoted: boolean
    score_delta: number
    evaluated_at: string
}

export interface SearchStatus {
    strategy_name: string
    current_phase: number
    iterations_run: number
    best_composite_score: number
    is_paused: boolean
    is_running: boolean
    last_promotion_at: string | null
}

// ── Batch Backtest ────────────────────────────────────────────────────────
export interface BacktestBatch {
    id: number
    batch_id: string
    strategy_names: string[]
    status: 'PENDING' | 'RUNNING' | 'COMPLETE' | 'PARTIAL_FAILURE'
    cross_analysis_json: CrossAnalysis | null
    created_at: string
    completed_at: string | null
}

export interface CrossAnalysis {
    ranked_by_composite_score: Array<{
        strategy_name: string
        composite_score: number
        win_rate: number
        roi_pct: number
    }>
    correlation_matrix: Record<string, Record<string, number>>
    complementary_pairs: Array<{
        pair: string[]
        combined_win_rate: number
        correlation: number
    }>
    dominant_strategy: string
    ensemble_simulation: EnsembleSimulation
}

export interface EnsembleSimulation {
    simulated_equity_curve: Array<{ date: string; equity: number }>
    win_rate: number
    roi_pct: number
    profit_factor: number
    max_drawdown_pct: number
    total_trades: number
}

export interface StrategyPairAnalysis {
    id: number
    batch_id: string
    strategy_names_json: string[]
    combination_type: 'pair' | 'triple' | 'all'
    combined_win_rate: number
    combined_roi_pct: number
    combined_composite_score: number
    agreement_rate: number
    synergy_score: number
    recommended: boolean
    analysis_json: {
        narrative: string
        works_well_when: string
        watch_out_for: string
    } | null
}

export interface BatchReport {
    batch: BacktestBatch
    individual_results: BacktestResult[]
    cross_analysis: CrossAnalysis
    pair_analyses: StrategyPairAnalysis[]
}

// ── Backtest Result ───────────────────────────────────────────────────────
export interface BacktestResult {
    id: number
    strategy_name: string
    symbol: string
    from_date: string
    to_date: string
    params: Record<string, unknown>
    initial_balance: number
    leverage: number
    risk_per_trade_pct: number
    status: 'PENDING' | 'RUNNING' | 'COMPLETE' | 'FAILED'
    metrics: BacktestMetrics | null
    equity_curve: Array<{ date: string; equity: number }>
    batch_id: string | null
    created_at: string
    completed_at: string | null
}

export interface BacktestMetrics {
    total_trades: number
    wins: number
    losses: number
    win_rate: number
    profit_factor: number
    total_pnl: number
    total_return_pct: number
    max_drawdown_pct: number
    sharpe_ratio: number
    sortino_ratio: number
    calmar_ratio: number
    expectancy: number
    avg_rr: number
    consecutive_wins: number
    consecutive_losses: number
    final_balance: number
    error?: string
}

export interface TradeLogEntry {
    index: number
    entry_time: string
    exit_time: string
    direction: 'BUY' | 'SELL'
    entry_price: number
    exit_price: number
    stop_loss: number
    pnl: number
    duration_minutes: number
    exit_reason: string
}

export interface MonthlyBreakdown {
    [yearMonth: string]: {
        wins: number
        losses: number
        net_pnl: number
    }
}

export interface ParameterEvolution {
    adaptation_events: Array<{
        after_trade_index: number
        win_rate_at_time: number
        param_deltas: Record<string, number>
    }>
}

// ── Ensemble ──────────────────────────────────────────────────────────────
export interface EnsembleDecision {
    id: number
    symbol: string
    timestamp: string
    resolved_direction: 'BUY' | 'SELL' | null
    resolved_confidence: number
    trade_id: number | null
    strategy_votes_json: StrategyVote[]   // returned as 'strategy_votes' by API — alias kept for DB model
    strategy_votes?: StrategyVote[]       // API response field name
    final_entry: number | null
    news_blocked: boolean
    risk_blocked: boolean
    block_reason: string | null
}

export interface StrategyVote {
    strategy_name: string
    direction: 'BUY' | 'SELL' | null
    raw_confidence: number
    weight: number
    weighted_contribution: number
    is_suspended: boolean
    contributed_to_winning_side: boolean
    // Legacy fields (pre-voter) — may still be present in older records
    confidence?: number
    weighted_vote?: number
    was_agreeing?: boolean
    contributed_to_levels?: boolean
}

export interface VoterSnapshot {
    normalized_weights: Record<string, number>
    suspended_strategies: string[]
    threshold: number
    alchemist_min_weight: number
    alchemist_has_floor: boolean
    using_defaults?: boolean
}

// ── News veto (all that remains of the retired strategy picker) ────────────
export interface NewsVetoDecision {
    id: number
    symbol: string
    timestamp: string
    trade_id: number | null
    veto: boolean
    veto_reason: string | null
    news_influence_json: {
        news_bias: number
        news_confidence: number
        bias_threshold?: number
        veto_threshold?: number
        signaling_strategies?: Record<string, string>
        veto?: boolean
        bias_direction?: string
    }
}

export interface NewsVetoStatus {
    settings: Record<string, string>
    strategy_scores: Record<string, { composite_score: number; live_score: number }>
    stats_7d: {
        total_news_checks: number
        veto_count: number
    }
}

// ── Pagination ────────────────────────────────────────────────────────────
export interface PaginatedResponse<T> {
    total: number
    page: number
    limit: number
    items: T[]
}