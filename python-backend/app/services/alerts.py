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
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

from pymongo.database import Database

from .. import crud

logger = logging.getLogger("alerts")

_LEVELS = {"info": 10, "warning": 20, "critical": 30}

# In-memory per-event throttle (monotonic clock; resets on process restart).
_last_forwarded: dict[str, float] = {}

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


def _enabled_channels(db: Database) -> list[tuple[str, dict]]:
    channels: list[tuple[str, dict]] = []
    tg_token = _first_setting(db, _TG_TOKEN_KEYS)
    tg_chat = _first_setting(db, _TG_CHAT_KEYS)
    if tg_token and tg_chat:
        # Tolerate a token saved with a leading "bot" prefix or full API path.
        tg_token = tg_token.removeprefix("bot").strip()
        channels.append(("telegram", {"token": tg_token, "chat_id": tg_chat}))
    hook = crud.get_setting(db, "alerts_webhook_url")
    if hook and hook.strip():
        channels.append(("webhook", {"url": hook.strip()}))
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


def _send_once(host: str, ip: str, request: bytes, timeout: float) -> _Resp:
    """Open one IPv4 TLS connection to ``ip`` (SNI/cert validated against
    ``host``), send a pre-built HTTP/1.0 request, and parse the response.

    HTTP/1.0 with ``Connection: close`` keeps parsing trivial (read to EOF) and
    sidesteps keep-alive/chunked edge cases — alerts are tiny one-shot POSTs.
    """
    raw = socket.create_connection((ip, 443), timeout=timeout)
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


def _http_post(url: str, json_body: dict, timeout: float = 10.0):
    """Robust IPv4-only JSON POST for tiny alert payloads.

    Resolves the host to its IPv4 edge IPs and tries each one (last-known-good
    first) with a short per-attempt timeout, so a single black-holed Telegram
    edge IP — the usual cause of ``_ssl.c:999: handshake operation timed out`` —
    no longer fails the whole send. The first IP to complete a TLS handshake is
    cached for subsequent calls. Uses only the stdlib (socket + ssl) so it does
    not depend on httpx's connection/retry behaviour.
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

    ips = _ipv4_addresses(host)
    if not ips:
        raise OSError(f"could not resolve any IPv4 address for {host}")

    # Try the last known-good IP first, then the rest, without duplicates.
    cached = _last_good_ip.get(host)
    ordered = ([cached] if cached in ips else []) + [ip for ip in ips if ip != cached]

    last_exc: Exception | None = None
    for attempt, ip in enumerate(ordered):
        try:
            resp = _send_once(host, ip, request, timeout)
            _last_good_ip[host] = ip
            return resp
        except Exception as exc:
            last_exc = exc
            _last_good_ip.pop(host, None)
            logger.debug("alert POST to %s via %s failed: %s", host, ip, exc)
            # brief backoff between edge IPs; keeps total time bounded
            if attempt + 1 < len(ordered):
                time.sleep(0.25)

    raise last_exc or OSError(f"all {len(ordered)} IPv4 routes to {host} failed")


def _forward(channel: str, cfg: dict, level: str, event: str, message: str, context: dict | None) -> dict:
    """Best-effort forward to one channel. Returns {"ok": bool, "detail": str}
    describing the actual delivery result (e.g. Telegram's API response) so the
    'send test message' button can report real success/failure. Never raises."""
    text = f"[{level.upper()}] {event}: {message}"
    try:
        if channel == "telegram":
            resp = _http_post(
                f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
                {"chat_id": cfg["chat_id"], "text": text},
            )
            # Telegram replies 200 with {"ok": true, ...} on success, or a 4xx
            # with {"ok": false, "description": "..."} on bad token/chat_id.
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            if resp.status_code == 200 and body.get("ok"):
                return {"ok": True, "detail": "delivered"}
            detail = body.get("description") or f"HTTP {resp.status_code}"
            logger.warning("Telegram send failed: %s", detail)
            return {"ok": False, "detail": detail}
        elif channel == "webhook":
            resp = _http_post(
                cfg["url"],
                {
                    "level": level,
                    "event": event,
                    "message": message,
                    "context": context or {},
                },
            )
            ok = resp.status_code < 400
            return {"ok": ok, "detail": "delivered" if ok else f"HTTP {resp.status_code}"}
    except Exception as exc:  # never let alerting break the caller
        logger.warning("Alert forward via %s failed: %s", channel, exc)
        return {"ok": False, "detail": str(exc)}
    return {"ok": False, "detail": "unknown channel"}


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
) -> None:
    """Emit a structured event and best-effort forward it to configured channels.

    `level` is one of "info" | "warning" | "critical". Safe to call from any
    sync context; it never raises.
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
        min_level = str(crud.get_setting(db, "alerts_min_level") or "warning").lower()
        if _LEVELS[level] < _LEVELS.get(min_level, 20):
            return

        # Optional per-event allow-list. Empty or "all" → forward everything.
        allow_raw = str(crud.get_setting(db, "alerts_enabled_events") or "").strip().lower()
        if allow_raw and allow_raw != "all":
            allowed = {e.strip() for e in allow_raw.split(",") if e.strip()}
            if event.lower() not in allowed:
                return

        channels = _enabled_channels(db)
        if not channels:
            return

        throttle = float(crud.get_setting(db, "alerts_throttle_seconds") or 300)
        now = time.monotonic()
        last = _last_forwarded.get(event, 0.0)
        if now - last < throttle:
            return
        _last_forwarded[event] = now

        for channel, cfg in channels:
            _forward(channel, cfg, level, event, message, context)
    except Exception as exc:
        logger.debug("dispatch_alert failed: %s", exc)
