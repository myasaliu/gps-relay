"""
gps-relay - Zero-storage WebSocket relay for gps-bridge.

Architecture:
    Phone  ──WebSocket──► /ws/{token} ──► OpenClaw (gps-bridge)

The server holds active WebSocket connections in RAM only.
No data is ever written to disk or logged.
When a message arrives from one side, it is forwarded immediately
to all other connections sharing the same token, then discarded.

Security:
    - Tokens shorter than MIN_TOKEN_LENGTH characters are rejected (4008).
    - Each token is limited to MAX_CONNECTIONS_PER_TOKEN simultaneous connections (4009).
    - Each connection is limited to RATE_LIMIT messages per RATE_WINDOW seconds.
      Exceeding the limit closes the connection (4029).
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------

MIN_TOKEN_LENGTH = 32       # reject short / guessable tokens
MAX_CONNECTIONS_PER_TOKEN = 3  # reject if token already has too many connections
RATE_LIMIT = 10             # max messages per connection per window
RATE_WINDOW = 30            # seconds

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="gps-relay",
    description=(
        "Zero-storage WebSocket relay. "
        "Messages are forwarded in real-time and never persisted."
    ),
)

# In-memory only.  Maps token -> list[WebSocket].
# Nothing is written to disk at any point.
_connections: dict[str, list[WebSocket]] = defaultdict(list)

# Rate tracking: Maps websocket id -> list of message timestamps (floats)
_rate_tracker: dict[int, list[float]] = defaultdict(list)


def _check_rate_limit(ws_id: int) -> bool:
    """Return True if the connection is within the rate limit, False if exceeded."""
    now = time.monotonic()
    timestamps = _rate_tracker[ws_id]
    # Discard timestamps outside the current window
    _rate_tracker[ws_id] = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(_rate_tracker[ws_id]) >= RATE_LIMIT:
        return False
    _rate_tracker[ws_id].append(now)
    return True


# ---------------------------------------------------------------------------
# WebSocket relay endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws/{token}")
async def relay(websocket: WebSocket, token: str) -> None:
    """
    Both the phone and OpenClaw connect here with the same token.
    Every message received from one side is forwarded to all other
    connections sharing that token, then immediately discarded.
    """
    # Fix 1: reject short tokens before accepting the connection
    if len(token) < MIN_TOKEN_LENGTH:
        await websocket.close(code=4008, reason="Token too short (min 32 chars).")
        return

    # Fix 3: reject if token already has too many connections
    if len(_connections[token]) >= MAX_CONNECTIONS_PER_TOKEN:
        await websocket.close(code=4009, reason="Too many connections for this token.")
        return

    await websocket.accept()
    ws_id = id(websocket)
    _connections[token].append(websocket)
    try:
        while True:
            data = await websocket.receive_text()

            # Fix 2: rate limit per connection
            if not _check_rate_limit(ws_id):
                await websocket.close(
                    code=4029, reason="Rate limit exceeded. Slow down."
                )
                break

            peers = [ws for ws in _connections[token] if ws is not websocket]
            for peer in peers:
                try:
                    await peer.send_text(data)
                except Exception:
                    pass  # peer disconnected between check and send
    except WebSocketDisconnect:
        pass
    finally:
        _connections[token].remove(websocket)
        if not _connections[token]:
            del _connections[token]
        _rate_tracker.pop(ws_id, None)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    """
    Returns the number of currently active tokens (not the tokens themselves).
    Safe to expose publicly.
    """
    return JSONResponse({"status": "ok", "active_tokens": len(_connections)})
