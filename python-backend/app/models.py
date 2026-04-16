from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
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
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
