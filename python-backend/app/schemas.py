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
