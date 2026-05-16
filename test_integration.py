"""Integration smoke test — verifies all 4 schema fixes without LLM calls."""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from src.agents.tools import (
    get_infrastructure_status,
    log_execution_trace,
    query_graph,
    verify_neo4j_connection,
)

print("=== 0. Neo4j connectivity ===", flush=True)
try:
    verify_neo4j_connection()
except RuntimeError as exc:
    print(f"  FAILED: {exc}", flush=True)
    sys.exit(1)
print("  Neo4j connection OK.", flush=True)

print("\n=== 1. ExecutionTrace history (empty on first run — expected) ===", flush=True)
rows = query_graph.invoke({"cypher_query": (
    "MATCH (et:ExecutionTrace)-[:RAN_FLOW]->(f:Flow), "
    "      (et)-[:RESULTED_IN]->(o:Outcome) "
    "RETURN f.id AS flow_id, round(avg(o.score), 2) AS avg_score, count(et) AS runs "
    "ORDER BY avg_score ASC"
)})
print(f"  rows: {rows}  <- expected [] on cold start, OK if non-empty from prior runs", flush=True)

print("\n=== 2. Active flows with fixed [:USES] relationship ===", flush=True)
flows = query_graph.invoke({"cypher_query": (
    "MATCH (f:Flow {status: 'active'})-[:USES]->(s:Skill) "
    "RETURN f.id AS flow_id, collect(s.name) AS skill_names"
)})
assert flows, "No active flows returned — ingest may not have run"
for f in flows:
    print(f"  {f['flow_id']}: {f['skill_names']}", flush=True)

print("\n=== 3. get_infrastructure_status (load 0-1, error_rate scalar) ===", flush=True)
infra = get_infrastructure_status.invoke({})
assert infra, "No servers returned"
for sid, stats in infra.items():
    assert 0.0 <= stats["load"] <= 1.0, f"load out of range for {sid}: {stats['load']}"
    print(f"  {sid}: load={stats['load']:.2f}, error_rate={stats['error_rate']:.3f}", flush=True)

print("\n=== 4. log_execution_trace: write ExecutionTrace bridge node ===", flush=True)
log_execution_trace("flow_basic_match", result_score=6.5, status="success")
print("  Trace written to Neo4j.", flush=True)

print("\n=== 5. Verify trace is readable by Planner query ===", flush=True)
rows2 = query_graph.invoke({"cypher_query": (
    "MATCH (et:ExecutionTrace)-[:RAN_FLOW]->(f:Flow), "
    "      (et)-[:RESULTED_IN]->(o:Outcome) "
    "RETURN f.id AS flow_id, round(avg(o.score), 2) AS avg_score, count(et) AS runs "
    "ORDER BY avg_score ASC"
)})
assert rows2, "ExecutionTrace not found after write — bridge is broken"
print(f"  rows after write: {rows2}", flush=True)

print("\nAll integration checks passed.", flush=True)
