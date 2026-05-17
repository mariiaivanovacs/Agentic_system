from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import src.realtime.event_bus as event_bus
from src.realtime.server import app
from src.realtime.ui import event_visual_transition


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


def test_event_read_limit_does_not_return_full_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        original_dir = event_bus.EVENT_DIR
        original_file = event_bus.EVENT_FILE
        event_bus.EVENT_DIR = Path(tmp)
        event_bus.EVENT_FILE = Path(tmp) / "events.jsonl"
        try:
            for index in range(25):
                event_bus.append_event(
                    {
                        "thread_id": "thread-a" if index % 2 == 0 else "thread-b",
                        "source": "planner",
                        "event_type": "message",
                        "title": f"Event {index}",
                    }
                )

            rows = event_bus.read_events(limit=5)
            assert len(rows) == 5
            assert rows[0]["title"] == "Event 20"
            scoped = event_bus.read_events(limit=3, thread_id="thread-a")
            assert len(scoped) == 3
            assert all(row["thread_id"] == "thread-a" for row in scoped)
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


def test_realtime_websocket_broadcasts_posted_event() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/events") as websocket:
        posted = client.post(
            "/events",
            json={
                "thread_id": "ws-thread",
                "source": "critic",
                "target": "generator",
                "event_type": "decision",
                "title": "Critic rejected flow locally",
            },
        )
        assert posted.status_code == 200
        received = websocket.receive_json()
        assert received["thread_id"] == "ws-thread"
        assert received["source"] == "critic"
        assert received["target"] == "generator"


def test_event_visual_transition_matches_langgraph_decisions() -> None:
    cases = [
        (
            {"source": "planner", "target": "generator", "event_type": "thinking"},
            {"active_node": "planner", "active_edge": "planner-generator", "is_retry": False, "is_terminal": False},
        ),
        (
            {"source": "critic", "target": "simulator", "event_type": "decision"},
            {"active_node": "critic", "active_edge": "critic-simulator", "is_retry": False, "is_terminal": False},
        ),
        (
            {"source": "critic", "target": "generator", "event_type": "decision"},
            {"active_node": "critic", "active_edge": "critic-generator", "is_retry": True, "is_terminal": False},
        ),
        (
            {"source": "evaluator", "target": "generator", "event_type": "decision"},
            {"active_node": "evaluator", "active_edge": "evaluator-generator", "is_retry": True, "is_terminal": False},
        ),
        (
            {"source": "evaluator", "target": "human_approval", "event_type": "approval_required"},
            {"active_node": "evaluator", "active_edge": "evaluator-human_approval", "is_retry": False, "is_terminal": False},
        ),
        (
            {"source": "human_approval", "event_type": "approved"},
            {"active_node": "end", "active_edge": "human_approval-end", "is_retry": False, "is_terminal": True},
        ),
        (
            {"source": "evaluator", "event_type": "decision"},
            {"active_node": "end", "active_edge": "evaluator-end", "is_retry": False, "is_terminal": True},
        ),
        (
            {"source": "critic", "event_type": "decision"},
            {"active_node": "end", "active_edge": "critic-end", "is_retry": False, "is_terminal": True},
        ),
    ]
    for event, expected in cases:
        actual = event_visual_transition(event)
        for key, value in expected.items():
            assert actual[key] == value


if __name__ == "__main__":
    test_event_write_round_trip()
    test_event_read_limit_does_not_return_full_file()
    test_realtime_api()
    test_realtime_websocket_broadcasts_posted_event()
    test_event_visual_transition_matches_langgraph_decisions()
    print("Realtime tests passed")
