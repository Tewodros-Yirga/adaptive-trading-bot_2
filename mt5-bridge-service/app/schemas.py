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
