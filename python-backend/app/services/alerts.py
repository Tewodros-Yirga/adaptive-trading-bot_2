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


def _enabled_channels(db: Database) -> list[tuple[str, dict]]:
    channels: list[tuple[str, dict]] = []
    tg_token = crud.get_setting(db, "alerts_telegram_bot_token")
    tg_chat = crud.get_setting(db, "alerts_telegram_chat_id")
    if tg_token and tg_chat:
        channels.append(("telegram", {"token": tg_token, "chat_id": tg_chat}))
    hook = crud.get_setting(db, "alerts_webhook_url")
    if hook:
        channels.append(("webhook", {"url": hook}))
    return channels


def _forward(channel: str, cfg: dict, level: str, event: str, message: str, context: dict | None) -> None:
    import httpx

    text = f"[{level.upper()}] {event}: {message}"
    try:
        if channel == "telegram":
            httpx.post(
                f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
                json={"chat_id": cfg["chat_id"], "text": text},
                timeout=5.0,
            )
        elif channel == "webhook":
            httpx.post(
                cfg["url"],
                json={
                    "level": level,
                    "event": event,
                    "message": message,
                    "context": context or {},
                },
                timeout=5.0,
            )
    except Exception as exc:  # never let alerting break the caller
        logger.debug("Alert forward via %s failed: %s", channel, exc)


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
