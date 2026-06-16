"""
Alerting / structured-event dispatch (items 6 + 17).

Provides a single ``dispatch_alert()`` entry point that:
  - ALWAYS emits a structured log line (so events are greppable in service logs);
  - optionally forwards to Telegram and/or a generic webhook when those channels
    are configured via AppSettings.

Everything is OPT-IN and OFF by default — with no settings configured this only
logs, so it cannot affect a running system. Delivery is best-effort and never
raises into the caller. An in-memory throttle prevents alert storms (the same
event key is forwarded at most once per ``alerts_throttle_seconds``).

Settings (all optional, AppSettings keys):
  alerts_min_level         info | warning | critical   (default "warning")
  alerts_throttle_seconds  per-event forward cooldown   (default 300)
  alerts_telegram_bot_token / alerts_telegram_chat_id   (Telegram channel)
  alerts_webhook_url        generic JSON POST target
  alerts_enabled_events     comma-separated allow-list of event names to forward;
                            empty/"all" = forward everything that passes min_level.
                            e.g. "service_started,bridge_outage,daily_loss_limit"
  alerts_proxy_url          optional HTTP CONNECT proxy for outbound delivery,
                            e.g. "http://user:pass@host:port". Needed on hosts
                            (Hugging Face Spaces, some corporate nets) that
                            black-hole direct TLS to api.telegram.org. Falls back
                            to the HTTPS_PROXY / ALL_PROXY env vars if unset.

Delivery is non-blocking: dispatch_alert() and send_async() enqueue to an
in-process worker thread that retries transient failures with capped
exponential backoff. send_direct() stays synchronous so the 'send test message'
button can report a real, immediate result.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import random
import socket
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from pymongo.database import Database

from .. import crud

logger = logging.getLogger("alerts")

_LEVELS = {"info": 10, "warning": 20, "critical": 30}

# In-memory per-event throttle (monotonic clock; resets on process restart).
# Keys are usually event names, but high-cardinality events (e.g. one per trade
# ticket) pass a distinct throttle_key, so the dict is pruned to stay bounded.
_last_forwarded: dict[str, float] = {}
_THROTTLE_MAX_KEYS = 2000


def _prune_throttle(now: float, throttle: float) -> None:
    """Drop throttle keys older than the cooldown window once the dict grows
    large. Such keys can no longer suppress anything, so removing them is safe
    and keeps memory bounded under high-cardinality throttle keys."""
    if len(_last_forwarded) <= _THROTTLE_MAX_KEYS:
        return
    cutoff = now - throttle
    for k in [k for k, ts in _last_forwarded.items() if ts < cutoff]:
        _last_forwarded.pop(k, None)

# Per-host cache of the last IPv4 edge address that completed a TLS handshake.
# api.telegram.org is served by a rotating pool of edge IPs and on container
# hosts (Render, etc.) some of those IPs black-hole the TLS ClientHello, which
# surfaces as "_ssl.c:999: handshake operation timed out". Remembering a known
# good IP makes every send after the first one fast and reliable instead of
# gambling on DNS round-robin each time.
_last_good_ip: dict[str, str] = {}

# Shared TLS context (created once; thread-safe for concurrent use).
_TLS_CTX = ssl.create_default_context()


# Canonical AppSettings keys (written by the Settings UI), plus a few common
# aliases so a token/chat-id saved manually under a slightly different key name
# is still picked up. Everything is read from the app_settings collection — no
# env vars are ever consulted for Telegram.
_TG_TOKEN_KEYS = (
    "alerts_telegram_bot_token",
    "telegram_bot_token",
    "telegram_token",
)
_TG_CHAT_KEYS = (
    "alerts_telegram_chat_id",
    "telegram_chat_id",
    "telegram_user_id",
    "alerts_telegram_user_id",
)
# Telegram API base URL. Override to point at a relay (e.g. a Cloudflare Worker
# that reverse-proxies api.telegram.org) on hosts whose egress to Telegram's IP
# range is blocked. The relay should sit behind a secret path so it is not an
# open proxy, e.g. "https://my-relay.workers.dev/<secret>".
_TG_API_BASE_KEYS = (
    "alerts_telegram_api_base",
    "telegram_api_base",
)
_TG_API_DEFAULT = "https://api.telegram.org"


def _telegram_api_base(db: Database) -> str:
    base = _first_setting(db, _TG_API_BASE_KEYS) or _TG_API_DEFAULT
    return base.rstrip("/")


def _first_setting(db: Database, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty AppSetting among ``keys`` (DB-only lookup).

    Values are stripped of surrounding whitespace/newlines — a trailing space or
    newline pasted into the token/chat-id field would otherwise corrupt the
    Telegram URL/payload and make the API silently reject the message even though
    the same value works from a clean curl.
    """
    for k in keys:
        val = crud.get_setting(db, k)
        if val and val.strip():
            return val.strip()
    return None


# Optional outbound proxy. Some hosts (Hugging Face Spaces, locked-down
# corporate networks) black-hole direct TLS to api.telegram.org — TCP connects
# but the handshake is dropped (``_ssl.c:999: handshake operation timed out``).
# Routing through an HTTPS/CONNECT proxy egresses from a different path and
# restores delivery. Configured via an AppSetting or a standard proxy env var.
_PROXY_KEYS = (
    "alerts_proxy_url",
    "telegram_proxy_url",
    "alerts_https_proxy",
)
_PROXY_ENV = ("ALERTS_PROXY_URL", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


def _proxy_url(db: Database) -> str | None:
    """Resolve an outbound proxy URL from settings first, then env vars."""
    val = _first_setting(db, _PROXY_KEYS)
    if val:
        return val
    for env in _PROXY_ENV:
        v = os.environ.get(env)
        if v and v.strip():
            return v.strip()
    return None


def _proxy_source(db: Database) -> str | None:
    """Where the active proxy URL came from (for diagnostics)."""
    if _first_setting(db, _PROXY_KEYS):
        return "setting:alerts_proxy_url"
    for env in _PROXY_ENV:
        if (os.environ.get(env) or "").strip():
            return f"env:{env}"
    return None


def _mask_proxy(url: str) -> str:
    """Render a proxy URL with credentials masked, safe to return to the UI."""
    try:
        p = urlsplit(url if "://" in url else f"http://{url}")
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        if p.username:
            um = (p.username[:3] + "***") if len(p.username) > 3 else "***"
            return f"{p.scheme}://{um}@{host}{port}"
        return f"{p.scheme}://{host}{port}"
    except Exception:
        return "(set)"


# Marker so the diagnostics endpoint can confirm the proxy-capable build is the
# one actually running. Bump when the delivery path changes materially.
ALERTS_BUILD = "relay-support-v3"


def diagnose(db: Database, timeout: float = 8.0) -> dict:
    """Actively probe every delivery route from THIS process's network and
    report what works. Used by GET /system/alerts/diagnostics so a blocked host
    can tell us, from its own egress, whether the proxy is configured/reachable
    and whether the direct path is really dead — instead of guessing from logs.

    Makes a real (harmless) GET to api.telegram.org over each route. No alert is
    sent. Credentials are never returned.
    """
    proxy = _proxy_url(db)
    tg_token = _first_setting(db, _TG_TOKEN_KEYS)
    tg_chat = _first_setting(db, _TG_CHAT_KEYS)

    # Probe the exact base the sender uses — the relay/Worker if configured, so
    # diagnostics test the real delivery path rather than always hitting
    # api.telegram.org direct.
    base = _telegram_api_base(db)
    bsplit = urlsplit(base)
    host = bsplit.hostname or "api.telegram.org"
    base_path = bsplit.path or ""

    token = (tg_token or "").removeprefix("bot").strip()
    path = f"{base_path}/bot{token}/getMe" if token else (base_path or "/")
    request = (
        f"GET {path} HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        f"Connection: close\r\n"
        f"User-Agent: adaptive-trading-alerts/1\r\n"
        f"\r\n"
    ).encode("utf-8")

    routes: list[tuple[str, str]] = []
    if proxy:
        routes.append(("proxy", f"proxy:{proxy}"))
    routes.extend((ip, ip) for ip in _ipv4_addresses(host))

    probes: list[dict] = []
    for name, route in routes:
        t0 = time.monotonic()
        try:
            resp = _send_once(host, route, request, timeout)
            probes.append({
                "route": name, "ok": True, "status": resp.status_code,
                "ms": int((time.monotonic() - t0) * 1000),
            })
        except Exception as exc:
            probes.append({
                "route": name, "ok": False, "error": str(exc),
                "ms": int((time.monotonic() - t0) * 1000),
            })

    return {
        "build": ALERTS_BUILD,
        "telegram_configured": bool(tg_token and tg_chat),
        "telegram_api_base": base,
        "probe_host": host,
        "proxy_configured": bool(proxy),
        "proxy_source": _proxy_source(db),
        "proxy": _mask_proxy(proxy) if proxy else None,
        "routes_tried": [p["route"] for p in probes],
        "any_route_ok": any(p["ok"] for p in probes),
        "probes": probes,
        "queue_depth": _alert_q.qsize(),
    }


def _enabled_channels(db: Database) -> list[tuple[str, dict]]:
    channels: list[tuple[str, dict]] = []
    proxy = _proxy_url(db)
    tg_token = _first_setting(db, _TG_TOKEN_KEYS)
    tg_chat = _first_setting(db, _TG_CHAT_KEYS)
    if tg_token and tg_chat:
        # Tolerate a token saved with a leading "bot" prefix or full API path.
        tg_token = tg_token.removeprefix("bot").strip()
        channels.append(("telegram", {
            "token": tg_token, "chat_id": tg_chat, "proxy": proxy,
            "api_base": _telegram_api_base(db),
        }))
    hook = crud.get_setting(db, "alerts_webhook_url")
    if hook and hook.strip():
        channels.append(("webhook", {"url": hook.strip(), "proxy": proxy}))
    return channels


class _Resp:
    """Minimal response shim exposing the ``.status_code`` / ``.json()`` surface
    that ``_forward`` relies on, so the sender can be swapped without touching
    the callers."""

    __slots__ = ("status_code", "_body")

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return json.loads(self._body.decode("utf-8", "replace"))


def _ipv4_addresses(host: str) -> list[str]:
    """Resolve ``host`` to its IPv4 (A-record) addresses only.

    IPv6 is deliberately excluded: container hosts often advertise AAAA routes
    that black-hole outbound traffic, so letting the socket pick an IPv6 edge is
    the classic cause of a hung TLS handshake even when IPv4 works fine.
    """
    try:
        infos = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    seen: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    return seen


def _read_http_head(sock) -> bytes:
    """Read bytes from ``sock`` until the end of the HTTP header block."""
    data = b""
    while b"\r\n\r\n" not in data:
        buf = sock.recv(65536)
        if not buf:
            break
        data += buf
    return data


def _open_via_proxy(host: str, proxy: str, timeout: float):
    """Open a raw TCP tunnel to ``host``:443 through an HTTP CONNECT proxy.

    Returns the connected (still-plaintext) socket positioned to start a TLS
    handshake with the origin. Raises on any non-2xx CONNECT response.
    """
    p = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    pport = p.port or (443 if p.scheme == "https" else 8080)
    raw = socket.create_connection((p.hostname, pport), timeout=timeout)
    raw.settimeout(timeout)
    try:
        lines = [f"CONNECT {host}:443 HTTP/1.1", f"Host: {host}:443"]
        if p.username:
            import base64
            cred = f"{p.username}:{p.password or ''}".encode()
            lines.append("Proxy-Authorization: Basic " + base64.b64encode(cred).decode())
        lines.append("Proxy-Connection: keep-alive")
        raw.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = _read_http_head(raw)
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        code = status_line.split(" ", 2)[1] if len(status_line.split(" ", 2)) >= 2 else ""
        if not code.startswith("2"):
            raise OSError(f"proxy CONNECT failed: {status_line.strip()}")
        return raw
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        raise


def _send_once(host: str, route: str, request: bytes, timeout: float) -> _Resp:
    """Open one TLS connection to ``host`` over ``route`` (an IPv4 edge IP, or
    ``"proxy:<url>"``), send a pre-built HTTP/1.0 request, and parse the reply.

    HTTP/1.0 with ``Connection: close`` keeps parsing trivial (read to EOF) and
    sidesteps keep-alive/chunked edge cases — alerts are tiny one-shot POSTs.
    SNI and certificate validation always use the real ``host``, even when the
    TCP connection targets a bare IP or a proxy.
    """
    if route.startswith("proxy:"):
        raw = _open_via_proxy(host, route[len("proxy:"):], timeout)
    else:
        raw = socket.create_connection((route, 443), timeout=timeout)
    try:
        raw.settimeout(timeout)
        with _TLS_CTX.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(request)
            chunks: list[bytes] = []
            while True:
                buf = tls.recv(65536)
                if not buf:
                    break
                chunks.append(buf)
    finally:
        try:
            raw.close()
        except Exception:
            pass

    data = b"".join(chunks)
    head, _, body = data.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = status_line.split(" ", 2)
    status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    return _Resp(status_code, body)


def _http_post(url: str, json_body: dict, timeout: float = 10.0, proxy: str | None = None):
    """Robust JSON POST for tiny alert payloads, resilient to host-level egress
    blocks.

    Builds an ordered list of routes and tries each (last-known-good first) with
    a short per-attempt timeout:
      - a configured proxy (tried first when present, since hosts that block
        Telegram block it consistently — no point burning a 10s timeout on the
        direct path every send);
      - then each IPv4 edge IP of the host (IPv6 excluded to dodge AAAA
        black-holes), so a single dead Telegram edge can't fail the send.
    The first route to complete a TLS handshake is cached for subsequent calls.
    Stdlib-only (socket + ssl); no dependency on httpx connection behaviour.
    """
    split = urlsplit(url)
    host = split.hostname or ""
    path = split.path or "/"
    if split.query:
        path = f"{path}?{split.query}"

    payload = json.dumps(json_body).encode("utf-8")
    request = (
        f"POST {path} HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n"
        f"User-Agent: adaptive-trading-alerts/1\r\n"
        f"\r\n"
    ).encode("utf-8") + payload

    routes: list[str] = []
    if proxy:
        routes.append(f"proxy:{proxy}")
    routes.extend(_ipv4_addresses(host))
    if not routes:
        raise OSError(f"could not resolve any IPv4 address for {host}")

    # Try the last known-good route first, then the rest, without duplicates.
    cached = _last_good_ip.get(host)
    ordered = ([cached] if cached in routes else []) + [r for r in routes if r != cached]

    last_exc: Exception | None = None
    for attempt, route in enumerate(ordered):
        try:
            resp = _send_once(host, route, request, timeout)
            _last_good_ip[host] = route
            return resp
        except Exception as exc:
            last_exc = exc
            _last_good_ip.pop(host, None)
            logger.debug("alert POST to %s via %s failed: %s", host, route, exc)
            # brief backoff between routes; keeps total time bounded
            if attempt + 1 < len(ordered):
                time.sleep(0.25)

    raise last_exc or OSError(f"all {len(ordered)} routes to {host} failed")


def _forward(channel: str, cfg: dict, level: str, event: str, message: str, context: dict | None) -> dict:
    """Best-effort forward to one channel. Returns
    ``{"ok": bool, "detail": str, "retryable": bool}`` describing the actual
    delivery result (e.g. Telegram's API response) so the 'send test message'
    button can report real success/failure and the background worker knows
    whether re-attempting is worthwhile. Never raises.

    A 4xx (bad token/chat-id, malformed request) is permanent — retrying is
    pointless and would spam. A transport error (TLS timeout, connection
    refused) or a 429/5xx is transient — worth retrying with backoff.
    """
    text = f"[{level.upper()}] {event}: {message}"
    proxy = cfg.get("proxy")
    try:
        if channel == "telegram":
            api_base = cfg.get("api_base") or _TG_API_DEFAULT
            resp = _http_post(
                f"{api_base}/bot{cfg['token']}/sendMessage",
                {"chat_id": cfg["chat_id"], "text": text},
                proxy=proxy,
            )
            # Telegram replies 200 with {"ok": true, ...} on success, or a 4xx
            # with {"ok": false, "description": "..."} on bad token/chat_id.
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            if resp.status_code == 200 and body.get("ok"):
                return {"ok": True, "detail": "delivered", "retryable": False}
            detail = body.get("description") or f"HTTP {resp.status_code}"
            retryable = resp.status_code == 429 or resp.status_code >= 500 or resp.status_code == 0
            logger.warning("Telegram send failed: %s", detail)
            return {"ok": False, "detail": detail, "retryable": retryable}
        elif channel == "webhook":
            resp = _http_post(
                cfg["url"],
                {
                    "level": level,
                    "event": event,
                    "message": message,
                    "context": context or {},
                },
                proxy=proxy,
            )
            ok = resp.status_code < 400
            retryable = resp.status_code == 429 or resp.status_code >= 500 or resp.status_code == 0
            return {
                "ok": ok,
                "detail": "delivered" if ok else f"HTTP {resp.status_code}",
                "retryable": retryable,
            }
    except Exception as exc:  # transport-level failure — never break the caller
        logger.warning("Alert forward via %s failed: %s", channel, exc)
        return {"ok": False, "detail": str(exc), "retryable": True}
    return {"ok": False, "detail": "unknown channel", "retryable": False}


# ---------------------------------------------------------------------------
# Background delivery — non-blocking, self-healing retries.
#
# Direct delivery (send_direct) blocks the caller and gives one shot. On hosts
# whose egress to Telegram is flaky or temporarily blocked, that means lost
# alerts and a stalled startup/request. The worker decouples generation from
# delivery: callers enqueue instantly, a daemon thread delivers with capped
# exponential backoff + jitter, and only transient failures are retried (a bad
# token is dropped immediately instead of spamming). The channel config is
# snapshotted at enqueue time, so the worker needs no DB handle.
# ---------------------------------------------------------------------------
_MAX_ATTEMPTS = 8           # ~ up to a few minutes of retries per alert
_MAX_AGE = 1800.0           # stop retrying an alert older than 30 min
_QUEUE_MAX = 1000

_alert_q: "queue.PriorityQueue[tuple[float, int, dict]]" = queue.PriorityQueue(maxsize=_QUEUE_MAX)
_seq_counter = itertools.count()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _worker_loop() -> None:
    while True:
        due, _, job = _alert_q.get()
        try:
            wait = due - time.monotonic()
            if wait > 0:
                # Not due yet — nap (bounded) and requeue so earlier jobs win.
                time.sleep(min(wait, 5.0))
                if time.monotonic() < due:
                    _alert_q.put((due, next(_seq_counter), job))
                    continue

            res = _forward(
                job["channel"], job["cfg"], job["level"],
                job["event"], job["message"], job.get("context"),
            )
            if res.get("ok"):
                continue

            job["attempts"] += 1
            age = time.monotonic() - job["created"]
            if res.get("retryable") and job["attempts"] < _MAX_ATTEMPTS and age < _MAX_AGE:
                delay = min(300.0, 2.0 ** job["attempts"]) + random.uniform(0.0, 1.0)
                logger.info(
                    "Alert %s/%s retry %d/%d in %.0fs (%s)",
                    job["channel"], job["event"], job["attempts"], _MAX_ATTEMPTS,
                    delay, res.get("detail"),
                )
                _alert_q.put((time.monotonic() + delay, next(_seq_counter), job))
            else:
                logger.warning(
                    "Alert %s/%s permanently undelivered after %d attempt(s): %s",
                    job["channel"], job["event"], job["attempts"], res.get("detail"),
                )
        except Exception as exc:
            logger.debug("alert worker iteration failed: %s", exc)
        finally:
            _alert_q.task_done()


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="alerts-delivery", daemon=True)
        _worker.start()


def _enqueue_delivery(channel: str, cfg: dict, level: str, event: str,
                      message: str, context: dict | None) -> bool:
    _ensure_worker()
    job = {
        "channel": channel, "cfg": cfg, "level": level, "event": event,
        "message": message, "context": context,
        "attempts": 0, "created": time.monotonic(),
    }
    try:
        _alert_q.put_nowait((time.monotonic(), next(_seq_counter), job))
        return True
    except queue.Full:
        logger.warning("alert queue full; dropping %s/%s", channel, event)
        return False


def send_async(
    db: Database,
    event: str,
    message: str,
    level: str = "info",
    context: dict[str, Any] | None = None,
) -> dict:
    """Non-blocking force-send: snapshot configured channels now and deliver in
    the background with retries/backoff. Returns immediately — use for startup
    banners and any hot path that must not block on network I/O."""
    logger.info("EVENT(async) %s | %s | %s", event, message, level)
    channels = _enabled_channels(db)
    if not channels:
        return {"queued": False, "reason": "No channels configured.", "channels": []}
    queued = [c for c, cfg in channels if _enqueue_delivery(c, cfg, level, event, message, context)]
    return {"queued": bool(queued), "channels": queued}


def send_direct(
    db: Database,
    event: str,
    message: str,
    level: str = "info",
    context: dict[str, Any] | None = None,
) -> dict:
    """Force-send to all configured channels, bypassing min_level / allow-list /
    throttle. Used for startup banners and the explicit 'send test message'
    button. Returns a small status dict so the API can report what happened.
    """
    logger.info("EVENT(direct) %s | %s | %s", event, message, level)
    channels = _enabled_channels(db)
    if not channels:
        return {
            "sent": False,
            "reason": "No channels configured. Save a Telegram bot token + chat ID "
                      "(or a webhook URL) in Settings first.",
            "channels": [],
        }
    results: list[dict] = []
    delivered: list[str] = []
    for channel, cfg in channels:
        res = _forward(channel, cfg, level, event, message, context)
        results.append({"channel": channel, **res})
        if res.get("ok"):
            delivered.append(channel)
    any_ok = bool(delivered)
    return {
        "sent": any_ok,
        "channels": delivered,
        "results": results,
        # Surface the first failure reason so the UI can show why nothing arrived.
        "reason": None if any_ok else "; ".join(
            f"{r['channel']}: {r['detail']}" for r in results if not r.get("ok")
        ),
    }


def dispatch_alert(
    db: Database,
    level: str,
    event: str,
    message: str,
    context: dict[str, Any] | None = None,
    throttle_key: str | None = None,
) -> None:
    """Emit a structured event and best-effort forward it to configured channels.

    `level` is one of "info" | "warning" | "critical". Safe to call from any
    sync context; it never raises.

    `throttle_key` overrides the key used for the per-event forward cooldown
    (defaults to `event`). High-cardinality events (e.g. one per trade ticket)
    pass a distinct key so unrelated operations don't suppress each other, while
    repeats sharing a key (e.g. trailing-stop modifies on one ticket) stay
    throttled. The allow-list and structured log line always use `event`.
    """
    level = level if level in _LEVELS else "info"

    # Structured log line — always, regardless of channel configuration.
    try:
        logger.log(
            logging.WARNING if _LEVELS[level] >= 20 else logging.INFO,
            "EVENT %s | %s | %s | %s",
            level, event, message, json.dumps(context or {}, default=str),
        )
    except Exception:
        pass

    try:
        # Optional per-event allow-list. Empty or "all" → forward everything.
        # Evaluated before the min_level floor so that explicitly enabling an
        # event opts it in regardless of its level (e.g. info-level
        # trade_operations with the default min_level of "warning").
        allow_raw = str(crud.get_setting(db, "alerts_enabled_events") or "").strip().lower()
        explicitly_allowed = False
        if allow_raw and allow_raw != "all":
            allowed = {e.strip() for e in allow_raw.split(",") if e.strip()}
            if event.lower() not in allowed:
                return
            explicitly_allowed = True

        # min_level floor — bypassed for events the operator explicitly allow-listed.
        if not explicitly_allowed:
            min_level = str(crud.get_setting(db, "alerts_min_level") or "warning").lower()
            if _LEVELS[level] < _LEVELS.get(min_level, 20):
                return

        channels = _enabled_channels(db)
        if not channels:
            return

        throttle = float(crud.get_setting(db, "alerts_throttle_seconds") or 300)
        now = time.monotonic()
        tkey = throttle_key or event
        last = _last_forwarded.get(tkey, 0.0)
        if now - last < throttle:
            return
        _last_forwarded[tkey] = now
        _prune_throttle(now, throttle)

        # Hand off to the background worker so a slow/blocked egress never
        # stalls the caller and transient failures are retried automatically.
        for channel, cfg in channels:
            _enqueue_delivery(channel, cfg, level, event, message, context)
    except Exception as exc:
        logger.debug("dispatch_alert failed: %s", exc)


# ---------------------------------------------------------------------------
# Trade/order operation notifications.
#
# Fired from every trade/order operation site so an operator who enables the
# "trade_operations" event in alerts_enabled_events is notified of each open,
# close, modify and cancel. Dispatched at "info" with a per-operation throttle
# key so distinct operations never suppress one another, while repeats on one
# ticket (e.g. trailing-stop modifies) are coalesced by the normal cooldown.
# ---------------------------------------------------------------------------
TRADE_OPERATIONS_EVENT = "trade_operations"

_OP_EMOJI = {
    "open": "🟢",
    "close": "🔴",
    "close_partial": "🟠",
    "modify": "✏️",
    "cancel": "🚫",
}


def _fmt_num(value: Any) -> str | None:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return None


def notify_trade_operation(
    db: Database,
    operation: str,
    *,
    symbol: str | None = None,
    direction: str | None = None,
    lot_size: float | None = None,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    ticket: int | None = None,
    trade_id: int | None = None,
    pnl: float | None = None,
    result: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort 'trade_operations' alert for one trade/order operation.

    Never raises into trading/persistence logic. `operation` is one of
    open | close | close_partial | modify | cancel.
    """
    try:
        emoji = _OP_EMOJI.get(operation, "•")
        verb = {
            "open": "Opened",
            "close": "Closed",
            "close_partial": "Partially closed",
            "modify": "Modified",
            "cancel": "Cancelled",
        }.get(operation, operation.capitalize())

        # Build a compact human-readable summary, skipping absent fields.
        head_bits = [b for b in (direction, symbol) if b]
        lots = _fmt_num(lot_size)
        if lots:
            head_bits.append(f"{lots} lots")
        head = " ".join(head_bits) or (symbol or "order")

        tail: list[str] = []
        px = _fmt_num(price)
        if px:
            tail.append(f"@ {px}")
        sl, tp = _fmt_num(stop_loss), _fmt_num(take_profit)
        if sl or tp:
            tail.append(f"(SL {sl or '—'} / TP {tp or '—'})")
        if pnl is not None and (pnlf := _fmt_num(pnl)) is not None:
            tail.append(f"PnL {pnlf}")
        if result:
            tail.append(str(result))
        ref = ticket if ticket is not None else trade_id
        if ref is not None:
            tail.append(f"#{ref}")

        message = f"{emoji} {verb} {head}" + (" " + " ".join(tail) if tail else "")

        context: dict[str, Any] = {
            "operation": operation,
            "symbol": symbol,
            "direction": direction,
            "lot_size": lot_size,
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "ticket": ticket,
            "trade_id": trade_id,
            "pnl": pnl,
            "result": result,
        }
        if extra:
            context.update(extra)
        context = {k: v for k, v in context.items() if v is not None}

        dispatch_alert(
            db, "info", TRADE_OPERATIONS_EVENT, message,
            context=context,
            throttle_key=f"{TRADE_OPERATIONS_EVENT}:{operation}:{ref}",
        )
    except Exception as exc:
        logger.debug("notify_trade_operation failed: %s", exc)
