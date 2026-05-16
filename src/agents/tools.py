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
import shutil
import subprocess
import sys
import tempfile
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

from src.agents.flow_utils import _extract_flow_references, _normalise_flow_def

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
    """Recursively strip secret-looking keys from a snapshot dict/list."""
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
def _run_write_cypher(cypher: str, params: Optional[Dict] = None) -> List[Dict]:
    """Execute a write Cypher query. Params are passed as a dict (matching _run_read_cypher)."""
    driver = _get_driver()
    try:
        with driver.session(database=_db()) as session:
            result = session.run(Query(cypher, timeout=_query_timeout()), params or {})
            return [dict(record) for record in result]
    finally:
        driver.close()


# --------------------------------------------------------------------------- #
# Tool 1 — query_graph                                                         #
# --------------------------------------------------------------------------- #

_WRITE_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DETACH", "CALL")
_WRITE_KEYWORD_RE = re.compile(r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b", re.IGNORECASE)
_CYPHER_STRING_RE = re.compile(r"'([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\"")


def _cypher_without_literals(cypher_query: str) -> str:
    """Remove string literals before checking for write keywords."""
    without_strings = _CYPHER_STRING_RE.sub("''", cypher_query)
    return re.sub(r"//.*", "", without_strings)


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
    match = _WRITE_KEYWORD_RE.search(_cypher_without_literals(cypher_query))
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

def _capability_token(flow_id: str, allowed_skills: Optional[List[str]] = None) -> str:
    """Mint a JWT capability token scoped to the skills actually in the flow."""
    secret = os.environ.get("CAPABILITY_TOKEN_SECRET", "dev-secret-do-not-use")
    skills = allowed_skills or [
        "filter_by_industry_exact",
        "random_shuffle",
        "semantic_similarity",
        "fuzzy_industry_match",
    ]
    payload = {
        "flow_id": flow_id,
        "allowed_connectors": ["sql_connector_v1", "csv_connector_v1", "json_connector_v1"],
        "allowed_skills": skills,
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _build_snapshot(
    industry: Optional[str] = None,
    app_id: Optional[str] = None,
) -> dict:
    """Query Neo4j Graph A for companies and mentors to build a sandbox snapshot.

    If *app_id* is provided, only Company nodes whose app_id matches are
    included. Falls back to the full graph if that query returns no results.
    The returned dict is always passed through _sanitize_snapshot().
    """
    _SAMPLE: dict = {
        "companies": [
            {
                "id": "C-01", "name": "Nexus AI", "industry": "Fintech",
                "description": "AI-powered payments startup", "pain_points": "scaling payments infrastructure",
            },
            {
                "id": "C-02", "name": "Etech Finance", "industry": "Fintech",
                "description": "B2B lending platform", "pain_points": "credit risk scoring",
            },
        ],
        "mentors": [
            {
                "id": "M-99", "name": "Dr. Kuan Studio",
                "expertise": ["Finance", "Scaling", "Payments"],
                "description": "Fintech scaling expert with payments background",
            },
            {
                "id": "M-88", "name": "Darveen Ventures",
                "expertise": ["Marketing", "Product", "B2B"],
                "description": "B2B product and go-to-market specialist",
            },
        ],
    }
    try:
        _company_fields = (
            "RETURN c.id AS id, c.name AS name, c.industry AS industry, "
            "c.description AS description, c.pain_points AS pain_points "
        )
        _mentor_fields = (
            "RETURN m.id AS id, m.name AS name, m.expertise_tags AS expertise, "
            "m.description AS description "
        )

        if app_id:
            company_rows = _run_read_cypher(
                f"MATCH (c:Company) WHERE c.app_id = $app_id {_company_fields}LIMIT 15",
                {"app_id": app_id},
            )
            if not company_rows:
                logger.info("No Company nodes for app_id=%s; using full graph.", app_id)
                company_rows = _run_read_cypher(
                    f"MATCH (c:Company) WHERE c.industry = $industry {_company_fields}LIMIT 15"
                    if industry
                    else f"MATCH (c:Company) {_company_fields}LIMIT 15",
                    {"industry": industry} if industry else {},
                )
        elif industry:
            company_rows = _run_read_cypher(
                f"MATCH (c:Company) WHERE c.industry = $industry {_company_fields}LIMIT 15",
                {"industry": industry},
            )
        else:
            company_rows = _run_read_cypher(
                f"MATCH (c:Company) {_company_fields}LIMIT 15"
            )

        mentor_rows = _run_read_cypher(
            f"MATCH (m:Mentor) {_mentor_fields}LIMIT 10"
        )

        companies = [
            {
                "id":          r["id"],
                "name":        r["name"],
                "industry":    r.get("industry", ""),
                "description": r.get("description", "") or "",
                "pain_points": r.get("pain_points", "") or "",
            }
            for r in company_rows
        ]
        mentors = [
            {
                "id":          r["id"],
                "name":        r["name"],
                "expertise":   r.get("expertise") or [],
                "description": r.get("description", "") or "",
            }
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


def _parse_sandbox_output(stdout: str) -> Optional[tuple[List[dict], Optional[float]]]:
    """Extract traces and optional sandbox_baseline_score from DATA_STREAM markers.

    Returns (traces, sandbox_baseline_score).  Handles two payload shapes:
      - Legacy list:  [{"company_id": ..., ...}, ...]
      - New dict:     {"traces": [...], "sandbox_baseline_score": 4.5}
    """
    if "DATA_STREAM_START" not in stdout or "DATA_STREAM_END" not in stdout:
        return None
    start = stdout.index("DATA_STREAM_START") + len("DATA_STREAM_START")
    end = stdout.index("DATA_STREAM_END")
    try:
        parsed = json.loads(stdout[start:end].strip())
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, list):
        return parsed, None
    if isinstance(parsed, dict):
        return parsed.get("traces", []), parsed.get("sandbox_baseline_score")
    return None


def _traces_to_metrics(
    traces: List[dict],
    latency_ms: int,
    sandbox_baseline_score: Optional[float] = None,
) -> Dict:
    scores = [
        t.get("simulated_outcome_score", 0.0)
        for t in traces
        if t.get("status") == "SIMULATION_SUCCESS"
    ]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    metrics: Dict = {"match_score": avg_score, "latency_ms": latency_ms, "sample_size": len(scores)}
    if sandbox_baseline_score is not None:
        metrics["sandbox_baseline_score"] = sandbox_baseline_score
    return metrics


def _mock_sandbox(flow_yaml: str) -> Dict:
    """Deterministic simulation scoring all 6 real skills by their expected performance tier.

    Scores mirror what the real skill executor produces for typical Fintech/Healthtech data:
      Tier A (semantic + domain-aware): 8.5–9.0  — multi-step intelligent pipelines
      Tier B (single semantic):          7.5–8.0  — meaningful but unfiltered matching
      Tier C (structural only):          6.5–7.5  — industry/expertise depth boosts only
      Tier D (random baseline):          2.8       — no intelligence, kept for comparison
    """
    try:
        yaml.safe_load(flow_yaml)  # syntax check only
    except yaml.YAMLError as exc:
        return {"status": "fail", "metrics": {}, "error_log": f"YAML parse error: {exc}"}

    text = flow_yaml.lower()

    # Tier A — best results: semantic reasoning + domain grounding
    has_semantic   = "semantic_similarity"       in text
    has_fuzzy      = "fuzzy_industry_match"      in text
    has_pain       = "pain_point_match"          in text
    has_filter     = "filter_by_industry_exact"  in text
    has_depth      = "score_by_expertise_depth"  in text
    has_random     = "random_shuffle"            in text

    if not any([has_semantic, has_fuzzy, has_pain, has_filter, has_depth, has_random]):
        return {
            "status": "fail",
            "metrics": {},
            "error_log": "No recognised skills in flow. Use one of: semantic_similarity, "
                         "filter_by_industry_exact, fuzzy_industry_match, pain_point_match, "
                         "score_by_expertise_depth, random_shuffle.",
        }

    # Score = weighted sum of skills present
    skill_weights = {
        "semantic":  (has_semantic,  3.5),  # highest signal
        "pain":      (has_pain,      2.5),  # contextual boost
        "fuzzy":     (has_fuzzy,     2.0),  # moderate signal
        "filter":    (has_filter,    1.5),  # structural
        "depth":     (has_depth,     0.8),  # minor bonus
    }
    base = 5.0
    score = base + sum(w for present, w in skill_weights.values() if present)
    score = min(9.5, score)  # hard cap

    if has_random and not any(p for p, _ in skill_weights.values()):
        # pure random flow — explicit low score for comparison
        return {
            "status": "success",
            "metrics": {"latency_ms": 80, "match_score": 2.8, "sample_size": 20,
                        "sandbox_baseline_score": 2.8},
            "error_log": None,
        }

    return {
        "status": "success",
        "metrics": {
            "latency_ms": 180,
            "match_score": round(score, 1),
            "sample_size": 20,
            "sandbox_baseline_score": 2.8,   # random baseline for relative comparison
        },
        "error_log": None,
    }


def _local_sandbox(flow_yaml: str, snapshot: dict) -> Dict:
    """Run sandbox_task.py as a local subprocess."""
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
        if "flow_id" not in flow_def and len(flow_def) == 1:
            key = next(iter(flow_def))
            flow_name = str(key)
        else:
            flow_name = flow_def.get("flow_id", "proposed_flow")
    except yaml.YAMLError:
        flow_name = "proposed_flow"

    env = os.environ.copy()
    env["SNAPSHOT_DATA"]       = json.dumps(snapshot)
    env["PROPOSED_FLOW"]       = str(flow_name)
    env["PROPOSED_FLOW_YAML"]  = flow_yaml          # full YAML for real skill execution

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

    parsed = _parse_sandbox_output(proc.stdout)
    if parsed is None:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": (
                f"No DATA_STREAM_START/END markers in sandbox output. "
                f"stdout: {proc.stdout[:400]}"
            ),
        }

    traces, sandbox_baseline = parsed
    return {
        "status": "success",
        "metrics": _traces_to_metrics(traces, latency_ms, sandbox_baseline),
        "error_log": None,
        "traces": traces,
    }


def _poll_cloud_logging_for_traces(
    project: str, region: str, job: str, execution_id: str
) -> Optional[List[dict]]:
    """Poll Cloud Logging until DATA_STREAM_START/END markers appear."""
    try:
        from google.cloud import logging as gcp_logging  # noqa: PLC0415
    except ImportError:
        logger.error("google-cloud-logging is not installed.")
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

    logger.info("Polling Cloud Logging for execution %s (up to %ds).", execution_id, _MAX_WAIT_S)

    while time.time() < deadline:
        entries = list(log_client.list_entries(filter_=log_filter, order_by="timestamp asc"))
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
            parsed = _parse_sandbox_output(combined)
            if parsed is not None:
                traces, _ = parsed
                logger.info("Parsed %d traces from Cloud Logging for %s.", len(traces), execution_id)
                return parsed   # return full tuple so caller can extract baseline

        time.sleep(_POLL_S)

    logger.warning("No DATA_STREAM_START/END in Cloud Logging for %s after %ds.", execution_id, _MAX_WAIT_S)
    return None


def _classify_cloud_error(exc: Exception) -> dict:
    """Parse a GCP exception into a structured infra-error dict."""
    msg = str(exc)
    if "SERVICE_DISABLED" in msg or "has not been used in project" in msg:
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
    if "403" in msg or "PERMISSION_DENIED" in msg:
        return {
            "error_type": "CLOUD_PERMISSION_DENIED",
            "service": "run.googleapis.com",
            "activation_url": "",
            "human_action": "Check GCP IAM permissions for the service account.",
            "raw": msg[:600],
        }
    return {
        "error_type": "CLOUD_ERROR",
        "service": "",
        "activation_url": "",
        "human_action": "Check GCP project configuration and credentials.",
        "raw": msg[:600],
    }


def _cloud_run_sandbox(flow_yaml: str, snapshot: dict, token: str) -> Dict:
    """Trigger sandbox_task.py via a Google Cloud Run Job."""
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
        return {"status": "fail", "metrics": {}, "error_log": "GOOGLE_CLOUD_PROJECT env var is not set."}
    job = os.environ.get("SANDBOX_JOB_NAME")
    if not job:
        return {"status": "fail", "metrics": {}, "error_log": "SANDBOX_JOB_NAME env var is not set."}
    region = os.environ.get("SANDBOX_GCP_REGION", "us-central1")

    client = run_v2.JobsClient()
    request = run_v2.RunJobRequest(
        name=f"projects/{project}/locations/{region}/jobs/{job}",
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="SNAPSHOT_DATA",      value=json.dumps(snapshot)),
                        run_v2.EnvVar(name="PROPOSED_FLOW",      value=flow_name),
                        run_v2.EnvVar(name="PROPOSED_FLOW_YAML", value=flow_yaml),
                        run_v2.EnvVar(name="CAPABILITY_TOKEN",   value=token),
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
        logger.error("Cloud Run sandbox failed [%s]: %s", classified["error_type"], classified["raw"])
        return {
            "status": "fail",
            "metrics": {},
            "error_log": classified["human_action"],
            "infra_error": classified,
        }

    latency_ms = round((time.time() - t0) * 1000)
    execution_id = execution.name.split("/")[-1]
    logger.info("Cloud Run execution %s completed in %dms.", execution_id, latency_ms)

    parsed = _poll_cloud_logging_for_traces(project, region, job, execution_id)
    if parsed is None:
        return {
            "status": "fail",
            "metrics": {},
            "error_log": (
                f"Cloud Run execution '{execution_id}' finished but no DATA_STREAM_START/END "
                "markers were found in Cloud Logging within 60s."
            ),
        }

    traces, sandbox_baseline = parsed
    return {
        "status": "success",
        "metrics": _traces_to_metrics(traces, latency_ms, sandbox_baseline),
        "error_log": None,
        "traces": traces,
    }


# --------------------------------------------------------------------------- #
# Code sandbox — isolated codebase copy with patch + test execution            #
# --------------------------------------------------------------------------- #

def _detect_test_cmd(src_root: Path) -> Optional[str]:
    """Auto-detect a test/validation command from the project layout."""
    if (src_root / "package.json").exists():
        try:
            pkg = json.loads((src_root / "package.json").read_text())
            if pkg.get("scripts", {}).get("test"):
                return "npm test"
        except Exception:
            pass
    if (src_root / "Cargo.toml").exists():
        return "cargo test"
    py_files = list(src_root.rglob("*.py"))
    if py_files:
        test_files = [f for f in py_files if f.name.startswith("test_") or "tests" in str(f)]
        if test_files:
            return f"{sys.executable} -m pytest --tb=short -q"
        return f"{sys.executable} -m py_compile " + " ".join(str(f) for f in py_files[:20])
    return None


def _parse_pytest_output(output: str) -> tuple[int, int]:
    """Return (passed, failed) from pytest -q output."""
    passed = failed = 0
    for line in output.splitlines():
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", line)
        if m:
            failed = int(m.group(1))
    return passed, failed


def _code_sandbox(
    source_path: str,
    patches: List[Dict],
    test_cmd: Optional[str] = None,
) -> Dict:
    """Apply code patches to an isolated temp copy of source_path, then run tests.

    Each patch dict must have: file_path (str), old_code (str), new_code (str).
    Returns a result dict compatible with the evaluator's metrics format.
    match_score = (tests_passed / total_tests) * 10, capped at 10.
    """
    src_root = Path(source_path).expanduser().resolve()
    if not src_root.exists():
        return {
            "status": "fail",
            "metrics": {},
            "error_log": f"source_path does not exist: {source_path}",
        }

    tmp_dir = tempfile.mkdtemp(prefix="ecolink_sandbox_")
    traces: List[Dict] = []
    patch_count = 0

    try:
        # 1. Copy the codebase into the temp dir
        sandbox_root = Path(tmp_dir) / "src"
        shutil.copytree(str(src_root), str(sandbox_root), ignore=shutil.ignore_patterns(
            ".git", "node_modules", "__pycache__", ".venv", "venv", "*.pyc",
        ))

        # 2. Apply each patch
        for patch in patches:
            rel_path = patch.get("file_path", "")
            old_code = patch.get("old_code", "")
            new_code = patch.get("new_code", "")
            if not rel_path:
                continue

            target = sandbox_root / rel_path
            applied = False
            error_msg = None

            if old_code == "" and new_code:
                # New file creation
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new_code, encoding="utf-8")
                applied = True
            elif target.exists():
                original = target.read_text(encoding="utf-8")
                if old_code in original:
                    target.write_text(original.replace(old_code, new_code, 1), encoding="utf-8")
                    applied = True
                else:
                    error_msg = f"old_code not found in {rel_path}"
                    logger.warning("Code patch failed: %s", error_msg)
            else:
                error_msg = f"Target file not found: {rel_path}"
                logger.warning("Code patch failed: %s", error_msg)

            traces.append({
                "file": rel_path,
                "patch_applied": applied,
                "description": patch.get("description", ""),
                "error": error_msg,
            })
            if applied:
                patch_count += 1

        # 3. Detect or use provided test command
        cmd = test_cmd or _detect_test_cmd(sandbox_root)
        if not cmd:
            # No tests found — treat patch application success as the metric
            success_rate = patch_count / max(len(patches), 1)
            return {
                "status": "success" if patch_count > 0 else "fail",
                "metrics": {
                    "match_score": round(success_rate * 10, 2),
                    "patch_count": patch_count,
                    "tests_passed": 0,
                    "tests_failed": 0,
                },
                "error_log": None if patch_count > 0 else "No patches applied and no test command found.",
                "traces": traces,
            }

        # 4. Run the test command inside the sandbox copy
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(sandbox_root),
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "PYTHONPATH": str(sandbox_root)},
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "fail",
                "metrics": {"patch_count": patch_count},
                "error_log": "Code sandbox test command timed out after 120 seconds.",
                "traces": traces,
            }
        latency_ms = round((time.time() - t0) * 1000)
        test_output = proc.stdout + proc.stderr

        passed, failed = _parse_pytest_output(test_output)
        total = passed + failed
        if total == 0:
            # Binary pass/fail — no pytest counts
            success = proc.returncode == 0
            match_score = 10.0 if success else 0.0
            tests_passed = 1 if success else 0
            tests_failed = 0 if success else 1
            total = 1
        else:
            match_score = round((passed / total) * 10, 2)
            tests_passed = passed
            tests_failed = failed

        status = "success" if proc.returncode == 0 else "fail"
        for t in traces:
            t["test_output"] = test_output[:500]

        logger.info(
            "Code sandbox: %d patches applied, %d/%d tests passed, score=%.2f, latency=%dms",
            patch_count, tests_passed, total, match_score, latency_ms,
        )

        return {
            "status": status,
            "metrics": {
                "match_score": match_score,
                "latency_ms": latency_ms,
                "patch_count": patch_count,
                "tests_passed": tests_passed,
                "tests_failed": tests_failed,
            },
            "error_log": test_output[:800] if status == "fail" else None,
            "traces": traces,
        }

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@tool
def simulate_flow(flow_yaml: str, dataset_snapshot_id: str) -> Dict:
    """Send a proposed flow YAML to the Secure Sandbox and retrieve performance metrics.

    Sandbox mode is controlled by two env vars:
      SANDBOX_MOCK=true   → deterministic local mock (default, no dependencies)
      SANDBOX_MOCK=false  → real sandbox using sandbox_task.py
      SANDBOX_MODE=local     → run sandbox_task.py as a subprocess (no GCP)
      SANDBOX_MODE=cloudrun  → trigger sandbox_task.py via Cloud Run Job (needs GCP)

    A JWT capability token is generated from the skills in the proposed flow.
    The snapshot (companies + mentors) is built from live Neo4j data, scoped
    to the app_id encoded in dataset_snapshot_id (prefix: 'snapshot_<app_id>').

    Args:
        flow_yaml: Full YAML text of the proposed flow definition.
        dataset_snapshot_id: 'snapshot_<app_id>' to scope data, else default graph.

    Returns:
        Dict with status, metrics, error_log, and optionally traces.
    """
    try:
        flow_def = yaml.safe_load(flow_yaml) or {}
    except yaml.YAMLError as exc:
        return {"status": "fail", "metrics": {}, "error_log": f"Invalid YAML: {exc}"}

    if not isinstance(flow_def, dict):
        flow_def = {}

    normalised = _normalise_flow_def(flow_def) if flow_def else {}
    flow_id = normalised.get("flow_id", f"flow_{uuid.uuid4().hex[:8]}")

    # Build dynamic capability token from the actual skills in the YAML
    flow_skills = list(_extract_flow_references(normalised)[0]) if normalised else []
    token = _capability_token(flow_id, allowed_skills=flow_skills or None)

    use_mock = os.environ.get("SANDBOX_MOCK", "true").lower() == "true"
    if use_mock:
        logger.info("Sandbox running in MOCK mode.")
        return _mock_sandbox(flow_yaml)

    # Decode app_id from snapshot_id prefix: 'snapshot_<app_id>'
    _snap_app_id: Optional[str] = None
    if (
        dataset_snapshot_id
        and dataset_snapshot_id.startswith("snapshot_")
        and dataset_snapshot_id != "snapshot_2025_q4"
    ):
        _snap_app_id = dataset_snapshot_id.removeprefix("snapshot_")

    sandbox_mode = os.environ.get("SANDBOX_MODE", "local").lower()
    snapshot = _build_snapshot(app_id=_snap_app_id)
    logger.info(
        "Sandbox snapshot (app_id=%s): %d companies, %d mentors — mode: %s",
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

    Returns:
        Dict mapping server_id → {'load': float, 'error_rate': float}.
        load is normalised to 0–1 (current_load / 100).
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

    Args:
        change_type: One of 'new_flow', 'update_connector', 'deprecate_skill'.
        details: Dict containing the YAML or JSON content of the proposed change.

    Returns:
        The ID of the created proposal node.
    """
    allowed = {"new_flow", "update_connector", "deprecate_skill"}
    if change_type not in allowed:
        raise ValueError(f"change_type must be one of {allowed}, got '{change_type}'")

    proposal_id = f"{change_type.replace('_', '')}_proposal_{uuid.uuid4().hex[:8]}"
    details_json = json.dumps(details)
    business_flow_id = details.get("business_flow_id")
    project_id = details.get("project_id")
    simulation_score = details.get("simulation_score")
    flow_context = details.get("business_flow_context") or []
    source_name = ""
    if flow_context and isinstance(flow_context[0], dict):
        source_name = flow_context[0].get("business_flow") or ""
    proposal_name = f"Optimized {source_name}" if source_name else proposal_id
    justification = details.get("justification", "")

    _run_write_cypher(
        """
        CREATE (:Flow {
            id: $id,
            name: $name,
            status: 'proposed',
            yaml_config: $details,
            project_id: $project_id,
            business_flow_id: $business_flow_id,
            avg_outcome_score: $simulation_score,
            justification: $justification
        })
        """,
        {
            "id": proposal_id,
            "name": proposal_name,
            "details": details_json,
            "project_id": project_id,
            "business_flow_id": business_flow_id,
            "simulation_score": simulation_score,
            "justification": justification,
        },
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
    skills_applied: Optional[List[str]] = None,
    sandbox_baseline_score: Optional[float] = None,
) -> None:
    """Create an ExecutionTrace bridge node linking a Flow to an Outcome.

    Persists skills_applied and sandbox_baseline_score so the Planner can learn
    which skill combinations produced the best improvements over the baseline.
    Uses OPTIONAL MATCH so the trace is always written even if the Flow node
    does not exist yet; the RAN_FLOW relationship is only created when matched.
    """
    trace_id = f"trace_{uuid.uuid4().hex[:8]}"
    rows = _run_write_cypher(
        """
        OPTIONAL MATCH (f:Flow {id: $flow_id})
        CREATE (et:ExecutionTrace {
            id:              $trace_id,
            status:          $status,
            timestamp:       datetime(),
            skills_applied:  $skills_applied,
            baseline_score:  $sandbox_baseline_score
        })
        CREATE (o:Outcome {score: $result_score, date: date()})
        CREATE (et)-[:RESULTED_IN]->(o)
        WITH et, f
        WHERE f IS NOT NULL
        CREATE (et)-[:RAN_FLOW]->(f)
        RETURN et.id AS trace_id, f.id AS flow_id
        """,
        {
            "flow_id":               flow_id,
            "trace_id":              trace_id,
            "status":                status,
            "result_score":          result_score,
            "skills_applied":        skills_applied or [],
            "sandbox_baseline_score": sandbox_baseline_score,
        },
    )
    if not rows or rows[0].get("flow_id") is None:
        logger.warning(
            "ExecutionTrace %s written but Flow '%s' was not found in Graph B — "
            "RAN_FLOW relationship not created.",
            trace_id,
            flow_id,
        )
    else:
        logger.info(
            "ExecutionTrace %s logged for flow %s (score=%.2f, baseline=%.2f, skills=%s).",
            trace_id, flow_id, result_score,
            sandbox_baseline_score or 0.0,
            skills_applied or [],
        )


def activate_proposal(proposal_id: str) -> None:
    """Mark a previously proposed Flow node as 'active'. Called by HumanApproval."""
    _run_write_cypher(
        "MATCH (f:Flow {id: $id}) SET f.status = 'active'",
        {"id": proposal_id},
    )
    logger.info("Proposal %s activated.", proposal_id)


def reject_proposal(proposal_id: str, reason: str) -> None:
    """Mark proposal as 'rejected' and store the rejection reason."""
    _run_write_cypher(
        "MATCH (f:Flow {id: $id}) SET f.status = 'rejected', f.rejection_reason = $reason",
        {"id": proposal_id, "reason": reason},
    )
    logger.info("Proposal %s rejected: %s", proposal_id, reason)


def approve_skill_proposal(skill_id: str) -> None:
    """Mark a SkillProposal as approved. Called from Streamlit admin page."""
    _run_write_cypher(
        "MATCH (s:SkillProposal {id: $id}) SET s.status = 'approved'",
        {"id": skill_id},
    )
    logger.info("SkillProposal %s approved.", skill_id)


def reject_skill_proposal(skill_id: str, reason: str = "") -> None:
    """Mark a SkillProposal as rejected. Called from Streamlit admin page."""
    _run_write_cypher(
        "MATCH (s:SkillProposal {id: $id}) SET s.status = 'rejected', s.rejection_reason = $reason",
        {"id": skill_id, "reason": reason},
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
    """
    from src.graphrag.retriever import retrieve_semantic_context  # noqa: PLC0415
    return retrieve_semantic_context(query_text, top_k=top_k)
