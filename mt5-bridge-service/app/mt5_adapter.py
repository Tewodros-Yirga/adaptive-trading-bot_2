import os
import random
import time
from typing import Any

from .config import settings

try:
    import MetaTrader5 as mt5_native  # type: ignore
except Exception:
    mt5_native = None

try:
    from mt5linux import MetaTrader5 as mt5linux_cls  # type: ignore
except Exception:
    mt5linux_cls = None


# ---------------------------------------------------------------------------
# How long to wait before retrying after a failed connection attempt.
# MT5 terminal installation can take 10-15 minutes on cold Render instances,
# so we use a generous backoff ceiling.
# ---------------------------------------------------------------------------
_RETRY_BACKOFF_SECONDS = [5, 10, 20, 30, 60, 120, 180, 300]  # per attempt
_MAX_RETRY_INTERVAL = 300  # cap: retry at most every 5 minutes


class MT5Adapter:
    def __init__(self) -> None:
        self.connected = False
        self.last_error: str | None = None
        self.last_error_class: str | None = None
        self._mt: Any | None = None
        self._backend: str | None = None
        self._resolved_terminal_exe: str | None = None
        # Time-based retry tracking.
        self._connect_attempts: int = 0
        self._next_connect_at: float = 0.0  # epoch seconds

    def _resolve_terminal_exe(self) -> str:
        """
        Resolve the MetaTrader 5 terminal executable inside the active Wine prefix.
        Checks the sentinel file written by bootstrap first (most reliable), then
        falls back to the configured path and a bounded filesystem search.
        """
        if self._resolved_terminal_exe and os.path.isfile(self._resolved_terminal_exe):
            return self._resolved_terminal_exe

        # 1) Check sentinel file written by bootstrap after install.
        logdir = os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs")
        sentinel_path = os.path.join(logdir, "mt5_terminal_exe.path")
        if os.path.isfile(sentinel_path):
            try:
                sentinel_exe = open(sentinel_path).read().strip()
                if sentinel_exe and os.path.isfile(sentinel_exe):
                    self._resolved_terminal_exe = sentinel_exe
                    return sentinel_exe
            except Exception:
                pass

        # 2) Configured env var.
        configured = settings.mt_terminal_exe
        if configured and os.path.isfile(configured):
            self._resolved_terminal_exe = configured
            return configured

        # 3) Derived candidates from WINEPREFIX.
        wineprefix = (os.environ.get("WINEPREFIX") or "/home/wineuser/.wineprefix").rstrip("/")
        derived_candidates = [
            os.path.join(wineprefix, "drive_c", "Program Files", "MetaTrader 5", "terminal64.exe"),
            os.path.join(wineprefix, "drive_c", "Program Files (x86)", "MetaTrader 5", "terminal64.exe"),
        ]
        for c in derived_candidates:
            if os.path.isfile(c):
                self._resolved_terminal_exe = c
                return c

        # 4) Bounded filesystem search.
        drive_c = os.path.join(wineprefix, "drive_c")
        if os.path.isdir(drive_c):
            for root, _dirs, files in os.walk(drive_c):
                if "terminal64.exe" in files:
                    resolved = os.path.join(root, "terminal64.exe")
                    self._resolved_terminal_exe = resolved
                    return resolved

        # Return the configured/default even if it doesn't exist yet.
        self._resolved_terminal_exe = configured or derived_candidates[0]
        return self._resolved_terminal_exe

    def _last_error_repr(self) -> str:
        return self.last_error or "unknown error"

    @staticmethod
    def _classify_error_text(error_text: str) -> str:
        low = (error_text or "").lower()
        if "-10005" in low or "ipc timeout" in low:
            return "ipc_timeout"
        if "-10003" in low or "x64 not found" in low or "ipc initialize failed" in low:
            return "terminal_not_found"
        if "account_info() returned none" in low or "not be logged in yet" in low:
            return "account_not_ready"
        if "connection refused" in low or "timed out" in low or "host" in low:
            return "rpc_unreachable"
        if "invalid account" in low or "authorization" in low or "login" in low:
            return "auth_failure"
        if "initialize() returned false" in low:
            return "initialize_failed"
        return "unknown"

    def _retry_backoff(self) -> float:
        """Return how many seconds to wait before the next connection attempt."""
        idx = min(self._connect_attempts, len(_RETRY_BACKOFF_SECONDS) - 1)
        return _RETRY_BACKOFF_SECONDS[idx]

    def ensure_connection(self) -> None:
        """
        Attempt to connect to MT5 (native or via mt5linux RPyC).

        Uses time-based cooldown instead of a fixed attempt counter so that:
        - A terminal that is still installing will be retried after each cooldown.
        - Already-connected adapters skip the check entirely.
        - A permanently broken setup retries at most every _MAX_RETRY_INTERVAL seconds.
        """
        if self.connected and self._mt is not None:
            return

        now = time.monotonic()
        if now < self._next_connect_at:
            # Still in cooldown — return whatever state we have.
            return

        # Reset per-attempt resolution cache so we re-check the sentinel file
        # (bootstrap may have installed the terminal since the last attempt).
        self._resolved_terminal_exe = None
        self._resolve_terminal_exe()

        self._connect_attempts += 1
        backoff = min(self._retry_backoff(), _MAX_RETRY_INTERVAL)
        self._next_connect_at = now + backoff

        # In deterministic pre-launch mode, fail fast until IPC probe confirms
        # the terminal is attachable. This avoids long RPC waits on every request.
        launch_terminal_enabled = os.environ.get("MT5_LAUNCH_TERMINAL", "false").lower() == "true"
        ipc_ready_file = os.path.join(
            os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs"),
            "mt5_ipc.ready",
        )
        if launch_terminal_enabled and not os.path.isfile(ipc_ready_file):
            self.connected = False
            self.last_error = "mt5 ipc not ready yet"
            self.last_error_class = "ipc_not_ready"
            if not settings.mt_fallback_mode:
                raise RuntimeError(self.last_error)
            return

        # 1) Try native MetaTrader5 (only works when the python bindings are
        #    actually usable in-container, which is rare on Linux).
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
                    self.last_error_class = None
                    self._connect_attempts = 0
                    self._next_connect_at = 0.0
                    return
                self.last_error = f"mt5 native initialize failed: {mt5_native.last_error()}"
                self.last_error_class = self._classify_error_text(self.last_error)
            except Exception as exc:
                self.last_error = f"mt5 native initialize exception: {exc}"
                self.last_error_class = self._classify_error_text(self.last_error)

        # 2) Try mt5linux (Wine + RPyC). Expected path for Linux containers.
        if mt5linux_cls is not None:
            try:
                host_candidates: list[str] = [settings.mt5linux_host]
                if settings.mt5linux_host.strip().lower() == "localhost":
                    # Some environments resolve `localhost` to IPv6 (`::1`) first,
                    # while mt5linux typically binds to IPv4 (`127.0.0.1`).
                    host_candidates.append("127.0.0.1")

                last_exc: Exception | None = None
                client = None
                for h in host_candidates:
                    try:
                        # Use a longer timeout for the RPC call itself: MT5 terminal
                        # can take 60-90s on first launch to connect to the broker.
                        rpc_timeout = int(os.environ.get("MT5_RPC_TIMEOUT_SECONDS", "45"))
                        client = mt5linux_cls(host=h, port=settings.mt5linux_port, timeout=rpc_timeout)

                        # Build credential kwargs.
                        creds: dict = {
                            "login": settings.mt_login,
                            "password": settings.mt_password,
                            "server": settings.mt_server,
                        }

                        # When the terminal is launched in portable mode (/portable),
                        # MetaTrader5.initialize() must also receive portable=True so it
                        # computes the IPC pipe name from the exe directory (portable data
                        # dir) rather than %APPDATA%\MetaQuotes\...  Without this flag,
                        # the pipe lookup always times out with -10005.
                        context_mode = os.environ.get("MT5_CONTEXT_MODE", "default").lower()
                        if context_mode == "portable":
                            creds["portable"] = True

                        ok = False
                        last_init_error = "unknown"
                        # Reliability-first: retry no-path initialize several times
                        # because the MT5 terminal GUI can be up before IPC is attachable.
                        for init_attempt in range(1, 4):
                            ok = client.initialize(**creds)
                            if ok:
                                break

                            err = client.last_error() if hasattr(client, "last_error") else "unknown"
                            last_init_error = str(err)
                            err_class = self._classify_error_text(last_init_error)
                            if err_class == "ipc_timeout" and init_attempt < 3:
                                time.sleep(2 + random.random())
                                continue
                            break

                        if not ok:
                            classified = self._classify_error_text(last_init_error)
                            if classified == "ipc_timeout" and context_mode in {"portable", "data_dir"}:
                                classified = "context_mismatch_suspected"
                            self.last_error_class = classified
                            raise RuntimeError(
                                f"initialize() returned False [{self.last_error_class}]: {last_init_error}"
                            )

                        info = client.account_info()
                        if info is None:
                            raise RuntimeError("account_info() returned None — terminal may not be logged in yet")

                        self._mt = client
                        self._backend = "mt5linux"
                        self.connected = True
                        self.last_error = None
                        self.last_error_class = None
                        self._connect_attempts = 0
                        self._next_connect_at = 0.0
                        return
                    except Exception as exc:
                        last_exc = exc
                        # Clean up the failed client so next attempt starts fresh.
                        try:
                            if client is not None:
                                client.shutdown()
                        except Exception:
                            pass
                        client = None
                        continue

                raise RuntimeError(f"mt5linux init failed (all hosts): {last_exc}")
            except Exception as exc:
                self.last_error = f"mt5linux init failed: {exc}"
                # Preserve more specific classification chosen in inner scope.
                if self.last_error_class != "context_mismatch_suspected":
                    self.last_error_class = self._classify_error_text(self.last_error)

        self.connected = False
        if not settings.mt_fallback_mode:
            raise RuntimeError(self.last_error or "MT5 backend not available")

    def reset_connection(self) -> None:
        """Force the adapter to reconnect on the next request (called externally if needed)."""
        self.connected = False
        self._mt = None
        self.last_error_class = None
        self._connect_attempts = 0
        self._next_connect_at = 0.0
        self._resolved_terminal_exe = None

    def account(self) -> dict[str, Any]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: {self.last_error or 'connection unavailable'}"
            )

        try:
            info = self._mt.account_info()
        except Exception as exc:
            # Connection dropped — force reconnect on next call.
            self.connected = False
            self._mt = None
            raise RuntimeError(f"mt5 account_info exception: {exc}") from exc

        if info is None:
            self.connected = False
            self._mt = None
            raise RuntimeError(f"mt5 account_info failed: {self._last_error_repr()}")

        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "freeMargin": info.margin_free,
            "mode": "LIVE",
            "backend": self._backend,
        }

    def positions(self) -> list[dict[str, Any]]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return []

        try:
            rows = self._mt.positions_get()
        except Exception as exc:
            self.connected = False
            self._mt = None
            raise RuntimeError(f"mt5 positions_get exception: {exc}") from exc

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
