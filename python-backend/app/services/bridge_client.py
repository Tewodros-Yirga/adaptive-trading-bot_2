import random
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from ..config import settings


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectError))


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
        self._candle_client = httpx.Client(
            base_url=settings.mt_bridge_url,
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=5.0),
            headers=headers,
        )

    @retry(retry=retry_if_exception(_is_retryable_error), wait=wait_exponential(multiplier=0.5, min=0.5, max=3), stop=stop_after_attempt(3), reraise=True)
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

    def place_limit_order(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        expiration: str | None = None,
    ) -> dict:
        """
        Place a pending limit or stop order on the broker.

        Args:
            symbol: Trading symbol (e.g. "XAUUSD").
            order_type: One of "BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP".
            volume: Lot size for the order.
            price: Trigger price for the pending order.
            stop_loss: Optional stop-loss price.
            take_profit: Optional take-profit price.
            expiration: Optional ISO datetime string for order expiry.

        Returns:
            Dict with order details from the bridge (or simulated response).
        """
        valid_types = {"BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"}
        if order_type not in valid_types:
            raise ValueError(f"order_type must be one of {valid_types}, got {order_type!r}")

        if settings.simulation_mode:
            ticket = random.randint(100000, 999999)
            return {
                "orderId": str(ticket),
                "symbol": symbol,
                "order_type": order_type,
                "volume": volume,
                "price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "expiration": expiration,
                "simulated": True,
            }

        body: dict = {
            "symbol": symbol,
            "type": order_type,
            "volume": volume,
            "price": price,
        }
        if stop_loss is not None:
            body["stopLoss"] = stop_loss
        if take_profit is not None:
            body["takeProfit"] = take_profit
        if expiration is not None:
            body["expiration"] = expiration

        data = self._request("POST", "/order/limit", body)
        return {
            "orderId": str(data.get("ticket")),
            "symbol": data.get("symbol", symbol),
            "order_type": data.get("type", order_type),
            "volume": data.get("volume", volume),
            "price": data.get("openPrice", price),
            "stop_loss": data.get("sl", stop_loss),
            "take_profit": data.get("tp", take_profit),
            "expiration": expiration,
            "simulated": False,
        }

    def close_position(self, ticket: int, lot_size: float, partial: bool = False) -> dict:
        """
        Close (or partially close) a live MT5 position.

        Args:
            ticket: MT5 position ticket number.
            lot_size: Volume to close. For a full close this equals the position volume.
            partial: If True, performs a partial close and the position remains open
                     for the remaining volume.

        Returns:
            Dict with keys: closed, ticket, partial, closedVolume, remainVolume, simulated.
        """
        if settings.simulation_mode:
            return {
                "closed": True,
                "ticket": ticket,
                "partial": False,
                "closedVolume": lot_size,
                "remainVolume": 0.0,
                "simulated": True,
            }
        data = self._request("POST", "/close", {"ticket": ticket, "volume": lot_size, "partial": partial})
        return {
            "closed": data.get("closed", True),
            "ticket": ticket,
            "partial": data.get("partial", partial),
            "closedVolume": data.get("closedVolume", lot_size),
            "remainVolume": data.get("remainVolume", 0.0),
            "simulated": False,
        }

    def modify_position(
        self,
        ticket: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict:
        """
        Modify the stop-loss and/or take-profit of an open MT5 position.

        Pass ``None`` to leave a field unchanged on the broker side.
        Pass ``0.0`` to explicitly remove SL or TP.

        Args:
            ticket: MT5 position ticket number.
            stop_loss: New stop-loss price, or None to leave unchanged.
            take_profit: New take-profit price, or None to leave unchanged.

        Returns:
            Dict with modification result from the bridge (or simulated response).
        """
        if settings.simulation_mode:
            return {"modified": True, "ticket": ticket, "simulated": True}

        payload: dict = {"ticket": ticket}
        if stop_loss is not None:
            payload["stopLoss"] = stop_loss
        if take_profit is not None:
            payload["takeProfit"] = take_profit

        return self._request("POST", "/modify", payload)

    def get_account(self) -> dict:
        if settings.simulation_mode:
            return {"balance": 10000, "equity": 10000, "margin": 0, "freeMargin": 10000, "mode": "SIMULATION"}
        return self._request("GET", "/account")

    def get_positions(self) -> list:
        if settings.simulation_mode:
            return []
        data = self._request("GET", "/positions")
        return data.get("positions", data)

    def get_deals(self, ticket: int, lookback_days: int = 14) -> list[dict]:
        """
        Fetch historical deal records for a closed MT5 position ticket.
        Returns empty list on error (non-fatal for reconciliation purposes).
        """
        if settings.simulation_mode:
            return []
        try:
            data = self._request("GET", f"/deals/{ticket}", payload=None)
            return data.get("deals", [])
        except Exception:
            return []

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        from_date: str,
        to_date: str,
        max_retries: int = 3,
    ) -> list[dict]:
        """
        Fetch OHLCV candle data from the MT5 bridge.
        Uses a 120-second read timeout (fetching months of 15m bars is slow).
        Retries up to max_retries times on transient 5xx/connection errors.
        """
        import time as _time
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = self._candle_client.get(
                    "/candles",
                    params={
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "from_date": from_date,
                        "to_date": to_date,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data.get("candles", data) if isinstance(data, dict) else data
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                _time.sleep(2 ** attempt)   # 1s, 2s, 4s
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (502, 503, 504):  # 503 = MT5 not connected yet
                    last_exc = exc
                    _time.sleep(2 ** attempt)
                else:
                    raise  # 4xx errors — don't retry
        raise last_exc or RuntimeError(f"get_candles failed after {max_retries} attempts")


bridge_client = MT5BridgeClient()