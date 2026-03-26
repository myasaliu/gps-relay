"""
gps-relay - Zero-storage WebSocket relay for gps-bridge.

Architecture:
    Phone  ──WebSocket──► /ws/{token} ──► OpenClaw (gps-bridge)

The server holds active WebSocket connections in RAM only.
No data is ever written to disk or logged.
When a message arrives from one side, it is forwarded immediately
to all other connections sharing the same token, then discarded.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

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


@app.websocket("/ws/{token}")
async def relay(websocket: WebSocket, token: str) -> None:
    """
    Both the phone and OpenClaw connect here with the same token.
    Every message received from one side is forwarded to all other
    connections sharing that token, then immediately discarded.
    """
    await websocket.accept()
    _connections[token].append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
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


@app.get("/health")
async def health() -> JSONResponse:
    """
    Returns the number of currently active tokens (not the tokens themselves).
    Safe to expose publicly.
    """
    return JSONResponse({"status": "ok", "active_tokens": len(_connections)})
