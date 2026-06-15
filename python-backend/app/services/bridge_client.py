"""
services/bridge_client.py — MT5 bridge HTTP client with circuit breaker.

Circuit breaker states
----------------------
CLOSED  — normal operation; all requests pass through.
OPEN    — bridge is down; fast-fail with BridgeUnavailableError for
          `_cb_timeout_secs` seconds so background loops don't pile up.
HALF    — after the open window expires, one probe request is allowed;
          success → CLOSED, failure → OPEN again.

This prevents the log flood of "The read operation timed out" that occurs
when the HuggingFace Space is sleeping or restarting.
"""
import threading
import time
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from ..config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class BridgeUnavailableError(RuntimeError):
    """Raised by the circuit breaker when the bridge is known to be down."""


class BridgeRateLimitedError(RuntimeError):
    """
    Raised when the broker is rate-limiting order requests.

    Maps to the bridge's HTTP 429 (MT5 retcode 10024). This is a transient,
    self-resolving condition — callers should back off and retry on the next
    cycle rather than treating it as an unexpected error.
    """


class BridgeAutoTradingDisabledError(BridgeUnavailableError):
    """
    Raised when the MT5 terminal's 'Algo Trading' toggle is OFF (retcode 10027).

    This is a terminal configuration state, NOT a bridge connectivity failure.
    The circuit breaker must NOT be opened for this error — the bridge itself
    is healthy; only the MT5 trade permission is temporarily disabled.
    The start.sh watcher re-enables AutoTrading automatically within seconds.
    """


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class _CircuitBreaker:
    """
    Thread-safe circuit breaker.

    open_threshold  — consecutive failures before opening the circuit.
    timeout_secs    — seconds to wait in OPEN state before probing again.
    """

    _CLOSED = "CLOSED"
    _OPEN   = "OPEN"
    _HALF   = "HALF"

    def __init__(self, open_threshold: int = 3, timeout_secs: float = 30.0):
        self._state = self._CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._threshold = open_threshold
        self._timeout = timeout_secs
        self._lock = threading.Lock()

    # Public helpers ---------------------------------------------------

    def allow(self) -> bool:
        """Return True if the request should be attempted."""
        with self._lock:
            if self._state == self._CLOSED:
                return True
            if self._state == self._OPEN:
                if time.monotonic() - self._opened_at >= self._timeout:
                    self._state = self._HALF
                    return True          # probe attempt
                return False
            # HALF — allow the probe
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self._state != self._CLOSED:
                logger.info("Bridge circuit breaker → CLOSED (recovered)")
            self._state = self._CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == self._HALF or self._failures >= self._threshold:
                if self._state != self._OPEN:
                    logger.warning(
                        "Bridge circuit breaker → OPEN (failures=%d); "
                        "fast-failing for %.0fs",
                        self._failures, self._timeout,
                    )
                self._state  = self._OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        return self._state


# ---------------------------------------------------------------------------
# Retry predicate (only retry on transient network errors, not timeouts)
# ---------------------------------------------------------------------------

def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        # 429: broker rate-limit — the bridge is still retrying MT5; wait and
        # retry from this side too rather than opening the circuit breaker.
        # 502/503/504: transient gateway / server errors.
        return exc.response.status_code in (429, 502, 503, 504)
    # Do NOT retry on ReadTimeout — the circuit breaker handles repeated failures
    return isinstance(exc, (httpx.RemoteProtocolError, httpx.ConnectError))


def _is_autotrading_disabled_response(exc: httpx.HTTPStatusError) -> bool:
    """Return True when a 503 is specifically caused by MT5 AutoTrading being OFF.

    The bridge sets HTTP 503 for both 'bridge not connected' and 'AutoTrading
    disabled by terminal'. Only the latter must NOT trip the circuit breaker —
    the bridge is reachable and healthy; only the MT5 trade permission is off.
    We detect it by inspecting the JSON detail the bridge includes.
    """
    if exc.response.status_code != 503:
        return False
    try:
        detail = exc.response.json().get("detail", "")
        return "autotrading disabled" in detail.lower() or "algo trading" in detail.lower()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class MT5BridgeClient:
    def __init__(self) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-Bridge-Secret": settings.mt_bridge_secret,
        }
        if settings.mt_bridge_hf_token:
            headers["Authorization"] = f"Bearer {settings.mt_bridge_hf_token}"

        # Short timeout for account/positions — these must not block the event loop
        self._client = httpx.Client(
            base_url=settings.mt_bridge_url,
            timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=2.0),
            headers=headers,
        )
        # Order client — must survive the full bridge retry window.
        # The bridge holds _ORDER_LOCK for up to ~35s (3 retries: 5+10+20s);
        # 60s gives comfortable headroom without hanging the event loop forever.
        self._order_client = httpx.Client(
            base_url=settings.mt_bridge_url,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=2.0),
            headers=headers,
        )
        # Longer timeout for candle fetches (months of 15m data is slow)
        self._candle_client = httpx.Client(
            base_url=settings.mt_bridge_url,
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=5.0),
            headers=headers,
        )

        # Circuit breaker:
        #   open_threshold=5  — tolerate short 10027 bursts before opening.
        #   timeout_secs=90   — give MT5 enough time to cool down before probing.
        # (Previously 3 / 30s — too aggressive; 3 consecutive 429s from one
        # order attempt would open the breaker and block all orders for 30s.)
        self._cb = _CircuitBreaker(open_threshold=5, timeout_secs=90.0)

        # Last known positions cache (used when circuit is OPEN)
        self._cached_positions: list[dict] = []
        self._cached_account: dict = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal request helper (with circuit breaker)
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(_is_retryable_error),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=3),
        stop=stop_after_attempt(2),   # reduced — CB handles sustained failures
        reraise=True,
    )
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self._cb.allow():
            raise BridgeUnavailableError(
                f"Bridge circuit breaker is OPEN — skipping request to {path}"
            )
        try:
            response = self._client.request(method, path, json=payload)
            response.raise_for_status()
            result = response.json()
            self._cb.record_success()
            return result
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            self._cb.record_failure()
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503, 504):
                self._cb.record_failure()
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _error_detail(exc: httpx.HTTPStatusError) -> str:
        """Best-effort extraction of the bridge's JSON `detail` error message."""
        try:
            data = exc.response.json()
            if isinstance(data, dict) and data.get("detail"):
                return str(data["detail"])
        except Exception:
            pass
        return f"HTTP {exc.response.status_code}"

    def _order_post(self, path: str, body: dict) -> dict:
        """
        POST through the long-timeout order client *with* circuit-breaker
        awareness.

        Unlike a bare ``_order_client.post(...)``, this:
          * fast-fails with BridgeUnavailableError while the breaker is OPEN,
            so we don't keep hammering a rate-limited broker every cycle;
          * counts a 429 (broker rate-limit / MT5 10027) as a breaker failure
            so sustained rate-limiting opens the breaker and lets the broker
            cool down, then re-raises a typed BridgeRateLimitedError instead of
            a raw HTTPStatusError that surfaces as a generic "Live loop error".
        """
        if not self._cb.allow():
            raise BridgeUnavailableError(
                f"Bridge circuit breaker is OPEN — skipping order request to {path}"
            )
        try:
            response = self._order_client.post(path, json=body)
            response.raise_for_status()
            result = response.json()
            self._cb.record_success()
            return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                self._cb.record_failure()
                raise BridgeRateLimitedError(
                    f"Broker is rate-limiting orders (429) on {path}; "
                    "will retry next cycle"
                ) from exc
            if exc.response.status_code in (502, 503, 504):
                # 503 with AutoTrading-disabled detail: the bridge is reachable
                # and healthy — MT5's Algo Trading toggle is simply OFF. Do NOT
                # record a circuit-breaker failure; the breaker must stay CLOSED
                # so the next order attempt reaches the bridge normally (the
                # start.sh watcher re-enables AutoTrading within seconds).
                if _is_autotrading_disabled_response(exc):
                    raise BridgeAutoTradingDisabledError(
                        f"Bridge cannot place order on {path}: {self._error_detail(exc)}"
                    ) from exc
                self._cb.record_failure()
                # Other 503/502/504: surface via BridgeUnavailable so the live
                # loop logs the real cause at info level.
                if exc.response.status_code == 503:
                    raise BridgeUnavailableError(
                        f"Bridge cannot place order on {path}: {self._error_detail(exc)}"
                    ) from exc
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError):
            self._cb.record_failure()
            raise

    def place_order(self, payload: dict) -> dict:
        # Use the long-timeout order client so the request survives the full
        # MT5 retry window inside the bridge (~35s worst case).
        body = {
            "symbol": payload["symbol"],
            "type": payload["direction"],
            "volume": payload["lot_size"],
            "stopLoss": payload["stop_loss"],
            "takeProfit": payload["take_profit"],
            "comment": "adaptive-bot-python",
        }
        # Idempotency key — the bridge can de-duplicate retried POSTs so a
        # timeout-then-retry never produces two fills.
        if payload.get("idempotency_key"):
            body["idempotencyKey"] = payload["idempotency_key"]
        data = self._order_post("/order", body)
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
        valid_types = {"BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"}
        if order_type not in valid_types:
            raise ValueError(f"order_type must be one of {valid_types}, got {order_type!r}")

        body: dict = {"symbol": symbol, "type": order_type, "volume": volume, "price": price}
        if stop_loss is not None:
            body["stopLoss"] = stop_loss
        if take_profit is not None:
            body["takeProfit"] = take_profit
        if expiration is not None:
            body["expiration"] = expiration

        # Limit/stop orders also go through MT5 order_send — use the long-timeout
        # order client (via _order_post) for the same reason as place_order.
        data = self._order_post("/order/limit", body)
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
        # Use the long-timeout order client — closing a position also goes
        # through MT5 order_send and may be subject to the same 10027 retries.
        data = self._order_post(
            "/close",
            {"ticket": ticket, "volume": lot_size, "partial": partial},
        )
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
        payload: dict = {"ticket": ticket}
        if stop_loss is not None:
            payload["stopLoss"] = stop_loss
        if take_profit is not None:
            payload["takeProfit"] = take_profit

        return self._request("POST", "/modify", payload)

    def get_account(self) -> dict:
        try:
            result = self._request("GET", "/account")
            with self._cache_lock:
                self._cached_account = result
            return result
        except BridgeUnavailableError:
            with self._cache_lock:
                if self._cached_account:
                    logger.debug("Bridge OPEN — returning cached account data")
                    return {**self._cached_account, "cached": True}
            raise

    def get_positions(self) -> list:
        try:
            data = self._request("GET", "/positions")
            positions = data.get("positions", data) if isinstance(data, dict) else data
            with self._cache_lock:
                self._cached_positions = positions
            return positions
        except BridgeUnavailableError:
            with self._cache_lock:
                if self._cached_positions:
                    logger.debug("Bridge OPEN — returning cached positions")
                    return self._cached_positions
            raise

    def get_orders(self) -> list:
        """List live pending orders. Not cached: acting on stale pending-order
        state is unsafe, so a bridge outage propagates and the caller skips."""
        data = self._request("GET", "/orders")
        return data.get("orders", data) if isinstance(data, dict) else data

    def get_deals(self, ticket: int, lookback_days: int = 14) -> list[dict]:
        try:
            data = self._request("GET", f"/deals/{ticket}", payload=None)
            return data.get("deals", [])
        except Exception:
            return []

    def cancel_order(self, ticket: int) -> dict:
        """Cancel a pending (limit/stop) order — item 16."""
        return self._request("POST", "/order/cancel", {"ticket": ticket})

    def get_tick(self, symbol: str) -> dict:
        """Current bid/ask/spread for a symbol — item 11 (spread guard source)."""
        return self._request("GET", f"/tick/{symbol}")

    def get_history(self, from_date: str, to_date: str, symbol: str | None = None) -> list[dict]:
        """Closed deals within a date range (optionally by symbol) — item 16."""
        params: dict = {"from_date": from_date, "to_date": to_date}
        if symbol:
            params["symbol"] = symbol
        try:
            response = self._client.get("/history", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("deals", []) if isinstance(data, dict) else (data or [])
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
                time.sleep(2 ** attempt)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (502, 503, 504):
                    last_exc = exc
                    time.sleep(2 ** attempt)
                else:
                    raise
        raise last_exc or RuntimeError(f"get_candles failed after {max_retries} attempts")

    @property
    def circuit_state(self) -> str:
        return self._cb.state

    @property
    def is_available(self) -> bool:
        return self._cb.allow()


bridge_client = MT5BridgeClient()