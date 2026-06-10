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
import time
from typing import Any

from pymongo.database import Database

from .. import crud

logger = logging.getLogger("alerts")

_LEVELS = {"info": 10, "warning": 20, "critical": 30}

# In-memory per-event throttle (monotonic clock; resets on process restart).
_last_forwarded: dict[str, float] = {}


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


def _http_post(url: str, json_body: dict, timeout: float = 15.0):
    """POST helper that forces IPv4 and uses a generous timeout.

    Container hosts (Render, etc.) often advertise IPv6 (AAAA) routes that
    black-hole outbound traffic, so an httpx call to api.telegram.org picks the
    IPv6 address and the TLS handshake hangs until timeout
    (``_ssl.c:999: handshake operation timed out``) — even though IPv4 works
    fine and a local curl succeeds. Binding the socket to an IPv4 source address
    (``local_address="0.0.0.0"``) forces IPv4 and avoids the dead IPv6 path.
    """
    import httpx

    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=1)
    with httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(timeout, connect=timeout),
    ) as client:
        return client.post(url, json=json_body)


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
