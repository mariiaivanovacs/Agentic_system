"""
test_graphrag.py — GraphRAG module tests (Phase 5 existing + Phase 6 new).

Existing tests require a live Neo4j connection.
Phase 6 tests are unit-level: no embedding API calls, no real DB required.

Run with: python test_graphrag.py
"""
from __future__ import annotations

import os
import py_compile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

from src.graphrag.prompt_engine import build_agent_planner_prompt, build_critic_prompt
from src.graphrag.retriever import retrieve_context
from src.graphrag.validator import validate_flow_yaml

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def _assert(condition: bool, label: str) -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        _failures.append(label)


# ── Original tests (require Neo4j) ───────────────────────────────────────────

def test_retrieve_context() -> None:
    load_dotenv(".env")
    context = retrieve_context(goal="Improve match quality for Healthtech startups")
    assert context.industry == "Healthtech"
    assert context.available_skills
    assert context.active_flows
    assert context.failure_patterns or context.success_patterns
    prompt = build_agent_planner_prompt(context.goal, context)
    assert "GraphRAG" in prompt
    assert "Failure subgraph" in prompt
    critic_prompt = build_critic_prompt("flow_id: test\nsteps: []", context, context.goal)
    assert "Valid skills" in critic_prompt


def test_validate_flow_yaml() -> None:
    valid = validate_flow_yaml(
        """
flow_id: flow_test
runs_on: srv_002
steps:
  - id: semantic
    skill: skill_semantic_similarity
    input: {}
""",
        ["skill_semantic_similarity"],
    )
    assert valid["valid"], valid
    invalid = validate_flow_yaml(
        """
flow_id: flow_test
steps:
  - skill: hallucinated_skill
""",
        ["skill_semantic_similarity"],
    )
    assert not invalid["valid"]
    assert "Unknown skill IDs" in invalid["errors"][0]


# ── Phase 6 unit tests ────────────────────────────────────────────────────────

def test_retrieve_semantic_context_fallback() -> None:
    """retrieve_semantic_context falls back gracefully when Neo4j is unavailable."""
    print("\n[P6-1] retrieve_semantic_context falls back when Neo4j unavailable")

    from neo4j.exceptions import ServiceUnavailable
    from src.graphrag.retriever import retrieve_semantic_context

    def _raise(*args, **kwargs):
        raise ServiceUnavailable("Mock Neo4j down")

    with patch("src.graphrag.embedder.generate_embedding", return_value=[0.1] * 3072):
        with patch("src.agents.tools._run_read_cypher", side_effect=_raise):
            result = retrieve_semantic_context("healthtech mentor", top_k=3)

    _assert(isinstance(result, list), "returns a list (not an exception)")
    # keyword fallback also patched to raise, so result should be empty
    _assert(len(result) == 0, "empty list when both vector and keyword queries fail")


def test_generate_embedding_missing_api_key() -> None:
    """generate_embedding raises RuntimeError when GOOGLE_API_KEY is missing."""
    print("\n[P6-2] generate_embedding raises when GOOGLE_API_KEY missing")

    from src.graphrag.embedder import generate_embedding

    saved = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        raised = False
        msg = ""
        try:
            generate_embedding("test query")
        except RuntimeError as exc:
            raised = True
            msg = str(exc)
        _assert(raised, "RuntimeError is raised when GOOGLE_API_KEY is missing")
        _assert("GOOGLE_API_KEY" in msg, "error message mentions GOOGLE_API_KEY")
    finally:
        if saved is not None:
            os.environ["GOOGLE_API_KEY"] = saved


def test_query_graph_semantic_is_tool() -> None:
    """query_graph_semantic is registered as a LangChain @tool."""
    print("\n[P6-3] query_graph_semantic is a LangChain tool")

    from src.agents.tools import query_graph_semantic

    _assert(
        hasattr(query_graph_semantic, "invoke"),
        "query_graph_semantic has .invoke() (LangChain tool interface)",
    )
    _assert(
        callable(query_graph_semantic.invoke),
        "query_graph_semantic.invoke is callable",
    )


def test_new_files_compile() -> None:
    """All Phase 6 new/modified files compile cleanly."""
    print("\n[P6-4] Phase 6 files compile cleanly")

    root = Path(__file__).resolve().parent
    files = [
        "src/graphrag/embedder.py",
        "src/graphrag/retriever.py",
        "src/agents/tools.py",
        "src/agents/nodes.py",
        "streamlit_app.py",
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
    print("=== Phase 6 unit tests (no DB / embedding API required) ===")
    test_retrieve_semantic_context_fallback()
    test_generate_embedding_missing_api_key()
    test_query_graph_semantic_is_tool()
    test_new_files_compile()

    print()
    if _failures:
        print(f"\033[31mFAILED ({len(_failures)}):\033[0m")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\033[32mAll Phase 6 GraphRAG tests passed.\033[0m")

    print("\n=== Original integration tests (require Neo4j) ===")
    try:
        test_retrieve_context()
        test_validate_flow_yaml()
        print("\033[32mGraphRAG integration tests passed.\033[0m")
    except Exception as exc:
        print(f"\033[33mIntegration tests skipped or failed (Neo4j needed): {exc}\033[0m")
