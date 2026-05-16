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
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

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
# Data isolation — secret sanitisation                                         #
# --------------------------------------------------------------------------- #

# Keys whose values must never appear in a sandbox snapshot.
_SECRET_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "auth_key", "authkey", "auth_token", "authtoken", "credential",
    "credentials", "private_key", "privatekey", "access_key",
    "accesskey", "bearer", "encryption_key", "signing_key",
})

# Substrings that flag a key as sensitive even if not in the set above.
_SECRET_SUBSTRINGS: tuple[str, ...] = (
    "secret", "password", "passwd", "token", "credential", "private",
)


def _is_secret_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return k in _SECRET_KEYS or any(sub in k for sub in _SECRET_SUBSTRINGS)


def _sanitize_snapshot(data: object) -> object:
    """Recursively strip secret-looking keys from a snapshot dict/list.

    Called on every snapshot before it is passed to the sandbox so that
    passwords, tokens, and API keys can never leak into the execution environment.
    """
    if isinstance(data, dict):
        return {
            k: _sanitize_snapshot(v)
            for k, v in data.items()
            if not _is_secret_key(k)
        }
    if isinstance(data, list):
        return [_sanitize_snapshot(item) for item in data]
    return data


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
def _run_read_cypher(cypher: str, params: Optional[Dict] = None) -> List[Dict]:
    driver = _get_driver()
    try:
        with driver.session(database=_db()) as session:
            result = session.run(Query(cypher, timeout=_query_timeout()), params or {})
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
_WRITE_KEYWORD_RE = re.compile(r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b", re.IGNORECASE)


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
    match = _WRITE_KEYWORD_RE.search(cypher_query)
    if match:
        raise ValueError(
            f"Write operation '{match.group(1).upper()}' is not permitted via query_graph. "
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


def _build_snapshot(
    industry: Optional[str] = None,
    app_id: Optional[str] = None,
) -> dict:
    """Query Neo4j Graph A for companies and mentors to build a sandbox snapshot.

    Isolation guarantees:
    - If *app_id* is provided, only Company nodes whose app_id matches are
      included. Falls back to the full graph if that query returns no results
      (the seeded EcoLink data pre-dates per-app scoping).
    - The returned dict is always passed through _sanitize_snapshot() so that
      secret-looking fields are stripped before the snapshot reaches the sandbox.
    - Falls back to two-row sample data if Neo4j is unavailable.
    """
    _SAMPLE: dict = {
        "companies": [
            {"id": "C-01", "name": "Nexus AI", "industry": "Fintech"},
            {"id": "C-02", "name": "Etech Finance", "industry": "Fintech"},
        ],
        "mentors": [
            {"id": "M-99", "name": "Dr. Kuan Studio", "expertise": ["Finance", "Scaling"]},
            {"id": "M-88", "name": "Darveen Ventures", "expertise": ["Marketing", "Product"]},
        ],
    }
    try:
        # Build company query — try app_id-scoped first, fall back to unscoped
        if app_id:
            company_rows = _run_read_cypher(
                "MATCH (c:Company) WHERE c.app_id = $app_id "
                "RETURN c.id AS id, c.name AS name, c.industry AS industry "
                "LIMIT 15",
                {"app_id": app_id},
            )
            if not company_rows:
                logger.info(
                    "No Company nodes found for app_id=%s; using full graph.", app_id
                )
                company_rows = _run_read_cypher(
                    "MATCH (c:Company) WHERE c.industry = $industry "
                    "RETURN c.id AS id, c.name AS name, c.industry AS industry LIMIT 15"
                    if industry
                    else "MATCH (c:Company) RETURN c.id AS id, c.name AS name, c.industry AS industry LIMIT 15",
                    {"industry": industry} if industry else {},
                )
        elif industry:
            company_rows = _run_read_cypher(
                "MATCH (c:Company) WHERE c.industry = $industry "
                "RETURN c.id AS id, c.name AS name, c.industry AS industry "
                "LIMIT 15",
                {"industry": industry},
            )
        else:
            company_rows = _run_read_cypher(
                "MATCH (c:Company) "
                "RETURN c.id AS id, c.name AS name, c.industry AS industry "
                "LIMIT 15"
            )

        mentor_rows = _run_read_cypher(
            "MATCH (m:Mentor) "
            "RETURN m.id AS id, m.name AS name, m.expertise_tags AS expertise "
            "LIMIT 10"
        )

        companies = [
            {"id": r["id"], "name": r["name"], "industry": r.get("industry", "")}
            for r in company_rows
        ]
        mentors = [
            {"id": r["id"], "name": r["name"], "expertise": r.get("expertise") or []}
            for r in mentor_rows
        ]

        if not companies or not mentors:
            logger.warning("Empty snapshot from Neo4j — falling back to sample data.")
            return _sanitize_snapshot(_SAMPLE)  # type: ignore[return-value]

        snapshot = {"companies": companies, "mentors": mentors}
        if app_id:
            snapshot["_meta"] = {"app_id": app_id, "scoped": True}
        return _sanitize_snapshot(snapshot)  # type: ignore[return-value]

    except Exception as exc:
        logger.warning("Could not build snapshot from Neo4j (%s); using sample data.", exc)
        return _sanitize_snapshot(_SAMPLE)  # type: ignore[return-value]


def _parse_sandbox_output(stdout: str) -> Optional[List[dict]]:
    """Extract the JSON trace list from between DATA_STREAM_START/END markers."""
    if "DATA_STREAM_START" not in stdout or "DATA_STREAM_END" not in stdout:
        return None
    start = stdout.index("DATA_STREAM_START") + len("DATA_STREAM_START")
    end = stdout.index("DATA_STREAM_END")
    try:
        return json.loads(stdout[start:end].strip())
    except json.JSONDecodeError:
        return None


def _traces_to_metrics(traces: List[dict], latency_ms: int) -> Dict:
    scores = [
        t.get("simulated_outcome_score", 0.0)
        for t in traces
        if t.get("status") == "SIMULATION_SUCCESS"
    ]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    return {"match_score": avg_score, "latency_ms": latency_ms, "sample_size": len(scores)}


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


def _local_sandbox(flow_yaml: str, snapshot: dict) -> Dict:
    """Run sandbox_task.py as a local subprocess.

    This is the default non-mock mode: real simulation logic from sandbox-system/,
    real Neo4j snapshot data, no GCP required. Uses the DATA_STREAM_START/END
    protocol defined in sandbox_task.py to extract results.
    """
    sandbox_script = (
        Path(__file__).resolve().parent.parent.parent
        / "sandbox-system"
        / "sandbox_task.py"
    )
    if not sandbox_script.exists():
        return {
            "status": "fail",
            "metrics": {},
            "error_log": f"sandbox_task.py not found at {sandbox_script}",
        }

    try:
        flow_def = yaml.safe_load(flow_yaml) or {}
        if not isinstance(flow_def, dict):
            flow_def = {}
        # Handle top-level nested YAML (e.g. {flow_proposal_v2: {steps: ...}})
        if "flow_id" not in flow_def and len(flow_def) == 1:
            key = next(iter(flow_def))
            if isinstance(flow_def[key], dict):
                flow_name = str(key)
            else:
                flow_name = str(key)
        else:
            flow_name = flow_def.get("flow_id", "proposed_flow")
    except yaml.YAMLError:
        flow_name = "proposed_flow"

    env = os.environ.copy()
    env["SNAPSHOT_DATA"] = json.dumps(snapshot)
    env["PROPOSED_FLOW"] = str(flow_name)

    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(sandbox_script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": "Sandbox subprocess timed out after 60 seconds.",
        }

    latency_ms = round((time.time() - t0) * 1000)
    logger.debug("Sandbox stdout:\n%s", proc.stdout)

    if proc.returncode != 0:
        logger.error("Sandbox stderr:\n%s", proc.stderr)
        return {
            "status": "fail",
            "metrics": {},
            "error_log": proc.stderr or f"Sandbox exited with code {proc.returncode}",
        }

    traces = _parse_sandbox_output(proc.stdout)
    if traces is None:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": (
                f"No DATA_STREAM_START/END markers in sandbox output. "
                f"stdout: {proc.stdout[:400]}"
            ),
        }

    return {
        "status": "success",
        "metrics": _traces_to_metrics(traces, latency_ms),
        "error_log": None,
        "traces": traces,
    }


def _poll_cloud_logging_for_traces(
    project: str, region: str, job: str, execution_id: str
) -> Optional[List[dict]]:
    """Poll Cloud Logging until DATA_STREAM_START/END markers appear in sandbox stdout.

    Returns the parsed trace list on success, or None if the markers are not
    found within the timeout window.
    """
    try:
        from google.cloud import logging as gcp_logging  # noqa: PLC0415
    except ImportError:
        logger.error(
            "google-cloud-logging is not installed. Run: pip install google-cloud-logging"
        )
        return None

    log_client = gcp_logging.Client(project=project)
    log_filter = (
        f'resource.type="cloud_run_job" '
        f'resource.labels.job_name="{job}" '
        f'resource.labels.location="{region}" '
        f'logName="projects/{project}/logs/run.googleapis.com%2Fstdout" '
        f'labels."run.googleapis.com/execution-name"="{execution_id}"'
    )

    _MAX_WAIT_S = 60
    _POLL_S = 5
    deadline = time.time() + _MAX_WAIT_S

    logger.info(
        "Polling Cloud Logging for execution %s (up to %ds).",
        execution_id,
        _MAX_WAIT_S,
    )

    while time.time() < deadline:
        entries = list(
            log_client.list_entries(filter_=log_filter, order_by="timestamp asc")
        )
        parts: list[str] = []
        for entry in entries:
            payload = entry.payload
            if isinstance(payload, str):
                parts.append(payload)
            elif isinstance(payload, dict):
                parts.append(json.dumps(payload))
            else:
                try:
                    parts.append(str(payload))
                except Exception:
                    pass
        combined = "\n".join(parts)

        if "DATA_STREAM_START" in combined and "DATA_STREAM_END" in combined:
            traces = _parse_sandbox_output(combined)
            if traces is not None:
                logger.info(
                    "Parsed %d traces from Cloud Logging for %s.", len(traces), execution_id
                )
                return traces

        time.sleep(_POLL_S)

    logger.warning(
        "No DATA_STREAM_START/END found in Cloud Logging for %s after %ds.",
        execution_id,
        _MAX_WAIT_S,
    )
    return None


def _classify_cloud_error(exc: Exception) -> dict:
    """Parse a GCP exception into a structured infra-error dict."""
    msg = str(exc)
    # SERVICE_DISABLED — API not enabled on this project
    if "SERVICE_DISABLED" in msg or "has not been used in project" in msg:
        import re
        url_match = re.search(r"https://console\.developers\.google\.com[^\s\"'>\]]+", msg)
        activation_url = url_match.group(0).rstrip(").,") if url_match else ""
        svc_match = re.search(r'value: "([^"]+\.googleapis\.com)"', msg)
        service = svc_match.group(1) if svc_match else "run.googleapis.com"
        return {
            "error_type": "CLOUD_API_DISABLED",
            "service": service,
            "activation_url": activation_url,
            "human_action": f"Enable the '{service}' API in GCP console, then retry.",
            "raw": msg[:600],
        }
    # PERMISSION_DENIED
    if "403" in msg or "PERMISSION_DENIED" in msg:
        return {
            "error_type": "CLOUD_PERMISSION_DENIED",
            "service": "run.googleapis.com",
            "activation_url": "",
            "human_action": "Check GCP IAM permissions for the service account.",
            "raw": msg[:600],
        }
    # Generic cloud error
    return {
        "error_type": "CLOUD_ERROR",
        "service": "",
        "activation_url": "",
        "human_action": "Check GCP project configuration and credentials.",
        "raw": msg[:600],
    }


def _cloud_run_sandbox(flow_yaml: str, snapshot: dict, token: str) -> Dict:
    """Trigger sandbox_task.py via a Google Cloud Run Job, then poll Cloud Logging
    for the DATA_STREAM_START/END output written by sandbox_task.py to extract
    real metrics.

    The Cloud Run job must already exist (see sandbox-system/Dockerfile and
    SandboxExecutor.py for setup instructions).

    Required env vars: GOOGLE_CLOUD_PROJECT, SANDBOX_JOB_NAME.
    Optional: SANDBOX_GCP_REGION (default: us-central1).
    """
    from google.cloud import run_v2  # noqa: PLC0415

    try:
        flow_def = yaml.safe_load(flow_yaml) or {}
        flow_name = (
            flow_def.get("flow_id", "proposed_flow")
            if isinstance(flow_def, dict)
            else "proposed_flow"
        )
    except yaml.YAMLError:
        flow_name = "proposed_flow"

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": "GOOGLE_CLOUD_PROJECT env var is not set.",
        }
    job = os.environ.get("SANDBOX_JOB_NAME")
    if not job:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": "SANDBOX_JOB_NAME env var is not set.",
        }
    region = os.environ.get("SANDBOX_GCP_REGION", "us-central1")

    client = run_v2.JobsClient()
    request = run_v2.RunJobRequest(
        name=f"projects/{project}/locations/{region}/jobs/{job}",
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="SNAPSHOT_DATA", value=json.dumps(snapshot)),
                        run_v2.EnvVar(name="PROPOSED_FLOW", value=flow_name),
                        run_v2.EnvVar(name="CAPABILITY_TOKEN", value=token),
                    ]
                )
            ]
        ),
    )

    t0 = time.time()
    try:
        operation = client.run_job(request=request)
        execution = operation.result(timeout=300)
    except Exception as exc:
        classified = _classify_cloud_error(exc)
        logger.error(
            "Cloud Run sandbox failed [%s]: %s",
            classified["error_type"],
            classified["raw"],
        )
        return {
            "status": "fail",
            "metrics": {},
            "error_log": classified["human_action"],
            "infra_error": classified,
        }

    latency_ms = round((time.time() - t0) * 1000)
    # execution.name format: projects/{p}/locations/{r}/jobs/{j}/executions/{id}
    execution_id = execution.name.split("/")[-1]
    logger.info("Cloud Run execution %s completed in %dms.", execution_id, latency_ms)

    traces = _poll_cloud_logging_for_traces(project, region, job, execution_id)
    if traces is None:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": (
                f"Cloud Run execution '{execution_id}' finished but no DATA_STREAM_START/END "
                "markers were found in Cloud Logging within 60s. "
                "Verify sandbox_task.py ran and that google-cloud-logging is installed."
            ),
        }

    return {
        "status": "success",
        "metrics": _traces_to_metrics(traces, latency_ms),
        "error_log": None,
        "traces": traces,
    }


@tool
def simulate_flow(flow_yaml: str, dataset_snapshot_id: str) -> Dict:
    """Send a proposed flow YAML to the Secure Sandbox and retrieve performance metrics.

    Sandbox mode is controlled by two env vars:
      SANDBOX_MOCK=true   → deterministic local mock (default, no dependencies)
      SANDBOX_MOCK=false  → real sandbox using sandbox_task.py
      SANDBOX_MODE=local     → run sandbox_task.py as a subprocess (no GCP)
      SANDBOX_MODE=cloudrun  → trigger sandbox_task.py via Cloud Run Job (needs GCP)

    A JWT capability token is always generated to scope allowed connectors/skills.
    The snapshot (companies + mentors) is built from live Neo4j data.

    Args:
        flow_yaml: Full YAML text of the proposed flow definition.
        dataset_snapshot_id: Hint for snapshot selection (industry or snapshot ID).

    Returns:
        Dict with:
          status      — 'success' or 'fail'
          metrics     — dict with match_score, latency_ms, sample_size (on success)
          error_log   — error string or None
          traces      — list of per-company simulation traces (local/cloudrun modes)
    """
    try:
        flow_def = yaml.safe_load(flow_yaml) or {}
    except yaml.YAMLError as exc:
        return {"status": "fail", "metrics": {}, "error_log": f"Invalid YAML: {exc}"}

    if not isinstance(flow_def, dict):
        flow_def = {}

    flow_id = flow_def.get("flow_id", f"flow_{uuid.uuid4().hex[:8]}")
    token = _capability_token(flow_id)

    use_mock = os.environ.get("SANDBOX_MOCK", "true").lower() == "true"
    if use_mock:
        logger.info("Sandbox running in MOCK mode.")
        return _mock_sandbox(flow_yaml)

    sandbox_mode = os.environ.get("SANDBOX_MODE", "local").lower()
    # Pass app_id so _build_snapshot can scope the snapshot to the right app.
    # dataset_snapshot_id is the caller-supplied hint; treat it as an industry
    # tag or app_id depending on format (domain-like strings → app_id).
    _snap_app_id = (
        dataset_snapshot_id
        if dataset_snapshot_id and "." in dataset_snapshot_id
        else None
    )
    snapshot = _build_snapshot(app_id=_snap_app_id)
    logger.info(
        "Sandbox snapshot (isolation: app_id=%s): %d companies, %d mentors — mode: %s",
        _snap_app_id or "none",
        len(snapshot.get("companies", [])),
        len(snapshot.get("mentors", [])),
        sandbox_mode,
    )

    if sandbox_mode == "cloudrun":
        return _cloud_run_sandbox(flow_yaml, snapshot, token)
    return _local_sandbox(flow_yaml, snapshot)


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


def approve_skill_proposal(skill_id: str) -> None:
    """Mark a SkillProposal as approved. Called from Streamlit admin page."""
    _run_write_cypher(
        "MATCH (s:SkillProposal {id: $id}) SET s.status = 'approved'",
        id=skill_id,
    )
    logger.info("SkillProposal %s approved.", skill_id)


def reject_skill_proposal(skill_id: str, reason: str = "") -> None:
    """Mark a SkillProposal as rejected. Called from Streamlit admin page."""
    _run_write_cypher(
        "MATCH (s:SkillProposal {id: $id}) SET s.status = 'rejected', s.rejection_reason = $reason",
        id=skill_id,
        reason=reason,
    )
    logger.info("SkillProposal %s rejected: %s", skill_id, reason)


# --------------------------------------------------------------------------- #
# Skill Modification Tools                                                     #
# --------------------------------------------------------------------------- #

@tool
def propose_skill_update(
    skill_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    performance_score: Optional[float] = None,
    avg_execution_ms: Optional[float] = None,
    language: Optional[str] = None,
    reason: str = "Performance tuning",
) -> str:
    """Propose modifications to an existing Skill node.
    
    Used by the agent to suggest updates to skill properties based on:
    - Performance metrics from simulation results
    - Updated descriptions based on learned patterns
    - Language or execution time optimizations
    
    The proposal waits for human approval via the admin interface before
    being applied to the actual Skill node.
    
    Args:
        skill_id: ID of the existing Skill to modify.
        name: New skill name (optional).
        description: New skill description (optional).
        performance_score: Updated performance score 0–10 (optional).
        avg_execution_ms: Updated average execution time (optional).
        language: Programming language or runtime (optional).
        reason: Reason for the modification (e.g. "Better performance observed").
    
    Returns:
        A string describing the created proposal ID.
    
    Raises:
        ValueError: if all update fields are None.
    """
    # Validate that at least one field is provided
    if all(v is None for v in [name, description, performance_score, avg_execution_ms, language]):
        raise ValueError(
            "At least one of name, description, performance_score, "
            "avg_execution_ms, or language must be provided"
        )
    
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ecolink-graph"))
    import queries as graph_queries  # noqa: PLC0415
    
    result = graph_queries.create_skill_modification_proposal(
        skill_id=skill_id,
        name=name,
        description=description,
        performance_score=performance_score,
        avg_execution_ms=avg_execution_ms,
        language=language,
        reason=reason,
        proposed_by="agent",
    )
    
    proposal_id = result.get("modification_proposal_id", skill_id)
    logger.info(
        "SkillModificationProposal created for %s: %s "
        "(name=%s, score=%s, time_ms=%s, reason=%s)",
        skill_id,
        proposal_id,
        name or "unchanged",
        performance_score or "unchanged",
        avg_execution_ms or "unchanged",
        reason,
    )
    return f"Skill modification proposal created (id={proposal_id}, reason={reason})"


@tool
def get_skill_modification_proposals(status: Optional[str] = None) -> List[Dict]:
    """Query all SkillModificationProposal nodes, optionally filtered by status.
    
    Used by the Critic or Evaluator to inspect proposed modifications
    before approval.
    
    Args:
        status: Filter by status ('proposed', 'approved', 'rejected'). 
                If None, returns all proposals.
    
    Returns:
        List of dicts with id, reason, status, proposed_by, created_at,
        and proposed_* fields (proposed_name, proposed_description, etc.).
    """
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ecolink-graph"))
    import queries as graph_queries  # noqa: PLC0415
    
    proposals = graph_queries.get_skill_modification_proposals(status=status)
    logger.info("Retrieved %d skill modification proposals (status=%s)", len(proposals), status or "any")
    return proposals


def approve_skill_modification(skill_id: str) -> None:
    """Apply a SkillModificationProposal to the actual Skill and mark as 'approved'.
    
    Called from Streamlit admin page or programmatically after validation.
    """
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ecolink-graph"))
    import queries as graph_queries  # noqa: PLC0415
    
    result = graph_queries.approve_skill_modification(skill_id=skill_id)
    logger.info("SkillModificationProposal %s approved and applied: %s", skill_id, result)


def reject_skill_modification(skill_id: str, reason: str = "") -> None:
    """Reject a SkillModificationProposal. Called from Streamlit admin page."""
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ecolink-graph"))
    import queries as graph_queries  # noqa: PLC0415
    
    result = graph_queries.reject_skill_modification(skill_id=skill_id, reason=reason)
    logger.info("SkillModificationProposal %s rejected: %s", skill_id, result)


@tool
def query_graph_semantic(query_text: str, top_k: int = 5) -> List[Dict]:
    """Semantic vector search over the graph.

    Returns nodes whose description is semantically similar to query_text.
    Falls back to keyword CONTAINS search if no vector index exists.

    Args:
        query_text: Natural-language description of what to find.
        top_k: Number of top results to return.

    Returns:
        List of dicts with id, name, description, label, score.
    """
    from src.graphrag.retriever import retrieve_semantic_context  # noqa: PLC0415
    return retrieve_semantic_context(query_text, top_k=top_k)
