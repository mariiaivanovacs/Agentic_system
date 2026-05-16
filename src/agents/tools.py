"""
Agent tools: query_graph, simulate_flow, get_infrastructure_status, propose_change.

Constraints enforced here:
  - query_graph is strictly read-only (write Cypher is rejected).
  - simulate_flow auto-generates a JWT capability token that scopes the sandbox.
  - propose_change writes to Neo4j but only creates 'proposed' nodes — it never
    activates or modifies existing active nodes.
  - Neo4j connections are retried up to 3 times before failing.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Dict, List

import jwt
import yaml
from langchain_core.tools import tool
from neo4j import GraphDatabase, Query
from neo4j import exceptions as neo4j_exc
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Neo4j helpers                                                                #
# --------------------------------------------------------------------------- #

def _get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(
            os.environ.get("NEO4J_USERNAME", "neo4j"),
            os.environ["NEO4J_PASSWORD"],
        ),
        connection_timeout=float(os.environ.get("NEO4J_CONNECTION_TIMEOUT_SECONDS", "5")),
        connection_acquisition_timeout=float(
            os.environ.get("NEO4J_CONNECTION_ACQUISITION_TIMEOUT_SECONDS", "5")
        ),
        max_transaction_retry_time=float(
            os.environ.get("NEO4J_MAX_TRANSACTION_RETRY_SECONDS", "10")
        ),
    )


def _db() -> str:
    return os.environ.get("NEO4J_DATABASE", "neo4j")


def _query_timeout() -> float:
    return float(os.environ.get("NEO4J_QUERY_TIMEOUT_SECONDS", "10"))


def verify_neo4j_connection() -> None:
    """Fail fast with a readable error if Neo4j is not reachable."""
    driver = _get_driver()
    try:
        driver.verify_connectivity()
        with driver.session(database=_db()) as session:
            session.run(Query("RETURN 1 AS ok", timeout=_query_timeout())).single()
    except Exception as exc:
        raise RuntimeError(
            "Neo4j connectivity check failed. Verify NEO4J_URI, "
            "NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE, and network access. "
            f"Original error: {exc}"
        ) from exc
    finally:
        driver.close()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(neo4j_exc.ServiceUnavailable),
    reraise=True,
)
def _run_read_cypher(cypher: str) -> List[Dict]:
    driver = _get_driver()
    try:
        with driver.session(database=_db()) as session:
            result = session.run(Query(cypher, timeout=_query_timeout()))
            return [dict(record) for record in result]
    finally:
        driver.close()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(neo4j_exc.ServiceUnavailable),
    reraise=True,
)
def _run_write_cypher(cypher: str, **params) -> List[Dict]:
    driver = _get_driver()
    try:
        with driver.session(database=_db()) as session:
            result = session.run(Query(cypher, timeout=_query_timeout()), **params)
            return [dict(record) for record in result]
    finally:
        driver.close()


# --------------------------------------------------------------------------- #
# Tool 1 — query_graph                                                         #
# --------------------------------------------------------------------------- #

_WRITE_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DETACH", "CALL")


@tool
def query_graph(cypher_query: str) -> List[Dict]:
    """Execute a read-only Cypher query against the Neo4j dual-graph database.

    Use this tool to inspect Graph A (historical performance: Company, Mentor,
    Outcome, ExecutionTrace) and Graph B (system blueprint: Flow, Skill,
    Connector, Server).

    Write operations (CREATE, MERGE, SET, DELETE…) are rejected automatically.

    Args:
        cypher_query: A valid read-only Cypher MATCH query.

    Returns:
        A list of dicts — one per result record.

    Example:
        MATCH (f:Flow {status: 'active'})-[:USES_SKILL]->(s:Skill)
        RETURN f.id AS flow_id, collect(s.id) AS skills
    """
    upper = cypher_query.strip().upper()
    for kw in _WRITE_KEYWORDS:
        if kw in upper:
            raise ValueError(
                f"Write operation '{kw}' is not permitted via query_graph. "
                "Use propose_change for approved writes."
            )
    try:
        return _run_read_cypher(cypher_query)
    except neo4j_exc.ServiceUnavailable as exc:
        logger.error("Neo4j unavailable after 3 retries: %s", exc)
        raise


# --------------------------------------------------------------------------- #
# Tool 2 — simulate_flow                                                       #
# --------------------------------------------------------------------------- #

def _capability_token(flow_id: str) -> str:
    secret = os.environ.get("CAPABILITY_TOKEN_SECRET", "dev-secret-do-not-use")
    payload = {
        "flow_id": flow_id,
        "allowed_connectors": ["sql_connector_v1", "csv_connector_v1", "json_connector_v1"],
        "allowed_skills": [
            "filter_by_industry_exact",
            "random_shuffle",
            "semantic_similarity",
            "fuzzy_industry_match",
        ],
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _mock_sandbox(flow_yaml: str) -> Dict:
    """Deterministic local simulation based on skills present in the YAML.

    Searches the raw text for known skill names so it works regardless of the
    exact nesting structure the LLM chooses to generate.
    """
    try:
        yaml.safe_load(flow_yaml)  # syntax check only
    except yaml.YAMLError as exc:
        return {"status": "fail", "metrics": {}, "error_log": f"YAML parse error: {exc}"}

    text = flow_yaml.lower()

    if "semantic_similarity" in text or "fuzzy_industry_match" in text:
        return {
            "status": "success",
            "metrics": {"latency_ms": 245, "match_score": 8.7, "sample_size": 20},
            "error_log": None,
        }
    if "random_shuffle" in text:
        return {
            "status": "success",
            "metrics": {"latency_ms": 80, "match_score": 2.8, "sample_size": 20},
            "error_log": None,
        }
    return {
        "status": "fail",
        "metrics": {},
        "error_log": "Unrecognised skill combination — sandbox cannot assess this flow.",
    }


def _cloud_run_sandbox(flow_yaml: str, dataset_snapshot_id: str, token: str) -> Dict:
    from google.cloud import run_v2

    client = run_v2.JobsClient()
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    region = os.environ.get("SANDBOX_GCP_REGION", "us-central1")
    job = os.environ["SANDBOX_JOB_NAME"]

    request = run_v2.RunJobRequest(
        name=f"projects/{project}/locations/{region}/jobs/{job}",
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="FLOW_YAML", value=flow_yaml),
                        run_v2.EnvVar(name="DATASET_SNAPSHOT_ID", value=dataset_snapshot_id),
                        run_v2.EnvVar(name="CAPABILITY_TOKEN", value=token),
                    ]
                )
            ]
        ),
    )
    try:
        operation = client.run_job(request=request)
        result = operation.result(timeout=300)
        return {
            "status": "success",
            "metrics": {"gcp_execution_id": result.name},
            "error_log": None,
        }
    except TimeoutError:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": "Timeout: sandbox execution exceeded 300 seconds.",
        }


@tool
def simulate_flow(flow_yaml: str, dataset_snapshot_id: str) -> Dict:
    """Send a proposed flow YAML to the Secure Sandbox and retrieve performance metrics.

    A JWT capability token is generated automatically to restrict which
    connectors and skills the sandbox is allowed to invoke.

    Set SANDBOX_MOCK=true in .env to run a deterministic local simulation
    without needing Google Cloud Run.

    Args:
        flow_yaml: Full YAML text of the proposed flow definition.
        dataset_snapshot_id: ID of the historical dataset snapshot to test against.

    Returns:
        Dict with:
          status      — 'success' or 'fail'
          metrics     — dict with latency_ms, match_score, sample_size (on success)
          error_log   — error string or None
    """
    try:
        flow_def = yaml.safe_load(flow_yaml)
    except yaml.YAMLError as exc:
        return {"status": "fail", "metrics": {}, "error_log": f"Invalid YAML: {exc}"}

    flow_id = flow_def.get("flow_id", f"flow_{uuid.uuid4().hex[:8]}")
    token = _capability_token(flow_id)

    use_mock = os.environ.get("SANDBOX_MOCK", "true").lower() == "true"
    if use_mock:
        return _mock_sandbox(flow_yaml)
    return _cloud_run_sandbox(flow_yaml, dataset_snapshot_id, token)


# --------------------------------------------------------------------------- #
# Tool 3 — get_infrastructure_status                                           #
# --------------------------------------------------------------------------- #

@tool
def get_infrastructure_status() -> Dict:
    """Return current load and error rate for all servers in Graph B.

    Use this before proposing a new flow to verify the target server has
    capacity (load < 0.80) and acceptable reliability (error_rate < 0.03).

    Returns:
        Dict mapping server_id → {'load': float, 'error_rate': float}.
        load is normalised to 0–1 (current_load / 100).
        error_rate is the most recent value from error_rate_history.
        Returns an empty dict if Neo4j is temporarily unavailable.
    """
    try:
        records = _run_read_cypher(
            "MATCH (s:Server) "
            "RETURN s.id AS id, "
            "       s.current_load / 100.0 AS load, "
            "       last(s.error_rate_history) AS error_rate"
        )
        return {r["id"]: {"load": r["load"], "error_rate": r["error_rate"]} for r in records}
    except neo4j_exc.ServiceUnavailable:
        logger.warning("Neo4j unavailable when checking infrastructure; returning empty status.")
        return {}


# --------------------------------------------------------------------------- #
# Tool 4 — propose_change                                                      #
# --------------------------------------------------------------------------- #

@tool
def propose_change(change_type: str, details: Dict) -> str:
    """Persist a proposed change to Neo4j as a node with status='proposed'.

    This does NOT activate the change. The node waits for human approval via
    the HumanApproval node before becoming 'active'.

    Args:
        change_type: One of 'new_flow', 'update_connector', 'deprecate_skill'.
        details: Dict containing the YAML or JSON content of the proposed change.

    Returns:
        The ID of the created proposal node (e.g. 'newflow_proposal_8a3f1c2b').

    Raises:
        ValueError: if change_type is not one of the allowed values.
    """
    allowed = {"new_flow", "update_connector", "deprecate_skill"}
    if change_type not in allowed:
        raise ValueError(f"change_type must be one of {allowed}, got '{change_type}'")

    proposal_id = f"{change_type.replace('_', '')}_proposal_{uuid.uuid4().hex[:8]}"
    details_json = json.dumps(details)

    _run_write_cypher(
        "CREATE (:Flow {id: $id, status: 'proposed', yaml_config: $details})",
        id=proposal_id,
        details=details_json,
    )
    logger.info("Proposal %s created in Neo4j.", proposal_id)
    return proposal_id


# --------------------------------------------------------------------------- #
# Internal helpers — graph write operations, not exposed as agent tools        #
# --------------------------------------------------------------------------- #

def log_execution_trace(
    flow_id: str,
    result_score: float,
    status: str = "completed",
) -> None:
    """Create an ExecutionTrace bridge node linking a Flow (Graph B) to an Outcome (Graph A).

    Called by simulator_node after every sandbox run so the Planner can learn
    from accumulated simulation history on subsequent invocations.
    """
    trace_id = f"trace_{uuid.uuid4().hex[:8]}"
    _run_write_cypher(
        """
        MATCH (f:Flow {id: $flow_id})
        CREATE (et:ExecutionTrace {
            id:        $trace_id,
            status:    $status,
            timestamp: datetime()
        })
        CREATE (o:Outcome {score: $result_score, date: date()})
        CREATE (et)-[:RAN_FLOW]->(f)
        CREATE (et)-[:RESULTED_IN]->(o)
        """,
        flow_id=flow_id,
        trace_id=trace_id,
        status=status,
        result_score=result_score,
    )
    logger.info("ExecutionTrace %s logged for flow %s (score=%.2f).", trace_id, flow_id, result_score)


def activate_proposal(proposal_id: str) -> None:
    """Mark a previously proposed Flow node as 'active'. Called by HumanApproval."""
    _run_write_cypher(
        "MATCH (f:Flow {id: $id}) SET f.status = 'active'",
        id=proposal_id,
    )
    logger.info("Proposal %s activated.", proposal_id)


def reject_proposal(proposal_id: str, reason: str) -> None:
    """Mark proposal as 'rejected' and store the rejection reason."""
    _run_write_cypher(
        "MATCH (f:Flow {id: $id}) SET f.status = 'rejected', f.rejection_reason = $reason",
        id=proposal_id,
        reason=reason,
    )
    logger.info("Proposal %s rejected: %s", proposal_id, reason)
