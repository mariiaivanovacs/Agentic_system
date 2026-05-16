"""
test_sandbox.py — verifies the full sandbox pipeline end-to-end.

Tests (in order):
  1. sandbox_task.py runs standalone with sample data
  2. _build_snapshot() returns real Neo4j data
  3. _local_sandbox() runs sandbox_task.py and returns parsed metrics
  4. simulate_flow tool (SANDBOX_MOCK=false, SANDBOX_MODE=local) returns metrics
  5. SANDBOX_MOCK=true still returns the mock response

Run:
    python test_sandbox.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


SANDBOX_TASK = Path(__file__).parent / "sandbox-system" / "sandbox_task.py"

SAMPLE_FLOW_YAML = """
flow_id: flow_proposal_test_v1
description: "Test flow for sandbox verification"
runs_on: srv_002
steps:
  - skill: semantic_similarity
    params:
      source: company.pain_points
      target: mentor.expertise
  - skill: score_calculator
    params: {}
"""

SAMPLE_SNAPSHOT = {
    "companies": [
        {"id": "C-01", "name": "Nexus AI", "industry": "Fintech"},
        {"id": "C-02", "name": "Etech Finance", "industry": "Fintech"},
        {"id": "C-03", "name": "HealthBridge", "industry": "Healthtech"},
    ],
    "mentors": [
        {"id": "M-01", "name": "Dr. Kuan Studio", "expertise": ["Finance", "Scaling"]},
        {"id": "M-02", "name": "Darveen Ventures", "expertise": ["Marketing", "Product"]},
    ],
}


def separator(title: str) -> None:
    print(f"\n{'─' * 4} {title} {'─' * max(0, 56 - len(title))}")


def test_1_sandbox_task_standalone():
    separator("Test 1: sandbox_task.py standalone")
    assert SANDBOX_TASK.exists(), f"sandbox_task.py not found at {SANDBOX_TASK}"

    env = os.environ.copy()
    env["SNAPSHOT_DATA"] = json.dumps(SAMPLE_SNAPSHOT)
    env["PROPOSED_FLOW"] = "semantic_similarity_v2"

    result = subprocess.run(
        [sys.executable, str(SANDBOX_TASK)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    print(f"  Exit code : {result.returncode}")
    print(f"  stdout    :\n{result.stdout[:600]}")
    if result.stderr:
        print(f"  stderr    : {result.stderr[:200]}")

    assert result.returncode == 0, f"Non-zero exit: {result.stderr}"
    assert "DATA_STREAM_START" in result.stdout, "Missing DATA_STREAM_START marker"
    assert "DATA_STREAM_END" in result.stdout, "Missing DATA_STREAM_END marker"
    assert "SIMULATION_SUCCESS" in result.stdout, "No SIMULATION_SUCCESS in output"
    print("  PASS")


def test_2_build_snapshot():
    separator("Test 2: _build_snapshot() from Neo4j")
    from src.agents.tools import _build_snapshot

    snapshot = _build_snapshot()
    print(f"  Companies : {len(snapshot['companies'])}")
    print(f"  Mentors   : {len(snapshot['mentors'])}")
    if snapshot["companies"]:
        print(f"  Sample co : {snapshot['companies'][0]}")
    if snapshot["mentors"]:
        print(f"  Sample me : {snapshot['mentors'][0]}")

    assert len(snapshot["companies"]) > 0, "No companies in snapshot"
    assert len(snapshot["mentors"]) > 0, "No mentors in snapshot"
    assert "id" in snapshot["companies"][0], "Company missing id"
    assert "id" in snapshot["mentors"][0], "Mentor missing id"
    print("  PASS")


def test_3_local_sandbox():
    separator("Test 3: _local_sandbox() with Neo4j snapshot")
    from src.agents.tools import _build_snapshot, _local_sandbox

    snapshot = _build_snapshot()
    result = _local_sandbox(SAMPLE_FLOW_YAML, snapshot)

    print(f"  status    : {result['status']}")
    print(f"  metrics   : {result.get('metrics', {})}")
    print(f"  traces    : {len(result.get('traces', []))} entries")
    if result.get("error_log"):
        print(f"  error_log : {result['error_log']}")

    assert result["status"] == "success", f"Sandbox failed: {result.get('error_log')}"
    assert "match_score" in result["metrics"], "Missing match_score in metrics"
    assert result["metrics"]["match_score"] > 0, "match_score should be > 0"
    assert result["metrics"]["sample_size"] > 0, "sample_size should be > 0"
    print("  PASS")


def test_4_simulate_flow_tool_real():
    separator("Test 4: simulate_flow tool (SANDBOX_MOCK=false, SANDBOX_MODE=local)")
    os.environ["SANDBOX_MOCK"] = "false"
    os.environ["SANDBOX_MODE"] = "local"

    from src.agents.tools import simulate_flow

    result = simulate_flow.invoke({
        "flow_yaml": SAMPLE_FLOW_YAML,
        "dataset_snapshot_id": "snapshot_test",
    })

    print(f"  status    : {result['status']}")
    print(f"  metrics   : {result.get('metrics', {})}")
    if result.get("error_log"):
        print(f"  error_log : {result['error_log']}")

    assert result["status"] == "success", f"simulate_flow failed: {result.get('error_log')}"
    assert result["metrics"]["match_score"] > 0
    print("  PASS")


def test_5_simulate_flow_mock():
    separator("Test 5: simulate_flow tool (SANDBOX_MOCK=true)")
    os.environ["SANDBOX_MOCK"] = "true"

    from importlib import reload
    import src.agents.tools as tools_mod
    reload(tools_mod)
    from src.agents.tools import simulate_flow

    result = simulate_flow.invoke({
        "flow_yaml": SAMPLE_FLOW_YAML,
        "dataset_snapshot_id": "snapshot_test",
    })

    print(f"  status    : {result['status']}")
    print(f"  metrics   : {result.get('metrics', {})}")

    assert result["status"] == "success"
    assert result["metrics"]["match_score"] == 8.7, "Mock should return 8.7 for semantic_similarity"
    print("  PASS")


if __name__ == "__main__":
    passed = 0
    failed = 0

    tests = [
        test_1_sandbox_task_standalone,
        test_2_build_snapshot,
        test_3_local_sandbox,
        test_4_simulate_flow_tool_real,
        test_5_simulate_flow_mock,
    ]

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {exc}")
            failed += 1

    separator("Results")
    print(f"  Passed: {passed}/{len(tests)}")
    print(f"  Failed: {failed}/{len(tests)}")
    if failed:
        sys.exit(1)
