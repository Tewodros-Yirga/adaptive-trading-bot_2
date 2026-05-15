import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..db import get_database
from ..auth_deps import get_current_user_from_token

router = APIRouter(tags=["websocket"])

_connections: list[WebSocket] = []


async def broadcast(event_type: str, data: Any):
    msg = json.dumps({"type": event_type, "data": data})
    dead = []
    for ws in _connections:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.remove(ws)


async def broadcast_ws_event(event: dict) -> None:
    """
    Broadcast a pre-formed event dict to all connected WebSocket clients.
    Reads 'event' or 'type' key for the event_type; falls back to 'event'.
    Called by continuous_backtest and other services.
    """
    event_type = event.get("event") or event.get("type") or "event"
    await broadcast(event_type, event)


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
):
    """
    WebSocket endpoint with JWT authentication.

    Clients must pass a valid JWT as ?token=<jwt> in the upgrade URL.
    The connection is accepted first (so the upgrade succeeds), then closed
    with code 4001 if the token is missing, invalid, or belongs to an
    inactive user. This prevents Starlette from emitting an HTTP 403.
    """
    await websocket.accept()

    # pymongo Database — no close() needed (pool managed by driver)
    db = get_database()
    user = get_current_user_from_token(token, db)

    if not user:
        await websocket.close(code=4001)
        return

    _connections.append(websocket)
    try:
        while True:
            # Keep connection alive — ping every 30 s
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        if websocket in _connections:
            _connections.remove(websocket)
    except Exception:
        if websocket in _connections:
            _connections.remove(websocket)