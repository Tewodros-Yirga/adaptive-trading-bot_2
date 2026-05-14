from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    symbol: str
    type: str = Field(description="BUY or SELL")
    volume: float
    stopLoss: float
    takeProfit: float
    comment: str | None = "adaptive-bot"


class CloseRequest(BaseModel):
    ticket: int
    volume: float | None = None


class CandlesRequest(BaseModel):
    symbol: str
    timeframe: str = Field(default="1h", description="e.g. 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w")
    from_date: str = Field(description="ISO date string e.g. 2024-01-01")
    to_date: str = Field(description="ISO date string e.g. 2024-12-31")
