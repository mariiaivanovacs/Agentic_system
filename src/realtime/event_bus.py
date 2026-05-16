from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
EVENT_DIR = ROOT / ".agent_events"
EVENT_FILE = EVENT_DIR / "events.jsonl"
DEFAULT_THREAD_ID = "system"
REALTIME_EVENT_URL = os.environ.get(
    "REALTIME_EVENT_URL",
    "http://127.0.0.1:8765/events",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return {
        "event_id": event.get("event_id") or uuid.uuid4().hex,
        "thread_id": event.get("thread_id") or DEFAULT_THREAD_ID,
        "source": event.get("source") or "system",
        "target": event.get("target") or "",
        "event_type": event.get("event_type") or "message",
        "title": event.get("title") or "Event",
        "detail": event.get("detail") or "",
        "payload": payload if isinstance(payload, dict) else {},
        "created_at": event.get("created_at") or _now_iso(),
    }


def append_event(event: dict[str, Any]) -> dict[str, Any]:
    normalised = normalise_event(event)
    EVENT_DIR.mkdir(exist_ok=True)
    with EVENT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(normalised, ensure_ascii=True) + "\n")
    return normalised


def read_events(limit: int = 200, thread_id: str | None = None) -> list[dict[str, Any]]:
    if not EVENT_FILE.exists():
        return []

    rows: list[dict[str, Any]] = []
    with EVENT_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if thread_id and event.get("thread_id") != thread_id:
                continue
            rows.append(event)
    return rows[-max(1, int(limit)):]


def publish_event(
    *,
    thread_id: str | None = None,
    source: str,
    event_type: str,
    title: str,
    detail: str = "",
    target: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = normalise_event(
        {
            "thread_id": thread_id or DEFAULT_THREAD_ID,
            "source": source,
            "target": target,
            "event_type": event_type,
            "title": title,
            "detail": detail,
            "payload": payload or {},
        }
    )

    try:
        response = requests.post(REALTIME_EVENT_URL, json=event, timeout=0.8)
        response.raise_for_status()
        return response.json()
    except Exception:
        return append_event(event)

