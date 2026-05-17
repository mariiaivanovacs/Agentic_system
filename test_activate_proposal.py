from src.agents import tools


def test_activate_proposal_returns_updated_flow(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        tools,
        "_run_read_cypher",
        lambda cypher, params: [
            {
                "id": "flow-123",
                "name": "Course run optimizer",
                "status": "approved",
                "project_id": "project-1",
                "business_flow_id": "bf-1",
                "last_registry_merge_at": None,
                "merge_event_id": None,
            }
        ],
    )

    def fake_write(cypher, params):
        captured["cypher"] = cypher
        captured["params"] = params
        return [
            {
                "id": "flow-123",
                "name": "Course run optimizer",
                "status": "active",
                "project_id": "project-1",
                "business_flow_id": "bf-1",
            }
        ]

    monkeypatch.setattr(tools, "_run_write_cypher", fake_write)

    result = tools.activate_proposal("flow-123")

    assert result == {
        "id": "flow-123",
        "name": "Course run optimizer",
        "status": "active",
        "project_id": "project-1",
        "business_flow_id": "bf-1",
    }
    assert captured["params"]["id"] == "flow-123"
    assert captured["params"]["merged_by"] == "streamlit_ui"
    assert captured["params"]["merge_source"] == "human_approval"
    assert captured["params"]["event_id"].startswith("registry_merge_")
    assert "RETURN f.id AS id" in captured["cypher"]
    assert "RegistryMergeEvent" in captured["cypher"]
    assert "last_registry_merge_at" in captured["cypher"]


def test_activate_proposal_returns_none_when_flow_missing(monkeypatch):
    monkeypatch.setattr(tools, "_run_read_cypher", lambda cypher, params: [])
    monkeypatch.setattr(tools, "_run_write_cypher", lambda cypher, params: [])

    assert tools.activate_proposal("missing-flow") is None


def test_activate_proposal_is_idempotent_when_already_merged(monkeypatch):
    existing = {
        "id": "flow-123",
        "name": "Course run optimizer",
        "status": "active",
        "project_id": "project-1",
        "business_flow_id": "bf-1",
        "last_registry_merge_at": "2026-05-16T23:21:37.731Z",
        "last_registry_merge_by": "sandbox_ui",
        "last_registry_merge_source": "approved_flow_sandbox",
        "registry_merge_count": 1,
        "merge_event_id": "registry_merge_existing",
    }
    monkeypatch.setattr(tools, "_run_read_cypher", lambda cypher, params: [existing])

    def fail_write(cypher, params):
        raise AssertionError("already-merged flows should not create another merge event")

    monkeypatch.setattr(tools, "_run_write_cypher", fail_write)

    assert tools.activate_proposal("flow-123") == existing
