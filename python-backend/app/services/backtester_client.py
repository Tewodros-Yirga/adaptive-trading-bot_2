"""
services/backtester_client.py

Centralised async HTTP client for all backend → backtester-service calls.

Every request automatically includes the HuggingFace Bearer token so the
private Space accepts it. Import ``backtester`` from here wherever you need
to talk to the backtester — never construct ad-hoc httpx calls elsewhere.

Usage
-----
from .services.backtester_client import BacktesterClient

client = BacktesterClient()
status = await client.get_status("dtc")
await client.pause("dtc")
await client.resume("dtc")
await client.trigger("dtc")
pong   = await client.ping()
health = await client.health()
all_st = await client.all_statuses()
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve config at import time so every call gets the same values.
# Reads from the app Settings object first, then falls back to raw env vars.
# ---------------------------------------------------------------------------

def _resolve_setting(attr: str, env_key: str, default: str = "") -> str:
    try:
        from .config import settings  # type: ignore
        val = getattr(settings, attr, None)
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get(env_key, default)


class BacktesterClient:
    """
    Thin async wrapper around the backtester microservice REST API.

    All methods raise ``BacktesterUnavailable`` if the backtester URL is not
    configured, or ``httpx.HTTPStatusError`` / ``httpx.RequestError`` on
    network/HTTP errors — callers should catch these and degrade gracefully.
    """

    def __init__(self) -> None:
        self._base_url = _resolve_setting("backtester_url", "BACKTESTER_URL").rstrip("/")
        self._hf_token = _resolve_setting("backtester_hf_token", "BACKTESTER_HF_TOKEN")
        self._timeout = 20.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._hf_token:
            h["Authorization"] = f"Bearer {self._hf_token}"
        return h

    def _url(self, path: str) -> str:
        if not self._base_url:
            raise BacktesterUnavailable(
                "BACKTESTER_URL is not set. "
                "Add it to your backend env/secrets "
                "(e.g. https://youruser-spacename.hf.space)."
            )
        return f"{self._base_url}/{path.lstrip('/')}"

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._url(path), headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url(path), headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Public API — mirrors the backtester service endpoints
    # ------------------------------------------------------------------

    async def ping(self) -> dict:
        """GET /ping — lightweight keepalive, no DB work."""
        return await self._get("/ping")

    async def health(self) -> dict:
        """GET /health — returns running strategies + uptime."""
        return await self._get("/health")

    async def all_statuses(self) -> dict:
        """GET /status — all strategy statuses."""
        return await self._get("/status")

    async def get_status(self, strategy_name: str) -> dict:
        """GET /status/{strategy_name}"""
        return await self._get(f"/status/{strategy_name}")

    async def pause(self, strategy_name: str) -> dict:
        """POST /pause/{strategy_name}"""
        return await self._post(f"/pause/{strategy_name}")

    async def resume(self, strategy_name: str) -> dict:
        """POST /resume/{strategy_name}"""
        return await self._post(f"/resume/{strategy_name}")

    async def trigger(self, strategy_name: str) -> dict:
        """POST /trigger/{strategy_name} — skip current sleep, run immediately."""
        return await self._post(f"/trigger/{strategy_name}")

    @property
    def is_configured(self) -> bool:
        """True if BACKTESTER_URL is set (token may still be missing)."""
        return bool(self._base_url)


class BacktesterUnavailable(RuntimeError):
    """Raised when BACKTESTER_URL is not configured."""