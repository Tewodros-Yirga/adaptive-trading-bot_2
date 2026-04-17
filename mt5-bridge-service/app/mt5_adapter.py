import os
import random
from typing import Any

from tenacity import retry, stop_after_attempt, wait_fixed

from .config import settings

try:
    import MetaTrader5 as mt5_native  # type: ignore
except Exception:
    mt5_native = None

try:
    from mt5linux import MetaTrader5 as mt5linux_cls  # type: ignore
except Exception:
    mt5linux_cls = None


class MT5Adapter:
    def __init__(self) -> None:
        self.connected = False
        self.last_error: str | None = None
        self._mt: Any | None = None
        self._backend: str | None = None
        self._resolved_terminal_exe: str | None = None

    def _resolve_terminal_exe(self) -> str:
        """
        Resolve the MetaTrader 5 terminal executable inside the active Wine prefix.
        The terminal install path can vary by prefix/config, so we fall back to a search.
        """

        if self._resolved_terminal_exe:
            return self._resolved_terminal_exe

        configured = settings.mt_terminal_exe
        if configured and os.path.isfile(configured):
            self._resolved_terminal_exe = configured
            return configured

        wineprefix = (os.environ.get("WINEPREFIX") or "/home/wineuser/.wineprefix").rstrip("/")

        derived_candidates = [
            os.path.join(wineprefix, "drive_c", "Program Files", "MetaTrader 5", "terminal64.exe"),
            os.path.join(wineprefix, "drive_c", "Program Files (x86)", "MetaTrader 5", "terminal64.exe"),
        ]
        for c in derived_candidates:
            if os.path.isfile(c):
                self._resolved_terminal_exe = c
                return c

        # If installation path differs, do a bounded search.
        drive_c = os.path.join(wineprefix, "drive_c")
        if os.path.isdir(drive_c):
            for root, _dirs, files in os.walk(drive_c):
                if "terminal64.exe" in files:
                    resolved = os.path.join(root, "terminal64.exe")
                    self._resolved_terminal_exe = resolved
                    return resolved

        self._resolved_terminal_exe = configured or derived_candidates[0]
        return self._resolved_terminal_exe

    def _last_error_repr(self) -> str:
        if self.last_error:
            return self.last_error
        return "unknown error"

    # MT5 (Wine) startup can take a while. Allow enough retries for the first connect
    # so /ready can transition to LIVE without requiring manual re-calls.
    @retry(wait=wait_fixed(2), stop=stop_after_attempt(10), reraise=True)
    def ensure_connection(self) -> None:
        # Avoid repeated reconnect attempts when already connected.
        if self.connected and self._mt is not None:
            return

        terminal_exe = self._resolve_terminal_exe()

        # 1) Try native MetaTrader5 (only works when the python bindings are actually usable in-container)
        if mt5_native is not None:
            try:
                ok = mt5_native.initialize(
                    path=terminal_exe,
                    login=settings.mt_login,
                    password=settings.mt_password,
                    server=settings.mt_server,
                )
                if ok:
                    self._mt = mt5_native
                    self._backend = "native"
                    self.connected = True
                    self.last_error = None
                    return
                self.last_error = f"mt5 native initialize failed: {mt5_native.last_error()}"
            except Exception as exc:
                self.last_error = f"mt5 native initialize exception: {exc}"

        # 2) Try mt5linux (Wine + RPyC). This is the expected path for Linux containers.
        if mt5linux_cls is not None:
            try:
                host_candidates: list[str] = [settings.mt5linux_host]
                if settings.mt5linux_host.strip().lower() == "localhost":
                    # Some environments resolve `localhost` to IPv6 (`::1`) first, while mt5linux
                    # typically binds to IPv4 (`127.0.0.1`).
                    host_candidates.append("127.0.0.1")

                last_exc: Exception | None = None
                client = None
                for h in host_candidates:
                    try:
                        client = mt5linux_cls(host=h, port=settings.mt5linux_port, timeout=300)
                        # mt5linux forwards parameters to the Windows-side MetaTrader5 integration.
                        client.initialize(
                            path=terminal_exe,
                            login=settings.mt_login,
                            password=settings.mt_password,
                            server=settings.mt_server,
                        )
                        info = client.account_info()
                        if info is None:
                            raise RuntimeError("mt5linux account_info returned None")

                        self._mt = client
                        self._backend = "mt5linux"
                        self.connected = True
                        self.last_error = None
                        return
                    except Exception as exc:
                        last_exc = exc
                        continue

                raise RuntimeError(f"mt5linux init failed (all hosts): {last_exc}")
            except Exception as exc:
                self.last_error = f"mt5linux init failed: {exc}"

        self.connected = False
        if not settings.mt_fallback_mode:
            raise RuntimeError(self.last_error or "MT5 backend not available")
        return

    def account(self) -> dict[str, Any]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return {
                "balance": 0.0,
                "equity": 0.0,
                "margin": 0.0,
                "freeMargin": 0.0,
                "mode": "FALLBACK",
                "warning": self.last_error,
            }

        info = self._mt.account_info()
        if info is None:
            raise RuntimeError(f"mt5 account_info failed: {self._last_error_repr()}")
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "freeMargin": info.margin_free,
            "mode": "LIVE",
        }

    def positions(self) -> list[dict[str, Any]]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return []

        rows = self._mt.positions_get()
        if rows is None:
            return []
        out = []
        for p in rows:
            out.append(
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "openPrice": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                }
            )
        return out

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return {
                "ticket": random.randint(100000, 999999),
                "symbol": payload["symbol"],
                "type": payload["type"],
                "volume": payload["volume"],
                "openPrice": 0.0,
                "sl": payload["stopLoss"],
                "tp": payload["takeProfit"],
                "warning": self.last_error,
            }
        symbol = payload["symbol"]
        side = payload["type"].upper()

        tick = self._mt.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol tick unavailable for {symbol}")

        order_type = self._mt.ORDER_TYPE_BUY if side == "BUY" else self._mt.ORDER_TYPE_SELL
        price = tick.ask if side == "BUY" else tick.bid
        request = {
            "action": self._mt.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": payload["volume"],
            "type": order_type,
            "price": price,
            "sl": payload["stopLoss"],
            "tp": payload["takeProfit"],
            "deviation": 20,
            "magic": 26042026,
            "comment": payload.get("comment", "adaptive-bot"),
            "type_time": self._mt.ORDER_TIME_GTC,
            "type_filling": self._mt.ORDER_FILLING_IOC,
        }

        result = self._mt.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send returned None: {self._last_error_repr()}")
        if result.retcode != self._mt.TRADE_RETCODE_DONE:
            raise RuntimeError(f"order_send failed retcode={result.retcode}")
        return {
            "ticket": result.order,
            "symbol": symbol,
            "type": side,
            "volume": payload["volume"],
            "openPrice": price,
            "sl": payload["stopLoss"],
            "tp": payload["takeProfit"],
        }

    def close_position(self, ticket: int, volume: float | None) -> dict[str, Any]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return {"closed": True, "ticket": ticket, "warning": self.last_error}

        positions = self._mt.positions_get(ticket=ticket)
        if not positions:
            return {"closed": False, "ticket": ticket, "error": "position not found"}
        pos = positions[0]
        symbol = pos.symbol

        side_close = self._mt.ORDER_TYPE_SELL if pos.type == self._mt.ORDER_TYPE_BUY else self._mt.ORDER_TYPE_BUY
        tick = self._mt.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol tick unavailable for {symbol}")
        price = tick.bid if side_close == self._mt.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": self._mt.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume or pos.volume,
            "type": side_close,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 26042026,
            "comment": "adaptive-close",
            "type_time": self._mt.ORDER_TIME_GTC,
            "type_filling": self._mt.ORDER_FILLING_IOC,
        }

        result = self._mt.order_send(req)
        if result is None:
            raise RuntimeError(f"close order_send returned None: {self._last_error_repr()}")
        ok = result.retcode == self._mt.TRADE_RETCODE_DONE
        return {"closed": ok, "ticket": ticket, "retcode": result.retcode}


adapter = MT5Adapter()
