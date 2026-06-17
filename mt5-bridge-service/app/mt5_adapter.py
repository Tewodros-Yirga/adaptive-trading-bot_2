import os
import random
import threading
import time
import re
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
import logging
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry / backoff constants
# ---------------------------------------------------------------------------
_RETRY_BACKOFF_SECONDS = [5, 10, 20, 30, 60, 120, 180, 300]
_MAX_RETRY_INTERVAL = 300

# ---------------------------------------------------------------------------
# AutoTrading re-enable sentinel.
#
# When order_send returns 10027 (CLIENT_DISABLES_AT), the terminal's Algo
# Trading toggle is OFF. The Python API *cannot* change this toggle — only
# the terminal UI (Ctrl+E keystroke) can. The dismiss loop in start.sh
# periodically sends Ctrl+E, but on a 5-second cycle. To make it react
# instantly, we write a sentinel file that the dismiss loop checks on every
# iteration. When the sentinel is present, the dismiss loop sends Ctrl+E
# immediately and removes it.
# ---------------------------------------------------------------------------
_REENABLE_SENTINEL_DIR = os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs")

# ---------------------------------------------------------------------------
# Global order-send serialisation lock + rate-limit cooldown.
#
# Two distinct MT5 retcodes were historically conflated here:
#   * 10024 TRADE_RETCODE_TOO_MANY_REQUESTS — the real broker throttle. Multiple
#     concurrent callers (one per bot instance) compound the pressure; serialising
#     through this lock + a cooldown after a hit keeps us under the limit.
#   * 10027 TRADE_RETCODE_CLIENT_DISABLES_AT — AutoTrading toggled OFF in the
#     terminal. This is NOT a rate-limit and the lock/cooldown does nothing for
#     it; it is handled separately in _order_send_with_ratelimit.
#
# Fix: serialise all order_send calls through a threading.Lock so only one runs
# at a time, and enforce a minimum inter-order gap after any 10024 hit.
# ---------------------------------------------------------------------------
_ORDER_LOCK = threading.Lock()

# Minimum seconds to wait between successive order_send calls after a 10024.
# Broker docs say the window is typically 1-2 seconds; we use 3s to be safe.
_ORDER_MIN_GAP_AFTER_RATELIMIT = float(os.environ.get("MT5_ORDER_MIN_GAP_SECONDS", "3"))
_last_order_send_at: float = 0.0          # monotonic timestamp of last send
_order_cooldown_until: float = 0.0        # monotonic timestamp: don't send before this

# Wall-clock budget for recovering from an AutoTrading-disabled (10027/10026)
# rejection. The backend's order client reads with a 60s timeout, so all
# re-enable signalling + polling must finish comfortably inside that window.
_DISABLE_RECOVERY_BUDGET_S = float(os.environ.get("MT5_DISABLE_RECOVERY_BUDGET_SECONDS", "40"))


class MT5Adapter:
    def __init__(self) -> None:
        self.connected = False
        self.last_error: str | None = None
        self.last_error_class: str | None = None
        self._mt: Any | None = None
        self._backend: str | None = None
        self._resolved_terminal_exe: str | None = None
        self._connect_attempts: int = 0
        self._next_connect_at: float = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_terminal_exe(self) -> str:
        if self._resolved_terminal_exe and os.path.isfile(self._resolved_terminal_exe):
            return self._resolved_terminal_exe

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
        return self.last_error or "unknown error"

    def _trade_allowed_repr(self) -> str:
        """
        Best-effort read of terminal_info().trade_allowed for diagnostics.

        Returns "True"/"False" when known, or "unknown" if the terminal info
        cannot be read. trade_allowed reflects whether the terminal's
        "Algo Trading" toggle is currently ON.
        """
        try:
            info = self._mt.terminal_info() if self._mt is not None else None
            if info is not None:
                return str(bool(getattr(info, "trade_allowed", False)))
        except Exception:
            pass
        return "unknown"

    def _is_trade_allowed(self) -> bool:
        """Return True if terminal_info().trade_allowed is True."""
        try:
            if self._mt is None:
                return False
            info = self._mt.terminal_info()
            if info is not None:
                return bool(getattr(info, "trade_allowed", False))
        except Exception:
            pass
        return False

    @staticmethod
    def _signal_reenable_autotrading() -> None:
        """
        Write a sentinel file that the dismiss loop in start.sh watches.
        When the dismiss loop sees this file, it immediately sends Ctrl+E
        to the terminal window to re-enable AutoTrading, then removes it.
        """
        sentinel = os.path.join(_REENABLE_SENTINEL_DIR, "mt5_reenable_autotrading")
        try:
            with open(sentinel, "w") as f:
                f.write(f"requested_at={time.time()}\n")
            logger.info("Wrote AutoTrading re-enable sentinel: %s", sentinel)
        except Exception as exc:
            logger.warning("Failed to write re-enable sentinel %s: %s", sentinel, exc)

    def _wait_for_trade_allowed(self, timeout: float = 30.0) -> bool:
        """
        Signal the dismiss loop to send Ctrl+E and poll terminal_info().trade_allowed
        until it becomes True or we run out of time.

        Returns True if trade_allowed became True within the timeout.

        This keeps the existing mt5 connection alive — NO shutdown/reinitialize.
        The root cause of 10027 is that the terminal's AutoTrading toggle is OFF,
        which is a terminal UI state that mt5.initialize() cannot change.
        Only the Ctrl+E keystroke (sent by the dismiss loop) can toggle it.
        """
        self._signal_reenable_autotrading()

        poll_interval = 2.0
        deadline = time.monotonic() + timeout
        polls = 0

        while time.monotonic() < deadline:
            polls += 1
            if self._is_trade_allowed():
                logger.info(
                    "trade_allowed became True after %d polls (%.1fs)",
                    polls, timeout - (deadline - time.monotonic()),
                )
                return True
            # Re-signal every ~8s in case the dismiss loop missed it
            if polls % 4 == 0:
                self._signal_reenable_autotrading()
            time.sleep(poll_interval)

        logger.warning(
            "trade_allowed did not become True within %.0fs (%d polls)",
            timeout, polls,
        )
        return False

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

    def _infer_context_mode(self) -> str:
        logdir = os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs")
        context_status_file = os.path.join(logdir, "mt5_context.status")
        try:
            if os.path.isfile(context_status_file):
                txt = open(context_status_file, "r", encoding="utf-8", errors="replace").read()
                m = re.search(r"mode=([^;]+);", txt)
                if m:
                    return m.group(1).strip().lower()
        except Exception:
            pass
        return os.environ.get("MT5_CONTEXT_MODE", "default").strip().lower()

    def _linux_to_windows_path(self, linux_path: str) -> str | None:
        try:
            wineprefix = (os.environ.get("WINEPREFIX") or "/opt/wineprefix").rstrip("/")
            rel = linux_path
            if linux_path.startswith(wineprefix):
                rel = linux_path[len(wineprefix):]
            m = re.search(r"/drive_([a-zA-Z])/((?:.*)$)", rel)
            if not m:
                return None
            drive = m.group(1).upper()
            sub = m.group(2).replace("/", "\\")
            return f"{drive}:\\{sub}"
        except Exception:
            return None

    def _retry_backoff(self) -> float:
        idx = min(self._connect_attempts, len(_RETRY_BACKOFF_SECONDS) - 1)
        return _RETRY_BACKOFF_SECONDS[idx]

    # ------------------------------------------------------------------
    # Rate-limit aware order_send wrapper
    # ------------------------------------------------------------------

    def _order_send_with_ratelimit(self, request: dict) -> Any:
        """
        Serialise all order_send calls through a process-level lock, and
        classify the trade-server return code correctly.

        IMPORTANT — MT5 trade return codes (enum_trade_return_codes):
          * 10024 TRADE_RETCODE_TOO_MANY_REQUESTS  — *real* rate-limit; the
            broker is throttling us. Transient → cooldown + retry, then 429.
          * 10026 TRADE_RETCODE_SERVER_DISABLES_AT — AutoTrading disabled by
            the SERVER. Not a rate-limit.
          * 10027 TRADE_RETCODE_CLIENT_DISABLES_AT — The terminal's EA
            authorization state is corrupted / stale. Under Wine, this
            happens when a SEPARATE process previously called
            mt5.initialize(login, password, server) and then shutdown(),
            corrupting the shared-memory EA-auth segment via Wine's
            broken MapViewOfFile.

            The Algo Trading button may appear green (Account=0 prevents
            toggle change), but the IPC session has lost trade permission.

            FIX: Call a BARE mt5.initialize() (no credentials, no shutdown)
            on the EXISTING mt5linux connection to refresh the IPC handshake.
            Do NOT pass credentials — that triggers another "account change"
            event which re-poisons the state.
        """
        global _last_order_send_at, _order_cooldown_until

        _RETCODE_DONE              = self._mt.TRADE_RETCODE_DONE
        _RETCODE_TOO_MANY_REQUESTS = 10024   # TRADE_RETCODE_TOO_MANY_REQUESTS — real rate-limit
        _RETCODE_SERVER_DISABLES   = 10026   # TRADE_RETCODE_SERVER_DISABLES_AT — algo off (server)
        _RETCODE_CLIENT_DISABLES   = 10027   # TRADE_RETCODE_CLIENT_DISABLES_AT — stale EA auth
        _MAX_RETRIES = 4

        with _ORDER_LOCK:
            result = None
            # Set on the first 10027/10026 hit; bounds total re-enable time.
            disable_deadline: float | None = None
            for attempt in range(_MAX_RETRIES):
                # Honour any active cooldown before sending
                now = time.monotonic()
                wait = max(0.0, _order_cooldown_until - now)
                if wait > 0:
                    logger.info(
                        "order_send: honouring rate-limit cooldown %.1fs before attempt %d/%d",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)

                _last_order_send_at = time.monotonic()
                result = self._mt.order_send(request)

                if result is None:
                    raise RuntimeError(f"order_send returned None: {self._last_error_repr()}")

                if result.retcode == _RETCODE_DONE:
                    # Reset cooldown on success
                    _order_cooldown_until = 0.0
                    return result

                if result.retcode == _RETCODE_TOO_MANY_REQUESTS:
                    # Real broker throttle. Cap at 20s so total worst-case wait
                    # is 5+10+20=35s, safely inside the backend's 60s order-client
                    # read timeout.
                    sleep_s = min(5 * (2 ** attempt), 20)  # 5, 10, 20
                    # Set global cooldown so the NEXT caller also waits
                    _order_cooldown_until = time.monotonic() + sleep_s
                    if attempt < _MAX_RETRIES - 1:
                        logger.warning(
                            "order_send retcode=10024 (too many requests) — retry %d/%d in %ds",
                            attempt + 1, _MAX_RETRIES, sleep_s,
                        )
                        time.sleep(sleep_s)
                        continue
                    raise TooManyRequestsError(
                        f"order_send retcode=10024 after {_MAX_RETRIES} attempts — "
                        f"broker is rate-limiting order requests"
                    )

                if result.retcode in (_RETCODE_CLIENT_DISABLES, _RETCODE_SERVER_DISABLES):
                    # ── AutoTrading disabled / EA-auth lost ───────────────
                    # Two distinct sub-cases, told apart by terminal_info()
                    # .trade_allowed. The previous code applied a bare
                    # mt5.initialize() to BOTH, but that cannot help the more
                    # common case (a), so orders failed permanently:
                    #
                    #  (a) trade_allowed == False — the terminal's "Algo
                    #      Trading" toggle is OFF. mt5.initialize() CANNOT turn
                    #      it back on; only a Ctrl+E keystroke in the terminal
                    #      UI can. We drop a sentinel file that the start.sh
                    #      dismiss loop watches, it sends Ctrl+E, and we poll
                    #      terminal_info().trade_allowed until it flips True.
                    #
                    #  (b) trade_allowed == True — toggle is on but the order
                    #      was still rejected → the IPC EA-auth segment is
                    #      stale (Wine shared-memory bug). A BARE
                    #      mt5.initialize() (no credentials → no account-change
                    #      re-poison) refreshes the handshake.
                    who = "client terminal" if result.retcode == _RETCODE_CLIENT_DISABLES else "server"
                    if disable_deadline is None:
                        disable_deadline = time.monotonic() + _DISABLE_RECOVERY_BUDGET_S
                    remaining = disable_deadline - time.monotonic()

                    if attempt < _MAX_RETRIES - 1 and remaining > 1.0:
                        trade_allowed = self._is_trade_allowed()
                        logger.warning(
                            "order_send retcode=%d (AutoTrading disabled, %s) — "
                            "recovering (attempt %d/%d, trade_allowed=%s, "
                            "%.0fs budget left)",
                            result.retcode, who, attempt + 1, _MAX_RETRIES,
                            trade_allowed, remaining,
                        )
                        if not trade_allowed:
                            # (a) Toggle OFF → drive the Ctrl+E re-enable via
                            # the dismiss loop and wait for it to take effect.
                            wait_t = min(remaining, 25.0)
                            recovered = self._wait_for_trade_allowed(timeout=wait_t)
                            logger.info(
                                "AutoTrading re-enable %s after retcode=%d",
                                "succeeded" if recovered else "timed out",
                                result.retcode,
                            )
                        else:
                            # (b) Toggle ON but order rejected → stale IPC
                            # EA-auth. Refresh without credentials.
                            try:
                                ok = self._mt.initialize(timeout=30000)
                                logger.info(
                                    "bare mt5.initialize() returned %s after retcode=%d",
                                    ok, result.retcode,
                                )
                            except Exception as _re_exc:
                                logger.warning(
                                    "bare mt5.initialize() failed after retcode=%d: %s",
                                    result.retcode, _re_exc,
                                )
                            time.sleep(2)  # brief pause for terminal to process
                        continue
                    raise AutoTradingDisabledError(
                        f"order_send retcode={result.retcode}: AutoTrading "
                        f"disabled ({who}) — recovery exhausted after "
                        f"{_MAX_RETRIES} attempts / "
                        f"{_DISABLE_RECOVERY_BUDGET_S:.0f}s budget. "
                        f"trade_allowed={self._trade_allowed_repr()}."
                    )

                raise RuntimeError(
                    f"order_send failed retcode={result.retcode} "
                    f"(see https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes)"
                )

            # Should never reach here
            raise RuntimeError("order_send: exhausted retries without result")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def ensure_connection(self) -> None:
        if self.connected and self._mt is not None:
            return

        now = time.monotonic()
        if now < self._next_connect_at:
            return

        self._resolved_terminal_exe = None
        terminal_exe = self._resolve_terminal_exe()

        self._connect_attempts += 1
        backoff = min(self._retry_backoff(), _MAX_RETRY_INTERVAL)
        self._next_connect_at = now + backoff

        launch_terminal_enabled = os.environ.get("MT5_LAUNCH_TERMINAL", "false").lower() == "true"
        ipc_ready_file = os.path.join(
            os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs"),
            "mt5_ipc.ready",
        )
        if launch_terminal_enabled and not os.path.isfile(ipc_ready_file):
            if self._connect_attempts < 3:
                self.connected = False
                self.last_error = "mt5 ipc not ready yet"
                self.last_error_class = "ipc_not_ready"
                if not settings.mt_fallback_mode:
                    raise RuntimeError(self.last_error)
                return

        # 1) Native (Windows only)
        if os.name == "nt" and mt5_native is not None:
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

        # 2) mt5linux (Linux containers)
        if mt5linux_cls is not None:
            try:
                host_candidates: list[str] = [settings.mt5linux_host]
                if settings.mt5linux_host.strip().lower() == "localhost":
                    host_candidates.append("127.0.0.1")

                last_exc: Exception | None = None
                client = None
                for h in host_candidates:
                    try:
                        rpc_timeout = int(os.environ.get("MT5_RPC_TIMEOUT_SECONDS", "90"))
                        client = mt5linux_cls(host=h, port=settings.mt5linux_port, timeout=rpc_timeout)

                        creds: dict = {
                            "login": settings.mt_login,
                            "password": settings.mt_password,
                            "server": settings.mt_server,
                        }

                        context_mode = self._infer_context_mode()
                        portable_flag = context_mode == "portable"
                        if portable_flag:
                            creds["portable"] = True

                        _mismatch_file = os.path.join(
                            os.environ.get("LOGDIR", "/tmp/mt5-logs"),
                            "build_mismatch",
                        )
                        if os.path.isfile(_mismatch_file):
                            try:
                                _mismatch_info = open(_mismatch_file).read().strip()
                            except Exception:
                                _mismatch_info = "unknown"
                            self.last_error_class = "build_mismatch"
                            raise RuntimeError(
                                f"FATAL: terminal/package build mismatch ({_mismatch_info}). "
                                f"Fix: set MT5_PORTABLE_ZIP_URL env var to a portable zip "
                                f"matching the package build, or rebuild the base image. "
                                f"mt5.initialize() will return -10005 every time."
                            )

                        ok = False
                        last_init_error = "unknown"

                        for init_attempt in range(1, 3):
                            init_kwargs: dict[str, Any] = {"timeout": 30000}
                            if portable_flag:
                                init_kwargs["portable"] = True
                            try:
                                ok = client.initialize(**init_kwargs)
                            except TypeError:
                                init_kwargs.pop("portable", None)
                                ok = client.initialize(**init_kwargs)
                            if ok:
                                break
                            err = client.last_error() if hasattr(client, "last_error") else "unknown"
                            last_init_error = str(err)
                            err_class = self._classify_error_text(last_init_error)
                            if err_class == "ipc_timeout" and init_attempt < 2:
                                time.sleep(3)
                                continue
                            break

                        if not ok:
                            for init_attempt in range(1, 3):
                                ok = client.initialize(**creds, timeout=60000)
                                if ok:
                                    break
                                err = client.last_error() if hasattr(client, "last_error") else "unknown"
                                last_init_error = str(err)
                                err_class = self._classify_error_text(last_init_error)
                                if err_class == "ipc_timeout" and init_attempt < 2:
                                    time.sleep(5)
                                    continue
                                break

                        if not ok:
                            classified = self._classify_error_text(last_init_error)
                            if classified == "ipc_timeout" and context_mode in {"portable", "data_dir"}:
                                classified = "context_mismatch_suspected"
                            if classified == "terminal_not_found":
                                terminal_windows_path = self._linux_to_windows_path(terminal_exe)
                                if terminal_windows_path:
                                    init_kwargs = {"timeout": 60000, "path": terminal_windows_path}
                                    if portable_flag:
                                        init_kwargs["portable"] = True
                                    try:
                                        ok = client.initialize(**init_kwargs)
                                    except TypeError:
                                        init_kwargs.pop("portable", None)
                                        ok = client.initialize(**init_kwargs)
                                    if ok:
                                        info = client.account_info()
                                        if info is not None:
                                            self._mt = client
                                            self._backend = "mt5linux"
                                            self.connected = True
                                            self.last_error = None
                                            self.last_error_class = None
                                            self._connect_attempts = 0
                                            self._next_connect_at = 0.0
                                            return

                            self.last_error_class = classified
                            raise RuntimeError(f"initialize() returned False [{self.last_error_class}]: {last_init_error}")

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
                if self.last_error_class not in ("context_mismatch_suspected", "build_mismatch"):
                    self.last_error_class = self._classify_error_text(self.last_error)

        self.connected = False
        if not settings.mt_fallback_mode:
            raise RuntimeError(self.last_error or "MT5 backend not available")

    def reset_connection(self) -> None:
        self.connected = False
        self._mt = None
        self.last_error_class = None
        self._connect_attempts = 0
        self._next_connect_at = 0.0
        self._resolved_terminal_exe = None

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def account(self) -> dict[str, Any]:
        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: {self.last_error or 'connection unavailable'}"
            )
        # Retry once on the existing connection before tearing it down.
        # Transient RPyC hiccups (Wine GC pauses, pipe stalls) should NOT
        # nuke the mt5linux connection — a destroyed connection triggers
        # ensure_connection() which may fall back to credential-based init,
        # re-poisoning the IPC EA-auth state.
        info = None
        last_exc = None
        for _acc_attempt in range(2):
            try:
                info = self._mt.account_info()
                if info is not None:
                    break
                # info is None — terminal might be momentarily busy
                if _acc_attempt == 0:
                    logger.warning("account_info() returned None — retrying in 2s")
                    time.sleep(2)
                    continue
            except Exception as exc:
                last_exc = exc
                if _acc_attempt == 0:
                    logger.warning("account_info() exception (retrying in 2s): %s", exc)
                    time.sleep(2)
                    continue
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
        # Same retry-before-teardown pattern as account() above.
        rows = None
        for _pos_attempt in range(2):
            try:
                rows = self._mt.positions_get()
                break  # success (rows may be None = no positions, which is fine)
            except Exception as exc:
                if _pos_attempt == 0:
                    logger.warning("positions_get() exception (retrying in 2s): %s", exc)
                    time.sleep(2)
                    continue
                self.connected = False
                self._mt = None
                raise RuntimeError(f"mt5 positions_get exception: {exc}") from exc
        if rows is None:
            return []

        def _s(obj, attr, default=0.0):
            # RPyC proxy: missing attributes raise AttributeError across the
            # wire, so we must catch locally rather than use hasattr().
            try:
                return getattr(obj, attr)
            except AttributeError:
                return default

        out = []
        for p in rows:
            try:
                out.append({
                    "ticket":    _s(p, "ticket",     0),
                    "symbol":    _s(p, "symbol",     ""),
                    "type":      "BUY" if _s(p, "type", 0) == 0 else "SELL",
                    "volume":    _s(p, "volume",     0.0),
                    "openPrice": _s(p, "price_open", 0.0),
                    "sl":        _s(p, "sl",         0.0),
                    "tp":        _s(p, "tp",         0.0),
                    "profit":    _s(p, "profit",     0.0),
                })
            except Exception as row_exc:
                logger.warning("skipping malformed position row: %s", row_exc)
                continue
        return out

    def orders(self) -> list[dict[str, Any]]:
        """List live pending orders (BUY_LIMIT / SELL_LIMIT / BUY_STOP / SELL_STOP)."""
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return []
        rows = None
        for _ord_attempt in range(2):
            try:
                rows = self._mt.orders_get()
                break
            except Exception as exc:
                if _ord_attempt == 0:
                    logger.warning("orders_get() exception (retrying in 2s): %s", exc)
                    time.sleep(2)
                    continue
                self.connected = False
                self._mt = None
                raise RuntimeError(f"mt5 orders_get exception: {exc}") from exc
        if rows is None:
            return []

        def _s(obj, attr, default=0.0):
            try:
                return getattr(obj, attr)
            except AttributeError:
                return default

        # MT5 order type ints → names (2-5 are the pending types).
        _TYPE_NAMES = {
            2: "BUY_LIMIT", 3: "SELL_LIMIT", 4: "BUY_STOP", 5: "SELL_STOP",
        }
        out = []
        for o in rows:
            try:
                type_int = int(_s(o, "type", -1))
                # volume_current is the live remaining volume on a pending order.
                volume = _s(o, "volume_current", None)
                if volume is None:
                    volume = _s(o, "volume_initial", 0.0)
                out.append({
                    "ticket":    _s(o, "ticket",     0),
                    "symbol":    _s(o, "symbol",     ""),
                    "type":      _TYPE_NAMES.get(type_int, str(type_int)),
                    "volume":    volume,
                    "price":     _s(o, "price_open", 0.0),
                    "sl":        _s(o, "sl",         0.0),
                    "tp":        _s(o, "tp",         0.0),
                    "timeSetup": _s(o, "time_setup", 0),
                })
            except Exception as row_exc:
                logger.warning("skipping malformed order row: %s", row_exc)
                continue
        return out

    def _prime_history(self, sym: str, mt5_timeframe: Any) -> bool:
        """Force MT5 to start downloading recent history for ``sym``.

        A ``copy_rates_range`` over a historical date window returns nothing
        until the bars exist in the terminal's local cache, and the range query
        alone does not trigger that download. After a binary LiveUpdate the cache
        is cold, so range queries stay empty forever. Requesting bars by position
        from the most-recent end (``copy_rates_from_pos(sym, tf, 0, N)``) is the
        standard idiom that wakes the broker-side history pull; once it lands the
        original range query succeeds. Best-effort — never raises.

        Returns True if the prime call itself returned bars (history is warming).
        """
        try:
            try:
                self._mt.symbol_select(sym, True)
            except Exception:
                pass
            from_pos = getattr(self._mt, "copy_rates_from_pos", None)
            if from_pos is None:
                return False  # backend lacks it — degrade to passive retry
            primed = from_pos(sym, mt5_timeframe, 0, 256)
            return primed is not None and (not hasattr(primed, "__len__") or len(primed) > 0)
        except Exception as exc:
            logger.debug("_prime_history(%s) failed: %s", sym, exc)
            return False

    def _resolve_symbol_tick(self, symbol: str):
        """Select symbol in Market Watch and return its tick, trying broker suffix variants."""
        def _alt(s: str) -> str | None:
            if s.upper().endswith("M"):
                return s[:-1]
            if s.upper() in {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}:
                return s + "m"
            return None

        tick = None
        resolved = symbol
        # After a terminal restart the broker data feed takes time to
        # re-establish.  Retry so we don't immediately reject orders with
        # "symbol tick unavailable" during the sync window.
        _TICK_RETRIES    = 3
        _TICK_RETRY_SLEEP = 3  # seconds
        for tick_attempt in range(_TICK_RETRIES):
            for sym in [symbol] + ([_alt(symbol)] if _alt(symbol) else []):
                try:
                    self._mt.symbol_select(sym, True)
                except Exception:
                    pass
                tick = self._mt.symbol_info_tick(sym)
                if tick is not None:
                    resolved = sym
                    break
            if tick is not None:
                break
            if tick_attempt < _TICK_RETRIES - 1:
                logger.warning(
                    "symbol_info_tick returned None for %s (attempt %d/%d) "
                    "— retrying in %ds (terminal may still be syncing after restart)",
                    symbol, tick_attempt + 1, _TICK_RETRIES, _TICK_RETRY_SLEEP,
                )
                time.sleep(_TICK_RETRY_SLEEP)
        return tick, resolved

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Place a market order (BUY or SELL)."""
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

        tick, resolved_symbol = self._resolve_symbol_tick(symbol)
        if tick is None:
            raise RuntimeError(
                f"symbol tick unavailable for {symbol} (also tried variant). "
                f"Ensure the symbol is enabled in Market Watch."
            )

        order_type = self._mt.ORDER_TYPE_BUY if side == "BUY" else self._mt.ORDER_TYPE_SELL
        price = tick.ask if side == "BUY" else tick.bid
        request = {
            "action": self._mt.TRADE_ACTION_DEAL,
            "symbol": resolved_symbol,
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

        result = self._order_send_with_ratelimit(request)
        return {
            "ticket": result.order,
            "symbol": resolved_symbol,
            "type": side,
            "volume": payload["volume"],
            "openPrice": price,
            "sl": payload["stopLoss"],
            "tp": payload["takeProfit"],
        }

    def place_limit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Place a pending limit/stop order.

        Supported types: BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP.
        The order sits in the terminal until price reaches `price`, then
        opens a position automatically.
        """
        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: {self.last_error or 'connection unavailable'}"
            )

        _TYPE_MAP = {
            "BUY_LIMIT":  "ORDER_TYPE_BUY_LIMIT",
            "SELL_LIMIT": "ORDER_TYPE_SELL_LIMIT",
            "BUY_STOP":   "ORDER_TYPE_BUY_STOP",
            "SELL_STOP":  "ORDER_TYPE_SELL_STOP",
        }
        order_type_str = payload["type"].upper()
        if order_type_str not in _TYPE_MAP:
            raise ValueError(f"type must be one of {list(_TYPE_MAP)}")

        mt5_order_type = getattr(self._mt, _TYPE_MAP[order_type_str], None)
        if mt5_order_type is None:
            raise RuntimeError(f"MT5 adapter has no attribute {_TYPE_MAP[order_type_str]}")

        symbol = payload["symbol"]
        # Ensure symbol is in Market Watch
        _, resolved_symbol = self._resolve_symbol_tick(symbol)

        request: dict[str, Any] = {
            "action":      self._mt.TRADE_ACTION_PENDING,
            "symbol":      resolved_symbol,
            "volume":      payload["volume"],
            "type":        mt5_order_type,
            "price":       payload["price"],
            "sl":          payload.get("stopLoss", 0.0),
            "tp":          payload.get("takeProfit", 0.0),
            "deviation":   20,
            "magic":       26042026,
            "comment":     payload.get("comment", "adaptive-bot"),
            "type_time":   self._mt.ORDER_TIME_GTC,
            "type_filling": self._mt.ORDER_FILLING_IOC,
        }

        # Optional expiration
        expiration_str = payload.get("expiration")
        if expiration_str:
            from datetime import datetime, timezone
            try:
                exp_dt = datetime.fromisoformat(expiration_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                request["type_time"] = self._mt.ORDER_TIME_SPECIFIED
                request["expiration"] = int(exp_dt.timestamp())
            except Exception as exc:
                raise ValueError(f"Invalid expiration datetime: {expiration_str!r} — {exc}") from exc

        result = self._order_send_with_ratelimit(request)
        return {
            "ticket":     result.order,
            "symbol":     resolved_symbol,
            "type":       order_type_str,
            "volume":     payload["volume"],
            "price":      payload["price"],
            "sl":         payload.get("stopLoss", 0.0),
            "tp":         payload.get("takeProfit", 0.0),
            "expiration": payload.get("expiration"),
        }

    def close_position(self, ticket: int, volume: float | None) -> dict[str, Any]:
        """Close a position fully or partially (if volume < position volume)."""
        self.ensure_connection()
        if self._mt is None or not self.connected:
            return {"closed": True, "ticket": ticket, "warning": self.last_error}

        positions = self._mt.positions_get(ticket=ticket)
        if not positions:
            return {"closed": False, "ticket": ticket, "error": "position not found"}
        pos = positions[0]
        symbol = pos.symbol

        close_volume = volume if volume is not None else pos.volume
        if close_volume > pos.volume:
            raise ValueError(
                f"Requested close volume {close_volume} exceeds position volume {pos.volume}"
            )
        partial = close_volume < pos.volume

        side_close = self._mt.ORDER_TYPE_SELL if pos.type == self._mt.ORDER_TYPE_BUY else self._mt.ORDER_TYPE_BUY

        try:
            self._mt.symbol_select(symbol, True)
        except Exception:
            pass
        tick = self._mt.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol tick unavailable for {symbol}")
        price = tick.bid if side_close == self._mt.ORDER_TYPE_SELL else tick.ask

        req = {
            "action":       self._mt.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       close_volume,
            "type":         side_close,
            "position":     ticket,
            "price":        price,
            "deviation":    20,
            "magic":        26042026,
            "comment":      "adaptive-close",
            "type_time":    self._mt.ORDER_TIME_GTC,
            "type_filling": self._mt.ORDER_FILLING_IOC,
        }

        result = self._order_send_with_ratelimit(req)
        if result is None:
            raise RuntimeError(f"close order_send returned None: {self._last_error_repr()}")
        ok = result.retcode == self._mt.TRADE_RETCODE_DONE
        return {
            "closed":        ok,
            "ticket":        ticket,
            "retcode":       result.retcode,
            "closedVolume":  close_volume,
            "partial":       partial,
            "remainVolume":  round(pos.volume - close_volume, 8) if ok and partial else 0.0,
        }

    def modify_position(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        """
        Modify the stop loss and/or take profit of an open position.

        Pass None for sl or tp to leave that value unchanged.
        Pass 0.0 to remove an existing sl/tp.
        """
        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: {self.last_error or 'connection unavailable'}"
            )

        positions = self._mt.positions_get(ticket=ticket)
        if not positions:
            raise ValueError(f"Position {ticket} not found")
        pos = positions[0]

        new_sl = sl if sl is not None else pos.sl
        new_tp = tp if tp is not None else pos.tp

        request = {
            "action":   self._mt.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol":   pos.symbol,
            "sl":       new_sl,
            "tp":       new_tp,
        }

        # Route through the rate-limit lock so SL/TP modifications cannot
        # race with in-flight place_order calls and compound 10027 pressure.
        result = self._order_send_with_ratelimit(request)
        ok = result.retcode == self._mt.TRADE_RETCODE_DONE
        if not ok:
            raise RuntimeError(
                f"modify SL/TP failed retcode={result.retcode} "
                f"(see https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes)"
            )
        return {
            "modified": True,
            "ticket":   ticket,
            "symbol":   pos.symbol,
            "sl":       new_sl,
            "tp":       new_tp,
            "retcode":  result.retcode,
        }

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    _TIMEFRAME_MAP: dict[str, str] = {
        "1m":  "TIMEFRAME_M1",
        "5m":  "TIMEFRAME_M5",
        "15m": "TIMEFRAME_M15",
        "30m": "TIMEFRAME_M30",
        "1h":  "TIMEFRAME_H1",
        "4h":  "TIMEFRAME_H4",
        "1d":  "TIMEFRAME_D1",
        "1w":  "TIMEFRAME_W1",
    }

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        from datetime import datetime, timezone

        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: "
                f"{self.last_error or 'connection unavailable'}"
            )

        tf_attr = self._TIMEFRAME_MAP.get(timeframe.lower())
        if not tf_attr:
            raise ValueError(f"Unsupported timeframe: {timeframe}. Use one of: {list(self._TIMEFRAME_MAP.keys())}")
        mt5_timeframe = getattr(self._mt, tf_attr, None)
        if mt5_timeframe is None:
            raise ValueError(f"MT5 adapter has no attribute {tf_attr}")

        dt_from = datetime.strptime(from_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt_to = datetime.strptime(to_date[:10], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )

        def _alt_symbol(s: str) -> str | None:
            if s.upper().endswith("M"):
                return s[:-1]
            if s.upper() in {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}:
                return s + "m"
            return None

        symbols_to_try = [symbol]
        alt = _alt_symbol(symbol)
        if alt:
            symbols_to_try.append(alt)

        rates = None
        used_symbol = symbol
        last_error: str | None = None

        # After a terminal self-restart (e.g. LiveUpdate) the broker data
        # feed takes time to reconnect.  mt5.initialize() passes the IPC
        # probe before price/history data is available, so copy_rates_range
        # returns None for a window that, after a *binary* update, can exceed a
        # minute.  Retry to let the terminal sync rather than immediately
        # returning 502 to every caller.  Crucially, a range query alone does
        # not trigger the history download — on an empty result we actively
        # prime via copy_rates_from_pos so the next attempt has bars to read.
        # Total worst-case in-call time (~6 attempts, capped backoff) stays
        # inside the backend candle client's 120s read timeout.
        _DATA_RETRIES    = 6
        _DATA_RETRY_SLEEP = 5  # base seconds between retries (capped backoff below)

        for data_attempt in range(_DATA_RETRIES):
            for sym in symbols_to_try:
                try:
                    try:
                        self._mt.symbol_select(sym, True)
                    except Exception:
                        pass
                    rates = self._mt.copy_rates_range(sym, mt5_timeframe, dt_from, dt_to)
                    if rates is not None and (not hasattr(rates, '__len__') or len(rates) > 0):
                        used_symbol = sym
                        break
                    last_error = f"copy_rates_range returned no data for {sym} {timeframe}"
                    rates = None
                except Exception as exc:
                    logger.error(
                        "copy_rates_range exception (attempt %d/%d) for %s %s: %s",
                        data_attempt + 1, _DATA_RETRIES, sym, timeframe, exc,
                    )
                    self.connected = False
                    self._mt = None
                    raise RuntimeError(f"copy_rates_range exception: {exc}") from exc

            if rates is not None and (not hasattr(rates, '__len__') or len(rates) > 0):
                break  # got data

            if data_attempt < _DATA_RETRIES - 1:
                # Actively wake the broker-side history pull for each symbol
                # variant before sleeping, so the next attempt's range query
                # has cached bars to return.
                for sym in symbols_to_try:
                    if self._prime_history(sym, mt5_timeframe):
                        logger.info(
                            "priming history for %s %s via copy_rates_from_pos "
                            "(range query empty, terminal cache cold)",
                            sym, timeframe,
                        )
                sleep_s = min(_DATA_RETRY_SLEEP * (1 + data_attempt), 15)
                logger.warning(
                    "copy_rates_range returned no data for %s %s "
                    "(attempt %d/%d) — terminal may still be syncing after restart; "
                    "retrying in %ds",
                    symbol, timeframe, data_attempt + 1, _DATA_RETRIES, sleep_s,
                )
                time.sleep(sleep_s)

        if rates is None or (hasattr(rates, '__len__') and len(rates) == 0):
            tried = " / ".join(symbols_to_try)
            raise RuntimeError(last_error or f"copy_rates_range returned no data for {tried} {timeframe}")

        logger.debug("copy_rates_range: using symbol %r (%d bars)", used_symbol, len(rates))

        candles: list[dict[str, Any]] = []
        for r in rates:
            try:
                ts = int(r[0]) if not hasattr(r, 'time') else int(r.time)
                candles.append({
                    "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "open":     float(r[1]) if not hasattr(r, 'open')       else float(r.open),
                    "high":     float(r[2]) if not hasattr(r, 'high')       else float(r.high),
                    "low":      float(r[3]) if not hasattr(r, 'low')        else float(r.low),
                    "close":    float(r[4]) if not hasattr(r, 'close')      else float(r.close),
                    "volume":   float(r[5]) if not hasattr(r, 'tick_volume') else float(r.tick_volume),
                })
            except (IndexError, ValueError, TypeError):
                continue
        return candles

    def history_deals_get(self, ticket: int, lookback_days: int = 7) -> list[dict]:
        from datetime import datetime, timedelta, timezone

        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: "
                f"{self.last_error or 'connection unavailable'}"
            )

        try:
            deals = self._mt.history_deals_get(position=ticket)
        except Exception as exc:
            raise RuntimeError(f"history_deals_get(position={ticket}) failed: {exc}") from exc

        if deals is None or (hasattr(deals, '__len__') and len(deals) == 0):
            dt_from = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            dt_to = datetime.now(timezone.utc)
            try:
                deals = self._mt.history_deals_get(dt_from, dt_to)
            except Exception as exc:
                raise RuntimeError(f"history_deals_get(date_range) failed: {exc}") from exc
            if deals is not None and hasattr(deals, '__len__'):
                deals = [d for d in deals if getattr(d, 'position_id', None) == ticket]
            else:
                deals = []

        if deals is None:
            return []

        result: list[dict] = []
        for d in deals:
            try:
                result.append({
                    "ticket":      int(getattr(d, 'ticket',      0)),
                    "order":       int(getattr(d, 'order',       0)),
                    "position_id": int(getattr(d, 'position_id', ticket)),
                    "time":        int(getattr(d, 'time',        0)),
                    "type":        int(getattr(d, 'type',        0)),
                    "entry":       int(getattr(d, 'entry',       0)),
                    "symbol":      str(getattr(d, 'symbol',      '')),
                    "volume":      float(getattr(d, 'volume',    0.0)),
                    "price":       float(getattr(d, 'price',     0.0)),
                    "profit":      float(getattr(d, 'profit',    0.0)),
                    "swap":        float(getattr(d, 'swap',      0.0)),
                    "commission":  float(getattr(d, 'commission', 0.0)),
                    "comment":     str(getattr(d, 'comment',    '')),
                })
            except Exception:
                continue
        return result

    # ------------------------------------------------------------------
    # Item 16 — cancel pending order + deal-range history
    # ------------------------------------------------------------------

    def cancel_order(self, ticket: int) -> dict[str, Any]:
        """Cancel a pending order (BUY_LIMIT / SELL_LIMIT / BUY_STOP / SELL_STOP)."""
        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: "
                f"{self.last_error or 'connection unavailable'}"
            )
        request = {
            "action": self._mt.TRADE_ACTION_REMOVE,
            "order": ticket,
            "comment": "adaptive-cancel",
        }
        result = self._order_send_with_ratelimit(request)
        if result is None:
            raise RuntimeError(f"cancel order_send returned None: {self._last_error_repr()}")
        ok = result.retcode == self._mt.TRADE_RETCODE_DONE
        if not ok:
            raise RuntimeError(
                f"cancel order failed retcode={result.retcode} "
                f"(see MT5 enum_trade_return_codes)"
            )
        return {"cancelled": True, "ticket": ticket, "retcode": result.retcode}

    def history_deals_range(
        self, from_date: str, to_date: str, symbol: str | None = None
    ) -> list[dict]:
        """All closed deals within a date range, optionally filtered by symbol."""
        from datetime import datetime, timezone

        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: "
                f"{self.last_error or 'connection unavailable'}"
            )
        dt_from = datetime.strptime(from_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt_to = datetime.strptime(to_date[:10], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        try:
            deals = self._mt.history_deals_get(dt_from, dt_to)
        except Exception as exc:
            raise RuntimeError(f"history_deals_get(date_range) failed: {exc}") from exc
        if deals is None:
            return []

        result: list[dict] = []
        for d in deals:
            try:
                sym = str(getattr(d, "symbol", ""))
                if symbol and sym.upper() != symbol.upper():
                    continue
                result.append({
                    "ticket":      int(getattr(d, "ticket",      0)),
                    "order":       int(getattr(d, "order",       0)),
                    "position_id": int(getattr(d, "position_id", 0)),
                    "time":        int(getattr(d, "time",        0)),
                    "type":        int(getattr(d, "type",        0)),
                    "entry":       int(getattr(d, "entry",       0)),
                    "symbol":      sym,
                    "volume":      float(getattr(d, "volume",    0.0)),
                    "price":       float(getattr(d, "price",     0.0)),
                    "profit":      float(getattr(d, "profit",    0.0)),
                    "swap":        float(getattr(d, "swap",      0.0)),
                    "commission":  float(getattr(d, "commission", 0.0)),
                    "comment":     str(getattr(d, "comment",    "")),
                })
            except Exception:
                continue
        return result

    # ------------------------------------------------------------------
    # Item 11 — current bid/ask/spread
    # ------------------------------------------------------------------

    def get_tick(self, symbol: str) -> dict[str, Any]:
        """Return current bid/ask and spread for a symbol."""
        self.ensure_connection()
        if self._mt is None or not self.connected:
            raise RuntimeError(
                f"mt5 not connected [{self.last_error_class or 'unknown'}]: "
                f"{self.last_error or 'connection unavailable'}"
            )
        tick, resolved = self._resolve_symbol_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol tick unavailable for {symbol}")
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        return {
            "symbol": resolved,
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, 8),
            "time": int(getattr(tick, "time", 0) or 0),
        }


# ---------------------------------------------------------------------------
# Custom exception so the bridge endpoint can return HTTP 429
# ---------------------------------------------------------------------------

class TooManyRequestsError(RuntimeError):
    """Raised when MT5 retcode 10024 (too frequent requests) exhausts all retries."""


class AutoTradingDisabledError(RuntimeError):
    """
    Raised when MT5 returns retcode 10027 (CLIENT_DISABLES_AT) or 10026
    (SERVER_DISABLES_AT) — AutoTrading is disabled, so no order can be placed
    until the 'Algo Trading' toggle is turned back on. This is a persistent
    configuration state, not a transient rate-limit.
    """


adapter = MT5Adapter()