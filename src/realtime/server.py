from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.realtime.event_bus import append_event, read_events


app = FastAPI(title="EcoLink Realtime Event Server")
clients: set[WebSocket] = set()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "clients": len(clients)}


@app.get("/events")
def events(
    limit: int = Query(default=200, ge=1, le=1000),
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    return read_events(limit=limit, thread_id=thread_id)


@app.post("/events")
async def post_event(event: dict[str, Any]) -> dict[str, Any]:
    saved = append_event(event)
    stale: list[WebSocket] = []
    for client in list(clients):
        try:
            await client.send_json(saved)
        except Exception:
            stale.append(client)
    for client in stale:
        clients.discard(client)
    return saved


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
    except Exception:
        clients.discard(websocket)

