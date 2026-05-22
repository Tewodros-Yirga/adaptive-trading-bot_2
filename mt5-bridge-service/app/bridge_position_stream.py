"""
bridge/position_stream.py — Server-Sent Events endpoint for the MT5 bridge.

Mount this router on the bridge FastAPI app:

    from .position_stream import router as stream_router
    app.include_router(stream_router)

The endpoint GET /stream/positions streams a JSON event every POLL_SECS
containing the current list of open positions plus a diff summary.

Clients (the backend) connect once and receive a continuous stream. If
the backend disconnects and reconnects the bridge simply resumes from the
current state — no history is buffered.

This eliminates the need for the backend to poll GET /positions every
second and reduces repeated MT5 adapter calls.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# How often to emit a position snapshot (seconds)
POLL_SECS: float = 5.0

router = APIRouter(tags=["stream"])


def _require_secret(request: Request) -> None:
    """Reuse the same bridge-secret auth used by other endpoints."""
    secret = request.headers.get("X-Bridge-Secret", "")
    from .config import settings
    if secret != settings.mt_bridge_secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid bridge secret")


def _pos_key(pos: dict) -> int:
    return int(pos.get("ticket") or 0)


def _positions_snapshot() -> list[dict]:
    """Synchronously fetch positions from the MT5 adapter."""
    try:
        from .mt5_adapter import adapter
        return adapter.positions()
    except Exception as exc:
        logger.warning("SSE: adapter.positions() failed: %s", exc)
        return []


async def _event_generator(request: Request) -> AsyncIterator[str]:
    """
    Async generator yielding SSE-formatted position snapshot events.

    Each event is JSON with:
      type: "snapshot" | "diff"
      positions: [ ...current open positions... ]
      opened:   [ ...tickets newly appeared since last snapshot... ]
      closed:   [ ...tickets that disappeared since last snapshot... ]
      modified: [ ...tickets whose volume/sl/tp changed... ]
      ts: ISO timestamp
    """
    prev: dict[int, dict] = {}
    loop = asyncio.get_event_loop()

    # Send an initial heartbeat immediately so the client knows the connection is live
    yield "event: connected\ndata: {}\n\n"

    while True:
        # Respect client disconnect
        if await request.is_disconnected():
            logger.info("SSE /stream/positions: client disconnected")
            return

        try:
            raw: list[dict] = await loop.run_in_executor(None, _positions_snapshot)
        except Exception as exc:
            # Don't close the stream on a single poll failure
            logger.warning("SSE poll error: %s", exc)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(POLL_SECS)
            continue

        curr: dict[int, dict] = {_pos_key(p): p for p in raw if _pos_key(p)}

        prev_keys = set(prev.keys())
        curr_keys = set(curr.keys())

        opened   = [curr[k] for k in curr_keys - prev_keys]
        closed   = [prev[k] for k in prev_keys - curr_keys]
        modified = []

        for k in prev_keys & curr_keys:
            old = prev[k]
            new = curr[k]
            # Detect volume change (partial close) or SL/TP change
            old_vol = float(old.get("volume") or 0)
            new_vol = float(new.get("volume") or 0)
            if (
                abs(old_vol - new_vol) > 0.001
                or old.get("sl") != new.get("sl")
                or old.get("tp") != new.get("tp")
            ):
                modified.append({
                    "ticket": k,
                    "old": old,
                    "new": new,
                    "volume_delta": round(old_vol - new_vol, 8),
                })

        event_type = "snapshot" if not (opened or closed or modified) else "diff"

        payload = json.dumps({
            "type": event_type,
            "positions": list(curr.values()),
            "opened": opened,
            "closed": closed,
            "modified": modified,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        yield f"event: {event_type}\ndata: {payload}\n\n"
        prev = curr

        await asyncio.sleep(POLL_SECS)


@router.get("/stream/positions", dependencies=[Depends(_require_secret)])
async def stream_positions(request: Request) -> StreamingResponse:
    """
    Server-Sent Events stream of MT5 position changes.

    Connect with:
        EventSource('/stream/positions', { headers: { 'X-Bridge-Secret': '...' } })

    Events:
        connected  — emitted once on connect
        snapshot   — full position list when nothing changed
        diff       — position list + opened/closed/modified arrays
        error      — poll failure (stream stays open)
    """
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering
        },
    )


@router.get("/positions/current", dependencies=[Depends(_require_secret)])
async def get_current_positions() -> dict:
    """
    Synchronous snapshot of current open positions + summary.
    Convenience endpoint when SSE is not needed (e.g. health checks).
    """
    loop = asyncio.get_event_loop()
    positions = await loop.run_in_executor(None, _positions_snapshot)
    return {
        "count": len(positions),
        "positions": positions,
        "ts": datetime.now(timezone.utc).isoformat(),
    }