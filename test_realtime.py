from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import src.realtime.event_bus as event_bus
from src.realtime.server import app


def test_event_write_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        original_dir = event_bus.EVENT_DIR
        original_file = event_bus.EVENT_FILE
        event_bus.EVENT_DIR = Path(tmp)
        event_bus.EVENT_FILE = Path(tmp) / "events.jsonl"
        try:
            saved = event_bus.append_event(
                {
                    "source": "planner",
                    "event_type": "started",
                    "title": "Planner started",
                }
            )
            assert saved["event_id"]
            assert saved["thread_id"] == "system"
            assert saved["created_at"]
            rows = event_bus.read_events()
            assert len(rows) == 1
            assert rows[0]["title"] == "Planner started"
        finally:
            event_bus.EVENT_DIR = original_dir
            event_bus.EVENT_FILE = original_file


def test_realtime_api() -> None:
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    posted = client.post(
        "/events",
        json={
            "thread_id": "test-thread",
            "source": "ui",
            "event_type": "message",
            "title": "API test",
            "detail": "hello",
        },
    )
    assert posted.status_code == 200
    body = posted.json()
    assert body["event_id"]
    assert body["thread_id"] == "test-thread"

    events = client.get("/events", params={"limit": 5, "thread_id": "test-thread"})
    assert events.status_code == 200
    assert any(row["title"] == "API test" for row in events.json())


if __name__ == "__main__":
    test_event_write_round_trip()
    test_realtime_api()
    print("Realtime tests passed")
