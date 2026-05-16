"""
test_pipeline.py — unit tests for pipeline discovery logic.

Tests run against the pure-Python matching functions only (no Neo4j required).
Run with: python test_pipeline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.indexer.pipeline_builder import _tokens, _names_overlap, _slug, discover_pipelines

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def _assert(condition: bool, label: str) -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        _failures.append(label)


# --------------------------------------------------------------------------- #
# _tokens                                                                       #
# --------------------------------------------------------------------------- #

print("\n── _tokens ──────────────────────────────────────────────")
_assert(_tokens("/create-campaign") == {"create", "campaign"}, "splits hyphen-path into tokens")
_assert(_tokens("/") == set(), "root path yields empty token set")
_assert(_tokens("Campaign Funding") == {"campaign", "funding"}, "space-separated feature name")
_assert(_tokens("donate") == {"donate"}, "single short-enough word")
_assert("id" not in _tokens("/campaign/:id"), "drops tokens shorter than 3 chars")
_assert("campaign" in _tokens("/campaign/:id"), "keeps long tokens from param paths")


# --------------------------------------------------------------------------- #
# _names_overlap                                                                #
# --------------------------------------------------------------------------- #

print("\n── _names_overlap ───────────────────────────────────────")
_assert(_names_overlap("/create-campaign", "create-campaign"), "exact route↔method match")
_assert(_names_overlap("/campaign/:id", "Campaign Funding"), "route↔feature match via 'campaign'")
_assert(_names_overlap("/donate", "donate"), "single token match")
_assert(not _names_overlap("/", "Campaign Funding"), "root route does NOT match feature")
_assert(not _names_overlap("/home", "donate"), "unrelated names do not overlap")
_assert(_names_overlap("/campaign/create", "create-campaign"), "shared tokens across compound names")


# --------------------------------------------------------------------------- #
# _slug                                                                         #
# --------------------------------------------------------------------------- #

print("\n── _slug ────────────────────────────────────────────────")
_assert(_slug("/create-campaign") == "create_campaign", "path slug replaces non-alphanum with _")
_assert(_slug("/") == "root", "root path falls back to 'root'")
_assert(_slug("Campaign Funding") == "campaign_funding", "space becomes underscore")


# --------------------------------------------------------------------------- #
# Pipeline assembly (pure Python, no DB)                                        #
# --------------------------------------------------------------------------- #

print("\n── pipeline assembly (mock data) ────────────────────────")

# Simulate what discover_pipelines() does internally after fetching from Neo4j
from src.indexer.pipeline_builder import _tokens, _names_overlap, _slug

mock_routes = [
    {"id": "r1", "name": "/create-campaign"},
    {"id": "r2", "name": "/campaign/:id"},
    {"id": "r3", "name": "/"},
]
mock_features = [
    {"id": "f1", "name": "Campaign Funding", "description": "Crowdfunding UI"},
]
mock_methods = [
    {"id": "m1", "name": "create-campaign", "category": "public"},
    {"id": "m2", "name": "donate",          "category": "public"},
]

def _assemble(routes, features, methods):
    pipelines = []
    for route in routes:
        rname = route["name"]
        mf = [f for f in features if _names_overlap(rname, f["name"])]
        mm = [m for m in methods if _names_overlap(rname, m["name"])]
        if not mf and not mm:
            continue
        steps = [{"step": 1, "type": "Route", "name": rname}]
        for f in mf:
            steps.append({"step": len(steps)+1, "type": "Feature", "name": f["name"]})
        for m in mm:
            steps.append({"step": len(steps)+1, "type": "ContractMethod", "name": m["name"]})
        pipelines.append({
            "id": f"pipeline_test_{_slug(rname)}",
            "name": rname,
            "entrypoint": rname,
            "steps": steps,
            "has_contract": any(s["type"] == "ContractMethod" for s in steps),
        })
    return pipelines

result = _assemble(mock_routes, mock_features, mock_methods)
ids = [p["id"] for p in result]
_assert("pipeline_test_create_campaign" in ids, "/create-campaign route becomes a pipeline")
_assert("pipeline_test_campaign_id" in ids, "/campaign/:id route becomes a pipeline")
_assert("pipeline_test_root" not in ids, "root '/' route excluded (no matches)")

p_create = next(p for p in result if "create_campaign" in p["id"])
types = {s["type"] for s in p_create["steps"]}
_assert("Route" in types, "create-campaign pipeline has Route step")
_assert("ContractMethod" in types, "create-campaign pipeline has ContractMethod step")
_assert(p_create["has_contract"], "create-campaign pipeline flagged as has_contract=True")

p_campaign = next(p for p in result if "campaign_id" in p["id"])
_assert(any(s["type"] == "Feature" for s in p_campaign["steps"]),
        "/campaign/:id pipeline has Feature step (via 'campaign' token)")


# --------------------------------------------------------------------------- #
# Import smoke test                                                              #
# --------------------------------------------------------------------------- #

print("\n── import smoke tests ───────────────────────────────────")
try:
    import src.indexer.pipeline_builder  # noqa: F401
    _assert(True, "pipeline_builder imports cleanly")
except Exception as exc:
    _assert(False, f"pipeline_builder import failed: {exc}")

try:
    import src.indexer.web_indexer  # noqa: F401
    _assert(True, "web_indexer imports cleanly")
except Exception as exc:
    _assert(False, f"web_indexer import failed: {exc}")

try:
    import py_compile
    py_compile.compile("streamlit_app.py", doraise=True)
    _assert(True, "streamlit_app compiles cleanly")
except Exception as exc:
    _assert(False, f"streamlit_app compile failed: {exc}")


# --------------------------------------------------------------------------- #
# Summary                                                                       #
# --------------------------------------------------------------------------- #

print()
if _failures:
    print(f"FAILED — {len(_failures)} assertion(s):")
    for f in _failures:
        print(f"  • {f}")
    sys.exit(1)
else:
    print("All pipeline tests passed.")
