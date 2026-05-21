from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    symbol: str
    type: str = Field(description="BUY or SELL")
    volume: float
    stopLoss: float
    takeProfit: float
    comment: str | None = "adaptive-bot"


class LimitOrderRequest(BaseModel):
    symbol: str
    type: str = Field(description="BUY_LIMIT, SELL_LIMIT, BUY_STOP, or SELL_STOP")
    volume: float
    price: float = Field(description="Pending order trigger price")
    stopLoss: float = 0.0
    takeProfit: float = 0.0
    comment: str | None = "adaptive-bot"
    expiration: str | None = Field(
        default=None,
        description="Optional expiration datetime ISO string e.g. 2024-06-01T12:00:00"
    )


class CloseRequest(BaseModel):
    ticket: int
    volume: float | None = None


class ModifyRequest(BaseModel):
    ticket: int
    stopLoss: float | None = Field(default=None, description="New stop loss; omit to leave unchanged")
    takeProfit: float | None = Field(default=None, description="New take profit; omit to leave unchanged")


class CandlesRequest(BaseModel):
    symbol: str
    timeframe: str = Field(default="1h", description="e.g. 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w")
    from_date: str = Field(description="ISO date string e.g. 2024-01-01")
    to_date: str = Field(description="ISO date string e.g. 2024-12-31")