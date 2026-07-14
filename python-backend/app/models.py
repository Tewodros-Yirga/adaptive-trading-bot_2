"""
MongoDB document models — replaces SQLAlchemy ORM models.py.

Each class mirrors the original SQLAlchemy model field names and types.
Documents use MongoDB _id (ObjectId) but expose .id as a string for
compatibility with existing Pydantic schemas.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from bson import ObjectId


def _ensure_str(v) -> str:
    """Return v as a JSON string if it's already a dict/list, else return as-is."""
    if isinstance(v, (dict, list)):
        import json as _json
        return _json.dumps(v)
    return v or ""


def _oid_to_str(v: Any) -> Any:
    """Recursively convert ObjectId values to strings for serialization."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _oid_to_str(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_oid_to_str(item) for item in v]
    return v


def _doc_id(doc: dict) -> str:
    raw = doc.get("_id") or doc.get("id", "")
    return str(raw)


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str
    entry_price: float
    lot_size: float = 0.01
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    pnl: float | None = None
    result: str | None = None
    duration_mins: float | None = None
    atr_at_entry: float | None = None
    ema_fast_at_entry: float | None = None
    ema_slow_at_entry: float | None = None
    params_version: int | None = None
    strategy_name: str | None = "DTC"
    mt5_ticket: int | None = None
    # MT5 account (login) this trade belongs to. Stamped at open from the active
    # account so risk/stats can be scoped per account. None on legacy docs.
    mt5_account: int | None = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    # IDs of the news_items that drove the news bias at decision time. Recorded at
    # open so trade-close learning updates exactly the items that influenced the
    # trade, instead of re-deriving correlation by a fuzzy time/symbol window.
    contributing_news_ids: list[int] = field(default_factory=list)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "Trade":
        doc = dict(doc)
        obj = cls(
            symbol=doc.get("symbol", ""),
            direction=doc.get("direction", ""),
            entry_price=doc.get("entry_price", 0.0),
            lot_size=doc.get("lot_size", 0.01),
            exit_price=doc.get("exit_price"),
            stop_loss=doc.get("stop_loss"),
            take_profit=doc.get("take_profit"),
            pnl=doc.get("pnl"),
            result=doc.get("result"),
            duration_mins=doc.get("duration_mins"),
            atr_at_entry=doc.get("atr_at_entry"),
            ema_fast_at_entry=doc.get("ema_fast_at_entry"),
            ema_slow_at_entry=doc.get("ema_slow_at_entry"),
            params_version=doc.get("params_version"),
            strategy_name=doc.get("strategy_name", "DTC"),
            mt5_ticket=doc.get("mt5_ticket"),
            mt5_account=doc.get("mt5_account"),
            opened_at=doc.get("opened_at", datetime.utcnow()),
            closed_at=doc.get("closed_at"),
            contributing_news_ids=list(doc.get("contributing_news_ids") or []),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# PendingOrder
# ---------------------------------------------------------------------------

@dataclass
class PendingOrder:
    symbol: str
    direction: str                 # BUY or SELL
    order_type: str                # BUY_LIMIT or SELL_LIMIT
    limit_price: float
    lot_size: float = 0.01
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    mt5_ticket: int | None = None
    mt5_account: int | None = None  # MT5 account (login) this order belongs to
    strategy_name: str | None = None
    confidence: float | None = None  # resolved signal confidence at placement
    status: str = "PENDING"        # PENDING / FILLED / CANCELLED / EXPIRED
    cancel_reason: str | None = None
    ensemble_decision_id: int | None = None
    params_version: int | None = None
    contributing_news_ids: list[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "PendingOrder":
        doc = dict(doc)
        obj = cls(
            symbol=doc.get("symbol", ""),
            direction=doc.get("direction", ""),
            order_type=doc.get("order_type", ""),
            limit_price=doc.get("limit_price", 0.0),
            lot_size=doc.get("lot_size", 0.01),
            stop_loss=doc.get("stop_loss"),
            tp1=doc.get("tp1"),
            tp2=doc.get("tp2"),
            mt5_ticket=doc.get("mt5_ticket"),
            mt5_account=doc.get("mt5_account"),
            strategy_name=doc.get("strategy_name"),
            confidence=doc.get("confidence"),
            status=doc.get("status", "PENDING"),
            cancel_reason=doc.get("cancel_reason"),
            ensemble_decision_id=doc.get("ensemble_decision_id"),
            params_version=doc.get("params_version"),
            contributing_news_ids=list(doc.get("contributing_news_ids") or []),
            created_at=doc.get("created_at", datetime.utcnow()),
            resolved_at=doc.get("resolved_at"),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# ParameterVersion
# ---------------------------------------------------------------------------

@dataclass
class ParameterVersion:
    version: int
    params_json: str
    reason: str | None = None
    trigger: str | None = None
    confidence_score: float | None = None
    delta_magnitude: float | None = None
    rollback_from_version: int | None = None
    strategy_name: str | None = "DTC"
    created_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "ParameterVersion":
        doc = dict(doc)
        obj = cls(
            version=doc.get("version", 0),
            params_json=_ensure_str(doc.get("params_json", "{}")),
            reason=doc.get("reason"),
            trigger=doc.get("trigger"),
            confidence_score=doc.get("confidence_score"),
            delta_magnitude=doc.get("delta_magnitude"),
            rollback_from_version=doc.get("rollback_from_version"),
            strategy_name=doc.get("strategy_name", "DTC"),
            created_at=doc.get("created_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# AdaptationLog
# ---------------------------------------------------------------------------

@dataclass
class AdaptationLog:
    trades_evaluated: int
    win_rate: float
    profit_factor: float
    avg_atr: float | None
    actions_taken: str
    new_params_version: int
    confidence_score: float | None = None
    delta_magnitude: float | None = None
    rollback_triggered: int = 0
    strategy_name: str | None = "DTC"
    evaluated_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "AdaptationLog":
        doc = dict(doc)
        obj = cls(
            trades_evaluated=doc.get("trades_evaluated", 0),
            win_rate=doc.get("win_rate", 0.0),
            profit_factor=doc.get("profit_factor", 0.0),
            avg_atr=doc.get("avg_atr"),
            actions_taken=doc.get("actions_taken", ""),
            new_params_version=doc.get("new_params_version", 0),
            confidence_score=doc.get("confidence_score"),
            delta_magnitude=doc.get("delta_magnitude"),
            rollback_triggered=doc.get("rollback_triggered", 0),
            strategy_name=doc.get("strategy_name", "DTC"),
            evaluated_at=doc.get("evaluated_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# AppSetting
# ---------------------------------------------------------------------------

@dataclass
class AppSetting:
    key: str
    value: str
    updated_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "AppSetting":
        doc = dict(doc)
        obj = cls(
            key=doc.get("key", ""),
            value=doc.get("value", ""),
            updated_at=doc.get("updated_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

@dataclass
class Strategy:
    name: str
    display_name: str
    description: str | None = None
    is_active: bool = False
    is_live: bool = False
    params_json: str = "{}"
    live_score: float = 0.0  # EWMA of realised R-multiples from live trades (0 = neutral)
    # Timeframe the currently-promoted params were backtested/won on. Live trading
    # fetches signal bars on THIS timeframe so live execution matches the backtest
    # that produced these params. None for MTF strategies (requires_mtf=True), which
    # fetch their full timeframe set regardless.
    live_timeframe: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "Strategy":
        doc = dict(doc)
        obj = cls(
            name=doc.get("name", ""),
            display_name=doc.get("display_name", ""),
            description=doc.get("description"),
            is_active=doc.get("is_active", False),
            is_live=doc.get("is_live", False),
            params_json=_ensure_str(doc.get("params_json", "{}")),
            live_score=float(doc.get("live_score") or 0.0),
            live_timeframe=doc.get("live_timeframe"),
            created_at=doc.get("created_at", datetime.utcnow()),
            updated_at=doc.get("updated_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# ShadowSignal
# ---------------------------------------------------------------------------

@dataclass
class ShadowSignal:
    strategy_name: str
    symbol: str
    direction: str
    entry_price: float
    sl: float | None = None
    tp1: float | None = None
    confidence: float | None = None
    signal_time: datetime = field(default_factory=datetime.utcnow)
    would_have_resulted_in: str | None = None
    actual_exit_price: float | None = None
    pnl_hypothetical: float | None = None
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "ShadowSignal":
        doc = dict(doc)
        obj = cls(
            strategy_name=doc.get("strategy_name", ""),
            symbol=doc.get("symbol", ""),
            direction=doc.get("direction", ""),
            entry_price=doc.get("entry_price", 0.0),
            sl=doc.get("sl"),
            tp1=doc.get("tp1"),
            confidence=doc.get("confidence"),
            signal_time=doc.get("signal_time", datetime.utcnow()),
            would_have_resulted_in=doc.get("would_have_resulted_in"),
            actual_exit_price=doc.get("actual_exit_price"),
            pnl_hypothetical=doc.get("pnl_hypothetical"),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# BacktestResult
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    from_date: str
    to_date: str
    params_json: str
    initial_balance: float = 10000.0
    leverage: int = 100
    risk_per_trade_pct: float = 1.0
    metrics_json: str = "{}"
    equity_curve_json: str = "[]"
    status: str = "PENDING"
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    strategy_performance_timeline_json: dict | None = None
    parameter_evolution_log_json: dict | None = None
    trade_log_json: list | None = None
    monthly_breakdown_json: dict | None = None
    drawdown_periods_json: list | None = None
    strategy_signals_log_json: list | None = None
    batch_id: str | None = None
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "BacktestResult":
        doc = dict(doc)
        obj = cls(
            strategy_name=doc.get("strategy_name", ""),
            symbol=doc.get("symbol", ""),
            from_date=doc.get("from_date", ""),
            to_date=doc.get("to_date", ""),
            params_json=_ensure_str(doc.get("params_json", "{}")),
            initial_balance=doc.get("initial_balance", 10000.0),
            leverage=doc.get("leverage", 100),
            risk_per_trade_pct=doc.get("risk_per_trade_pct", 1.0),
            metrics_json=_ensure_str(doc.get("metrics_json", "{}")),
            equity_curve_json=_ensure_str(doc.get("equity_curve_json", "[]")),
            status=doc.get("status", "PENDING"),
            created_at=doc.get("created_at", datetime.utcnow()),
            completed_at=doc.get("completed_at"),
            strategy_performance_timeline_json=doc.get("strategy_performance_timeline_json"),
            parameter_evolution_log_json=doc.get("parameter_evolution_log_json"),
            trade_log_json=doc.get("trade_log_json"),
            monthly_breakdown_json=doc.get("monthly_breakdown_json"),
            drawdown_periods_json=doc.get("drawdown_periods_json"),
            strategy_signals_log_json=doc.get("strategy_signals_log_json"),
            batch_id=doc.get("batch_id"),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# OHLCVCache
# ---------------------------------------------------------------------------

@dataclass
class OHLCVCache:
    symbol: str
    interval: str
    from_date: str
    to_date: str
    data_json: str
    source: str | None = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "OHLCVCache":
        doc = dict(doc)
        obj = cls(
            symbol=doc.get("symbol", ""),
            interval=doc.get("interval", ""),
            from_date=doc.get("from_date", ""),
            to_date=doc.get("to_date", ""),
            data_json=_ensure_str(doc.get("data_json", "[]")),
            source=doc.get("source"),
            fetched_at=doc.get("fetched_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# NewsItem
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    source: str
    headline: str
    summary: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    symbols_mentioned: str = "[]"
    raw_sentiment_score: float | None = None
    ai_sentiment_score: float | None = None
    ai_sentiment_label: str | None = None
    ai_confidence: float | None = None
    market_impact_predicted: float | None = None
    market_impact_actual: float | None = None
    impact_learning_weight: float = 1.0
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "NewsItem":
        doc = dict(doc)
        obj = cls(
            source=doc.get("source", ""),
            headline=doc.get("headline", ""),
            summary=doc.get("summary"),
            url=doc.get("url"),
            published_at=doc.get("published_at"),
            symbols_mentioned=doc.get("symbols_mentioned", "[]"),
            raw_sentiment_score=doc.get("raw_sentiment_score"),
            ai_sentiment_score=doc.get("ai_sentiment_score"),
            ai_sentiment_label=doc.get("ai_sentiment_label"),
            ai_confidence=doc.get("ai_confidence"),
            market_impact_predicted=doc.get("market_impact_predicted"),
            market_impact_actual=doc.get("market_impact_actual"),
            impact_learning_weight=doc.get("impact_learning_weight", 1.0),
            fetched_at=doc.get("fetched_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

@dataclass
class User:
    username: str
    password_hash: str
    role: str = "viewer"
    full_access: bool = False
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "User":
        doc = dict(doc)
        obj = cls(
            username=doc.get("username", ""),
            password_hash=doc.get("password_hash", ""),
            role=doc.get("role", "viewer"),
            full_access=doc.get("full_access", False),
            is_active=doc.get("is_active", True),
            created_at=doc.get("created_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# BacktestCandidate
# ---------------------------------------------------------------------------

@dataclass
class BacktestCandidate:
    strategy_name: str
    params_json: dict
    search_context_json: dict
    win_rate: float | None = None
    roi_pct: float | None = None
    profit_factor: float | None = None
    composite_score: float | None = None
    qualified: bool = False
    promoted: bool = False
    score_delta: float = 0.0
    backtest_result_id: int | None = None
    evaluated_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "BacktestCandidate":
        doc = dict(doc)
        obj = cls(
            strategy_name=doc.get("strategy_name", ""),
            params_json=doc.get("params_json", {}),
            search_context_json=doc.get("search_context_json", {}),
            win_rate=doc.get("win_rate"),
            roi_pct=doc.get("roi_pct"),
            profit_factor=doc.get("profit_factor"),
            composite_score=doc.get("composite_score"),
            qualified=doc.get("qualified", False),
            promoted=doc.get("promoted", False),
            score_delta=doc.get("score_delta", 0.0),
            backtest_result_id=doc.get("backtest_result_id"),
            evaluated_at=doc.get("evaluated_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# BacktestBatch
# ---------------------------------------------------------------------------

@dataclass
class BacktestBatch:
    batch_id: str
    strategy_names: list
    shared_settings_json: dict | None = None
    status: str = "PENDING"
    cross_analysis_json: dict | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "BacktestBatch":
        doc = dict(doc)
        obj = cls(
            batch_id=doc.get("batch_id", ""),
            strategy_names=doc.get("strategy_names", []),
            shared_settings_json=doc.get("shared_settings_json"),
            status=doc.get("status", "PENDING"),
            cross_analysis_json=doc.get("cross_analysis_json"),
            created_at=doc.get("created_at", datetime.utcnow()),
            completed_at=doc.get("completed_at"),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# StrategyPairAnalysis
# ---------------------------------------------------------------------------

@dataclass
class StrategyPairAnalysis:
    batch_id: str
    strategy_names_json: list
    combination_type: str
    combined_win_rate: float | None = None
    combined_roi_pct: float | None = None
    combined_profit_factor: float | None = None
    combined_composite_score: float | None = None
    individual_scores_json: dict | None = None
    agreement_rate: float | None = None
    disagreement_win_rate: float | None = None
    correlation: float | None = None
    synergy_score: float | None = None
    recommended: bool = False
    analysis_json: dict | None = None
    computed_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "StrategyPairAnalysis":
        doc = dict(doc)
        obj = cls(
            batch_id=doc.get("batch_id", ""),
            strategy_names_json=doc.get("strategy_names_json", []),
            combination_type=doc.get("combination_type", "pair"),
            combined_win_rate=doc.get("combined_win_rate"),
            combined_roi_pct=doc.get("combined_roi_pct"),
            combined_profit_factor=doc.get("combined_profit_factor"),
            combined_composite_score=doc.get("combined_composite_score"),
            individual_scores_json=doc.get("individual_scores_json"),
            agreement_rate=doc.get("agreement_rate"),
            disagreement_win_rate=doc.get("disagreement_win_rate"),
            correlation=doc.get("correlation"),
            synergy_score=doc.get("synergy_score"),
            recommended=doc.get("recommended", False),
            analysis_json=doc.get("analysis_json"),
            computed_at=doc.get("computed_at", datetime.utcnow()),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# EnsembleDecision
# ---------------------------------------------------------------------------

@dataclass
class EnsembleDecision:
    symbol: str
    timestamp: datetime
    resolved_direction: str | None = None
    resolved_confidence: float | None = None
    trade_id: int | None = None
    strategy_votes_json: list | None = None
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
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "EnsembleDecision":
        doc = dict(doc)
        obj = cls(
            symbol=doc.get("symbol", ""),
            timestamp=doc.get("timestamp", datetime.utcnow()),
            resolved_direction=doc.get("resolved_direction"),
            resolved_confidence=doc.get("resolved_confidence"),
            trade_id=doc.get("trade_id"),
            strategy_votes_json=doc.get("strategy_votes_json"),
            final_entry=doc.get("final_entry"),
            final_sl=doc.get("final_sl"),
            final_tp1=doc.get("final_tp1"),
            final_tp2=doc.get("final_tp2"),
            final_tp3=doc.get("final_tp3"),
            final_tp4=doc.get("final_tp4"),
            news_bias=doc.get("news_bias"),
            news_blocked=doc.get("news_blocked", False),
            risk_blocked=doc.get("risk_blocked", False),
            block_reason=doc.get("block_reason"),
        )
        obj.id = _doc_id(doc)
        return obj


# ---------------------------------------------------------------------------
# NewsVetoDecision
#
# Audit record for the news-veto check — the only surviving piece of the old
# strategy picker. The EnsembleVoter is now the sole direction authority; this
# only records whether news blocked the trade. (Replaces the former
# StrategyPickerDecision; the picker's factor-scoring fields are gone.)
# ---------------------------------------------------------------------------

@dataclass
class NewsVetoDecision:
    symbol: str
    timestamp: datetime
    trade_id: int | None = None
    veto: bool = False
    veto_reason: str | None = None
    news_influence_json: dict | None = None
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return _oid_to_str(d)

    @classmethod
    def from_doc(cls, doc: dict) -> "NewsVetoDecision":
        doc = dict(doc)
        obj = cls(
            symbol=doc.get("symbol", ""),
            timestamp=doc.get("timestamp", datetime.utcnow()),
            trade_id=doc.get("trade_id"),
            veto=doc.get("veto", False),
            veto_reason=doc.get("veto_reason"),
            news_influence_json=doc.get("news_influence_json"),
        )
        obj.id = _doc_id(doc)
        return obj