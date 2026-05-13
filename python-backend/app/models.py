from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
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


class OHLCVCache(Base):
    __tablename__ = "ohlcv_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    interval: Mapped[str] = mapped_column(String, nullable=False)
    from_date: Mapped[str] = mapped_column(String, nullable=False)
    to_date: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
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
