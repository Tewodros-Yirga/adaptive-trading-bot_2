import random

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings


class MT5BridgeClient:
    def __init__(self) -> None:
        headers = {"Content-Type": "application/json", "X-Bridge-Secret": settings.mt_bridge_secret}
        if settings.mt_bridge_hf_token:
            headers["Authorization"] = f"Bearer {settings.mt_bridge_hf_token}"
        self._client = httpx.Client(
            base_url=settings.mt_bridge_url,
            timeout=10.0,
            headers=headers,
        )

    @retry(wait=wait_exponential(multiplier=0.5, min=0.5, max=3), stop=stop_after_attempt(3), reraise=True)
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        response = self._client.request(method, path, json=payload)
        response.raise_for_status()
        return response.json()

    def place_order(self, payload: dict) -> dict:
        if settings.simulation_mode:
            ticket = random.randint(100000, 999999)
            return {
                "orderId": str(ticket),
                "symbol": payload["symbol"],
                "direction": payload["direction"],
                "volume": payload["lot_size"],
                "openPrice": payload["price"],
                "stopLoss": payload["stop_loss"],
                "takeProfit": payload["take_profit"],
                "simulated": True,
            }
        data = self._request(
            "POST",
            "/order",
            {
                "symbol": payload["symbol"],
                "type": payload["direction"],
                "volume": payload["lot_size"],
                "stopLoss": payload["stop_loss"],
                "takeProfit": payload["take_profit"],
                "comment": "adaptive-bot-python",
            },
        )
        return {
            "orderId": str(data.get("ticket")),
            "symbol": data.get("symbol", payload["symbol"]),
            "direction": data.get("type", payload["direction"]),
            "volume": data.get("volume", payload["lot_size"]),
            "openPrice": data.get("openPrice", payload["price"]),
            "stopLoss": data.get("sl", payload["stop_loss"]),
            "takeProfit": data.get("tp", payload["take_profit"]),
            "simulated": False,
        }

    def close_position(self, ticket: int, lot_size: float) -> dict:
        if settings.simulation_mode:
            return {"closed": True, "ticket": ticket, "simulated": True}
        return self._request("POST", "/close", {"ticket": ticket, "volume": lot_size})

    def get_account(self) -> dict:
        if settings.simulation_mode:
            return {"balance": 10000, "equity": 10000, "margin": 0, "freeMargin": 10000, "mode": "SIMULATION"}
        return self._request("GET", "/account")

    def get_positions(self) -> list:
        if settings.simulation_mode:
            return []
        data = self._request("GET", "/positions")
        return data.get("positions", data)


bridge_client = MT5BridgeClient()
