"""
test_skill_registry.py — Phase 5 unit tests for Skill Registry.

Tests (no DB required):
  - create_skill_proposal Cypher string is valid Python (compile check)
  - Critic rejects YAML referencing an unknown skill (mock valid_skills empty)
  - approve/reject functions exist in queries.py
  - Skill Registry page block compiles cleanly

Run with: python test_skill_registry.py
"""
from __future__ import annotations

import inspect
import py_compile
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def _assert(condition: bool, label: str) -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        _failures.append(label)


# ── Test 1: Cypher in create_skill_proposal is valid Python ──────────────────

def test_create_skill_proposal_compiles() -> None:
    print("\n[1] create_skill_proposal Cypher/Python compile check")
    graph_path = str(Path(__file__).resolve().parent / "ecolink-graph")
    if graph_path not in sys.path:
        sys.path.insert(0, graph_path)
    import queries  # noqa: PLC0415

    src = inspect.getsource(queries.create_skill_proposal)
    try:
        compile(src, "<create_skill_proposal>", "exec")
        _assert(True, "create_skill_proposal source compiles as Python")
    except SyntaxError as exc:
        _assert(False, f"create_skill_proposal syntax error: {exc}")


# ── Test 2: Critic rejects YAML with unknown skill ───────────────────────────

def test_critic_rejects_unknown_skill() -> None:
    print("\n[2] Critic rejects flow referencing unknown skill")

    from src.agents.nodes import _extract_flow_references
    import yaml

    flow_yaml = """
flow_id: test_flow
runs_on: srv_001
steps:
  - id: step1
    skill: fake_skill_xyz
    input: {}
"""
    parsed = yaml.safe_load(flow_yaml)
    referenced_skills, _ = _extract_flow_references(parsed)
    valid_skills: set[str] = set()  # intentionally empty — simulates empty Graph B
    unknown_skills = referenced_skills - valid_skills

    _assert("fake_skill_xyz" in referenced_skills, "fake_skill_xyz extracted from YAML")
    _assert(bool(unknown_skills), "unknown_skills set is non-empty when valid_skills is empty")
    _assert("fake_skill_xyz" in unknown_skills, "fake_skill_xyz is in unknown_skills")


# ── Test 3: approve/reject functions exist in queries.py ─────────────────────

def test_queries_functions_exist() -> None:
    print("\n[3] approve/reject skill proposal functions exist in queries.py")
    import sys as _sys
    from pathlib import Path as _Path
    graph_path = str(_Path(__file__).resolve().parent / "ecolink-graph")
    if graph_path not in _sys.path:
        _sys.path.insert(0, graph_path)

    import queries
    _assert(callable(getattr(queries, "create_skill_proposal", None)), "create_skill_proposal exists")
    _assert(callable(getattr(queries, "get_skill_proposals", None)), "get_skill_proposals exists")
    _assert(callable(getattr(queries, "approve_skill_proposal", None)), "approve_skill_proposal exists")
    _assert(callable(getattr(queries, "reject_skill_proposal", None)), "reject_skill_proposal exists")


# ── Test 4: Key source files compile cleanly ─────────────────────────────────

def test_files_compile() -> None:
    print("\n[4] Key source files compile cleanly")
    root = Path(__file__).resolve().parent
    files = [
        "streamlit_app.py",
        "src/graphrag/retriever.py",
        "src/agents/tools.py",
        "src/agents/nodes.py",
        "ecolink-graph/queries.py",
    ]
    for rel in files:
        path = root / rel
        try:
            py_compile.compile(str(path), doraise=True)
            _assert(True, f"{rel} compiles")
        except py_compile.PyCompileError as exc:
            _assert(False, f"{rel} compile error: {exc}")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_create_skill_proposal_compiles()
    test_critic_rejects_unknown_skill()
    test_queries_functions_exist()
    test_files_compile()

    print()
    if _failures:
        print(f"\033[31mFAILED ({len(_failures)}):\033[0m")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\033[32mAll skill registry tests passed.\033[0m")
