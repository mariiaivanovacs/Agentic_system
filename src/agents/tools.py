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
import tarfile
import tempfile
import time
import uuid
import base64
import hashlib
from pathlib import Path
from typing import Dict, List, Optional

import jwt
import yaml
from langchain_core.tools import tool
from neo4j import GraphDatabase, Query, READ_ACCESS, WRITE_ACCESS
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
        with driver.session(database=_db(), default_access_mode=READ_ACCESS) as session:
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
    retry=retry_if_exception_type((neo4j_exc.ServiceUnavailable, neo4j_exc.SessionExpired)),
    reraise=True,
)
def _run_read_cypher(cypher: str, params: Optional[Dict] = None) -> List[Dict]:
    driver = _get_driver()
    try:
        with driver.session(database=_db(), default_access_mode=READ_ACCESS) as session:
            result = session.run(Query(cypher, timeout=_query_timeout()), params or {})
            return [dict(record) for record in result]
    finally:
        driver.close()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((neo4j_exc.ServiceUnavailable, neo4j_exc.SessionExpired)),
    reraise=True,
)
def _run_write_cypher(cypher: str, params: Optional[Dict] = None) -> List[Dict]:
    """Execute a write Cypher query. Params are passed as a dict (matching _run_read_cypher)."""
    driver = _get_driver()
    try:
        with driver.session(database=_db(), default_access_mode=WRITE_ACCESS) as session:
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

_DEFAULT_ALLOWED_CONNECTORS = ["sql_connector_v1", "csv_connector_v1", "json_connector_v1"]
_DEFAULT_ALLOWED_SKILLS = [
    "filter_by_industry_exact",
    "random_shuffle",
    "semantic_similarity",
    "fuzzy_industry_match",
]
_CAPABILITY_SKILL_ALIASES = {
    "skill_semantic_similarity": "semantic_similarity",
    "skill_filter_by_industry_exact": "filter_by_industry_exact",
    "skill_fuzzy_industry_match": "fuzzy_industry_match",
    "skill_random_shuffle": "random_shuffle",
    "skill_score_by_expertise_depth": "score_by_expertise_depth",
    "skill_pain_point_match": "pain_point_match",
    "skill_score_calculator": "score_by_expertise_depth",
    "score_calculator": "score_by_expertise_depth",
}


def _canonical_skill_id(skill_id: str) -> str:
    canonical = _CAPABILITY_SKILL_ALIASES.get(skill_id, skill_id)
    if canonical.startswith("skill_"):
        canonical = canonical.removeprefix("skill_")
    return canonical


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _kms_sign_rs256(signing_input: bytes, key_version: str) -> bytes:
    """Sign JWT bytes with Cloud KMS asymmetric RSASSA_PKCS1_SHA256."""
    from google.cloud import kms_v1  # noqa: PLC0415

    digest = hashlib.sha256(signing_input).digest()
    client = kms_v1.KeyManagementServiceClient(credentials=_impersonated_credentials())
    response = client.asymmetric_sign(
        request={
            "name": key_version,
            "digest": kms_v1.Digest(sha256=digest),
        }
    )
    return response.signature


def _sign_capability_payload(payload: dict) -> str:
    """Sign a JWT payload with Cloud KMS, falling back to a dev RS256 key for tests."""
    header = {"alg": "RS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")),
            _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")),
        ]
    ).encode("ascii")

    key_version = os.environ.get("CAPABILITY_KMS_KEY_VERSION", "").strip()
    if key_version:
        signature = _kms_sign_rs256(signing_input, key_version)
        return f"{signing_input.decode('ascii')}.{_b64url(signature)}"

    dev_private_key = os.environ.get("CAPABILITY_JWT_PRIVATE_KEY", "").strip()
    if dev_private_key:
        return jwt.encode(payload, dev_private_key.replace("\\n", "\n"), algorithm="RS256")

    raise RuntimeError(
        "Capability token signing is not configured. Set CAPABILITY_KMS_KEY_VERSION "
        "for Cloud KMS signing, or CAPABILITY_JWT_PRIVATE_KEY for local tests."
    )


def _capability_token(
    flow_id: str,
    allowed_skills: Optional[List[str]] = None,
    allowed_connectors: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    """Mint an RS256 JWT capability token scoped to one sandbox run."""
    skills = sorted({_canonical_skill_id(s) for s in (allowed_skills or _DEFAULT_ALLOWED_SKILLS)})
    connectors = sorted(set(allowed_connectors or _DEFAULT_ALLOWED_CONNECTORS))
    now = int(time.time())
    payload = {
        "aud": os.environ.get("CAPABILITY_TOKEN_AUDIENCE", "ecolink-sandbox-job"),
        "flow_id": flow_id,
        "project_id": project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "local-dev"),
        "run_id": run_id or f"run_{uuid.uuid4().hex[:12]}",
        "allowed_connectors": connectors,
        "allowed_skills": skills,
        "iat": now,
        "exp": now + int(os.environ.get("CAPABILITY_TOKEN_TTL_SECONDS", "600")),
    }
    return _sign_capability_payload(payload)


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


def _local_sandbox(
    flow_yaml: str,
    snapshot: dict,
    token: str,
    project_id: str,
    run_id: str,
) -> Dict:
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
    env["CAPABILITY_TOKEN"]    = token
    env["PROJECT_ID"]          = project_id
    env["RUN_ID"]              = run_id

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


def _payloads_from_gcloud_logs(entries: list[dict]) -> str:
    parts: list[str] = []
    for entry in sorted(entries, key=lambda item: item.get("timestamp", "")):
        if "textPayload" in entry:
            parts.append(str(entry["textPayload"]))
        elif "jsonPayload" in entry:
            parts.append(json.dumps(entry["jsonPayload"]))
        elif "protoPayload" in entry:
            parts.append(json.dumps(entry["protoPayload"]))
    return "\n".join(parts)


def _poll_cloud_logging_with_gcloud(
    project: str, region: str, job: str, execution_id: str, max_wait_s: int
) -> Optional[tuple[List[dict], Optional[float]]]:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        logger.error("google-cloud-logging is not installed and gcloud CLI was not found.")
        return None

    log_filter = (
        f'resource.type="cloud_run_job" '
        f'resource.labels.job_name="{job}" '
        f'resource.labels.location="{region}" '
        f'logName="projects/{project}/logs/run.googleapis.com%2Fstdout" '
        f'labels."run.googleapis.com/execution_name"="{execution_id}"'
    )
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        cmd = [
            gcloud,
            "logging",
            "read",
            log_filter,
            "--project",
            project,
            "--limit",
            "200",
            "--format",
            "json",
        ]
        invoker = os.environ.get("SANDBOX_INVOKER_SERVICE_ACCOUNT", "").strip()
        if invoker:
            cmd.append(f"--impersonate-service-account={invoker}")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning("gcloud logging read failed: %s", proc.stderr[:300])
        else:
            try:
                combined = _payloads_from_gcloud_logs(json.loads(proc.stdout or "[]"))
            except json.JSONDecodeError:
                combined = ""
            if "DATA_STREAM_START" in combined and "DATA_STREAM_END" in combined:
                parsed = _parse_sandbox_output(combined)
                if parsed is not None:
                    traces, _ = parsed
                    logger.info("Parsed %d traces from gcloud logs for %s.", len(traces), execution_id)
                    return parsed
        time.sleep(5)
    return None


def _poll_cloud_logging_for_traces(
    project: str, region: str, job: str, execution_id: str
) -> Optional[tuple[List[dict], Optional[float]]]:
    """Poll Cloud Logging until DATA_STREAM_START/END markers appear."""
    try:
        from google.cloud import logging as gcp_logging  # noqa: PLC0415
    except ImportError:
        logger.info("google-cloud-logging is not installed; falling back to gcloud logging read.")
        return _poll_cloud_logging_with_gcloud(project, region, job, execution_id, max_wait_s=60)

    credentials = _impersonated_credentials()
    log_client = gcp_logging.Client(project=project, credentials=credentials)
    log_filter = (
        f'resource.type="cloud_run_job" '
        f'resource.labels.job_name="{job}" '
        f'resource.labels.location="{region}" '
        f'logName="projects/{project}/logs/run.googleapis.com%2Fstdout" '
        f'labels."run.googleapis.com/execution_name"="{execution_id}"'
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


_SOURCE_BUNDLE_EXCLUDES = {
    ".git",
    ".env",
    ".env.local",
    ".env.production",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".agent_events",
    ".agent_runs",
    ".agent_architecture_sandbox",
}


def _source_path_for_bundle() -> Optional[Path]:
    configured = os.environ.get("SANDBOX_SOURCE_PATH", "").strip()
    if not configured:
        return Path(__file__).resolve().parent.parent.parent
    source_path = Path(configured).expanduser().resolve()
    return source_path if source_path.exists() else None


def _include_in_source_bundle(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
    parts = Path(tarinfo.name).parts
    if any(part in _SOURCE_BUNDLE_EXCLUDES for part in parts):
        return None
    if any(part.endswith((".pyc", ".pyo")) for part in parts):
        return None
    return tarinfo


def _upload_source_bundle(run_id: str) -> Optional[str]:
    """Create and upload a sanitized source bundle for cloud sandbox runs."""
    bucket_name = os.environ.get("SANDBOX_SOURCE_BUCKET", "").strip()
    if not bucket_name:
        return None

    source_root = _source_path_for_bundle()
    if source_root is None:
        raise RuntimeError("SANDBOX_SOURCE_PATH is set but does not exist.")

    from google.cloud import storage  # noqa: PLC0415

    tmp_path = Path(tempfile.mkdtemp(prefix="sandbox_source_bundle_")) / "source.tar.gz"
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            tar.add(source_root, arcname="source", filter=_include_in_source_bundle)

        blob_name = f"sandbox-runs/{run_id}/source.tar.gz"
        client = storage.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            credentials=_impersonated_credentials(),
        )
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(tmp_path))
        return f"gs://{bucket_name}/{blob_name}"
    finally:
        shutil.rmtree(str(tmp_path.parent), ignore_errors=True)


def _impersonated_credentials():
    invoker = os.environ.get("SANDBOX_INVOKER_SERVICE_ACCOUNT", "").strip()
    if not invoker:
        return None

    try:
        import google.auth  # noqa: PLC0415
        from google.auth import impersonated_credentials  # noqa: PLC0415
    except ImportError:
        logger.warning("google-auth impersonation imports unavailable; using default credentials")
        return None

    source_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=invoker,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=900,
    )


def _run_jobs_client():
    from google.cloud import run_v2  # noqa: PLC0415

    credentials = _impersonated_credentials()
    if credentials is None:
        return run_v2.JobsClient()
    return run_v2.JobsClient(credentials=credentials)


def _discover_gcp_config() -> dict:
    """Resolve GCP project, region, and sandbox job without requiring manual env config.

    Resolution order:
      project: env GOOGLE_CLOUD_PROJECT → gcloud config → metadata server → google.auth.default()
      region:  env SANDBOX_GCP_REGION / GOOGLE_CLOUD_LOCATION → gcloud config → us-central1
      job:     env SANDBOX_JOB_NAME → Cloud Run jobs list (name contains 'sandbox')
    """
    result: dict = {}

    # ── Project ──────────────────────────────────────────────────────────────
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT") or
        os.environ.get("GCLOUD_PROJECT") or
        os.environ.get("GCP_PROJECT") or ""
    ).strip()

    if not project:
        try:
            proc = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            candidate = proc.stdout.strip()
            if proc.returncode == 0 and candidate and candidate != "(unset)":
                project = candidate
        except Exception:
            pass

    if not project:
        try:
            import urllib.request as _urlreq  # noqa: PLC0415
            req = _urlreq.Request(
                "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                headers={"Metadata-Flavor": "Google"},
            )
            with _urlreq.urlopen(req, timeout=1) as resp:
                project = resp.read().decode().strip()
        except Exception:
            pass

    if not project:
        try:
            import google.auth  # noqa: PLC0415
            _, detected = google.auth.default()
            project = (detected or "").strip()
        except Exception:
            pass

    if project:
        result["project"] = project

    # ── Region ───────────────────────────────────────────────────────────────
    region = (
        os.environ.get("SANDBOX_GCP_REGION") or
        os.environ.get("GOOGLE_CLOUD_LOCATION") or ""
    ).strip()

    if not region:
        try:
            proc = subprocess.run(
                ["gcloud", "config", "get-value", "run/region"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            candidate = proc.stdout.strip()
            if proc.returncode == 0 and candidate and candidate != "(unset)":
                region = candidate
        except Exception:
            pass

    result["region"] = region or "us-central1"

    # ── Job name ─────────────────────────────────────────────────────────────
    job = os.environ.get("SANDBOX_JOB_NAME", "").strip()

    if not job and project:
        try:
            from google.cloud import run_v2  # noqa: PLC0415
            client = _run_jobs_client()
            parent = f"projects/{project}/locations/{result['region']}"
            all_jobs = list(client.list_jobs(parent=parent))
            sandbox_jobs = [j for j in all_jobs if "sandbox" in j.name.lower()]
            if sandbox_jobs:
                job = sandbox_jobs[0].name.split("/")[-1]
                logger.info("Auto-discovered Cloud Run sandbox job: %s", job)
        except Exception as exc:
            logger.debug("Cloud Run job auto-discovery failed: %s", exc)

    if job:
        result["job"] = job

    return result


def _cloud_run_sandbox(
    flow_yaml: str,
    snapshot: dict,
    token: str,
    project_id: str,
    run_id: str,
) -> Dict:
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

    _gcp = _discover_gcp_config()
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or _gcp.get("project", "")
    if not project:
        return {
            "status": "fail", "metrics": {}, "error_log": (
                "Could not determine GCP project. Set GOOGLE_CLOUD_PROJECT, "
                "run 'gcloud auth application-default login', or ensure "
                "the process runs on a GCP VM with a metadata server."
            ),
        }
    region = os.environ.get("SANDBOX_GCP_REGION") or _gcp.get("region", "us-central1")
    job = os.environ.get("SANDBOX_JOB_NAME") or _gcp.get("job", "")
    if not job:
        return {
            "status": "fail", "metrics": {}, "error_log": (
                f"Could not find a Cloud Run sandbox job in project '{project}' "
                f"(region: {region}). Set SANDBOX_JOB_NAME or deploy the job with "
                "scripts/deploy_sandbox.sh."
            ),
        }

    try:
        source_bundle_uri = _upload_source_bundle(run_id)
    except Exception as exc:
        logger.error("Could not upload sandbox source bundle: %s", exc)
        return {
            "status": "fail",
            "metrics": {},
            "error_log": f"Could not upload sandbox source bundle: {exc}",
            "run": {
                "execution_mode": "cloudrun",
                "run_id": run_id,
                "project_id": project_id,
                "gcp_project": project,
                "region": region,
                "job": job,
                "stage": "source_bundle_upload",
            },
        }

    client = _run_jobs_client()
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
                        run_v2.EnvVar(name="PROJECT_ID",         value=project_id),
                        run_v2.EnvVar(name="RUN_ID",             value=run_id),
                        run_v2.EnvVar(name="SOURCE_BUNDLE_GCS_URI", value=source_bundle_uri or ""),
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
            "run": {
                "execution_mode": "cloudrun",
                "run_id": run_id,
                "project_id": project_id,
                "gcp_project": project,
                "region": region,
                "job": job,
                "source_bundle_gcs_uri": source_bundle_uri,
                "stage": "cloud_run_execution",
            },
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
            "run": {
                "execution_mode": "cloudrun",
                "run_id": run_id,
                "project_id": project_id,
                "gcp_project": project,
                "region": region,
                "job": job,
                "execution_id": execution_id,
                "source_bundle_gcs_uri": source_bundle_uri,
                "latency_ms": latency_ms,
                "stage": "cloud_logging_parse",
            },
        }

    traces, sandbox_baseline = parsed
    return {
        "status": "success",
        "metrics": _traces_to_metrics(traces, latency_ms, sandbox_baseline),
        "error_log": None,
        "traces": traces,
        "run": {
            "execution_mode": "cloudrun",
            "run_id": run_id,
            "project_id": project_id,
            "gcp_project": project,
            "region": region,
            "job": job,
            "execution_id": execution_id,
            "source_bundle_gcs_uri": source_bundle_uri,
            "latency_ms": latency_ms,
            "stage": "complete",
        },
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

    use_mock = os.environ.get("SANDBOX_MOCK", "false").lower() == "true"
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

    sandbox_mode = os.environ.get("SANDBOX_MODE", "cloudrun").lower()
    project_id = _snap_app_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "local-dev")
    run_id = f"run_{uuid.uuid4().hex[:12]}"

    # Build dynamic capability token from the actual skills/connectors in the YAML.
    flow_skills, flow_connectors = _extract_flow_references(normalised) if normalised else (set(), set())
    try:
        token = _capability_token(
            flow_id,
            allowed_skills=list(flow_skills) or None,
            allowed_connectors=list(flow_connectors) or None,
            project_id=project_id,
            run_id=run_id,
        )
    except Exception as exc:
        logger.error("Could not mint capability token: %s", exc)
        return {
            "status": "fail",
            "metrics": {},
            "error_log": f"Could not mint capability token: {exc}",
        }

    snapshot = _build_snapshot(app_id=_snap_app_id)
    logger.info(
        "Sandbox snapshot (app_id=%s): %d companies, %d mentors — mode: %s",
        _snap_app_id or "none",
        len(snapshot.get("companies", [])),
        len(snapshot.get("mentors", [])),
        sandbox_mode,
    )

    if sandbox_mode == "cloudrun":
        return _cloud_run_sandbox(flow_yaml, snapshot, token, project_id, run_id)
    return _local_sandbox(flow_yaml, snapshot, token, project_id, run_id)


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
        change_type: One of 'new_flow', 'update_connector', 'deprecate_skill', 'schema_extension'.
        details: Dict containing the YAML or JSON content of the proposed change.

    Returns:
        The ID of the created proposal node.
    """
    allowed = {"new_flow", "update_connector", "deprecate_skill", "schema_extension"}
    if change_type not in allowed:
        raise ValueError(f"change_type must be one of {allowed}, got '{change_type}'")

    proposal_id = f"{change_type.replace('_', '')}_proposal_{uuid.uuid4().hex[:8]}"
    details_json = json.dumps(details)
    business_flow_id = details.get("business_flow_id")
    project_id = details.get("project_id")
    simulation_score = details.get("simulation_score")
    flow_context = details.get("business_flow_context")
    source_name = ""
    if isinstance(flow_context, dict):
        source_name = flow_context.get("business_flow") or ""
    proposal_name = f"Optimized {source_name}" if source_name else proposal_id
    justification = details.get("justification", "")

    if change_type == "schema_extension":
        label = str(details.get("label") or details.get("node_label") or "").strip()
        if not label:
            raise ValueError("schema_extension requires details.label or details.node_label")
        required_fields = details.get("required_fields") or ["id", "name"]
        optional_fields = details.get("optional_fields") or []
        relationship_examples = details.get("relationship_examples") or []
        reason = details.get("reason") or justification or "Agent requested a new graph primitive type."
        _run_write_cypher(
            """
            CREATE (:SchemaChangeProposal {
                id: $id,
                label: $label,
                name: $name,
                status: 'proposed',
                proposed_by: 'agent',
                reason: $reason,
                required_fields: $required_fields,
                optional_fields: $optional_fields,
                relationship_examples: $relationship_examples,
                project_id: $project_id,
                details_json: $details,
                created_at: datetime()
            })
            """,
            {
                "id": proposal_id,
                "label": label,
                "name": f"Schema extension: {label}",
                "reason": reason,
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "relationship_examples": relationship_examples,
                "project_id": project_id,
                "details": details_json,
            },
        )
        logger.info("SchemaChangeProposal %s created in Neo4j.", proposal_id)
        return proposal_id

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


def activate_proposal(
    proposal_id: str,
    *,
    merged_by: str = "streamlit_ui",
    merge_source: str = "human_approval",
) -> Optional[Dict]:
    """Mark a previously proposed Flow node as 'active'. Called by HumanApproval."""
    existing = _run_read_cypher(
        """
        MATCH (f:Flow {id: $id})
        OPTIONAL MATCH (evt:RegistryMergeEvent)-[:MERGED_FLOW]->(f)
        WITH f, evt
        ORDER BY evt.timestamp DESC
        RETURN f.id AS id,
               coalesce(f.name, f.id) AS name,
               f.status AS status,
               f.project_id AS project_id,
               f.business_flow_id AS business_flow_id,
               toString(f.last_registry_merge_at) AS last_registry_merge_at,
               f.last_registry_merge_by AS last_registry_merge_by,
               f.last_registry_merge_source AS last_registry_merge_source,
               f.registry_merge_count AS registry_merge_count,
               evt.id AS merge_event_id
        LIMIT 1
        """,
        {"id": proposal_id},
    )
    if not existing:
        logger.warning("Proposal %s was not found; no Flow was activated.", proposal_id)
        return None
    if existing[0].get("status") == "active" and existing[0].get("last_registry_merge_at"):
        logger.info("Proposal %s already active; returning existing merge metadata.", proposal_id)
        return existing[0]

    rows = _run_write_cypher(
        """
        MATCH (f:Flow {id: $id})
        SET f.status = 'active',
            f.activated_at = coalesce(f.activated_at, datetime()),
            f.last_registry_merge_at = datetime(),
            f.last_registry_merge_by = $merged_by,
            f.last_registry_merge_source = $merge_source,
            f.registry_merge_count = coalesce(f.registry_merge_count, 0) + 1
        CREATE (evt:RegistryMergeEvent {
            id: $event_id,
            flow_id: $id,
            flow_name: coalesce(f.name, f.id),
            status: 'success',
            merged_by: $merged_by,
            source: $merge_source,
            timestamp: datetime()
        })
        CREATE (evt)-[:MERGED_FLOW]->(f)
        RETURN f.id AS id,
               coalesce(f.name, f.id) AS name,
               f.status AS status,
               f.project_id AS project_id,
               f.business_flow_id AS business_flow_id,
               toString(f.last_registry_merge_at) AS last_registry_merge_at,
               f.last_registry_merge_by AS last_registry_merge_by,
               f.last_registry_merge_source AS last_registry_merge_source,
               f.registry_merge_count AS registry_merge_count,
               evt.id AS merge_event_id
        """,
        {
            "id": proposal_id,
            "event_id": f"registry_merge_{uuid.uuid4().hex[:10]}",
            "merged_by": merged_by,
            "merge_source": merge_source,
        },
    )
    logger.info("Proposal %s activated.", proposal_id)
    return rows[0]


def reject_proposal(proposal_id: str, reason: str) -> None:
    """Mark proposal as 'rejected' and store the rejection reason."""
    _run_write_cypher(
        "MATCH (f:Flow {id: $id}) SET f.status = 'rejected', f.rejection_reason = $reason",
        {"id": proposal_id, "reason": reason},
    )
    logger.info("Proposal %s rejected: %s", proposal_id, reason)


def set_flow_container_url(flow_id: str, url: str) -> None:
    """Store the deployed container URL on an active Flow node."""
    _run_write_cypher(
        "MATCH (f:Flow {id: $id}) SET f.container_url = $url",
        {"id": flow_id, "url": url},
    )
    logger.info("Container URL set on flow %s: %s", flow_id, url)


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


def create_architecture_proposal(payload: Dict) -> str:
    """Persist a tested architecture proposal for admin approval.

    The payload must be sanitized before it reaches this function. Credential
    values are intentionally not accepted; only credential reference names are
    stored so approved replacements can keep using the existing environment.
    """
    proposal_id = payload["proposal_id"]
    summary = payload.get("summary", {})
    validation = payload.get("validation", {})
    _run_write_cypher(
        """
        MERGE (p:ArchitectureProposal {id: $id})
        SET p.project_id = $project_id,
            p.project_name = $project_name,
            p.status = 'proposed',
            p.replacement_mode = $replacement_mode,
            p.test_status = $test_status,
            p.tested = $tested,
            p.summary = $summary,
            p.payload_json = $payload_json,
            p.credential_refs = $credential_refs,
            p.created_at = coalesce(p.created_at, datetime()),
            p.updated_at = datetime()
        WITH p
        OPTIONAL MATCH (project:Project {id: $project_id})
        FOREACH (_ IN CASE WHEN project IS NULL THEN [] ELSE [1] END |
            MERGE (project)-[:HAS_ARCHITECTURE_PROPOSAL]->(p)
        )
        """,
        {
            "id": proposal_id,
            "project_id": payload.get("project_id"),
            "project_name": payload.get("project_name"),
            "replacement_mode": payload.get("replacement_mode", "merge"),
            "test_status": validation.get("status", "unknown"),
            "tested": validation.get("status") == "success",
            "summary": summary.get("title", "Architecture proposal"),
            "payload_json": json.dumps(payload),
            "credential_refs": payload.get("credential_refs", []),
        },
    )
    return proposal_id


def list_architecture_proposals(project_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
    """Return architecture proposals, newest first."""
    clauses = []
    params: Dict = {}
    if project_id:
        clauses.append("p.project_id = $project_id")
        params["project_id"] = project_id
    if status:
        clauses.append("p.status = $status")
        params["status"] = status
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return _run_read_cypher(
        f"""
        MATCH (p:ArchitectureProposal)
        {where}
        RETURN p.id AS id,
               p.project_id AS project_id,
               p.project_name AS project_name,
               p.status AS status,
               p.replacement_mode AS replacement_mode,
               p.test_status AS test_status,
               p.tested AS tested,
               p.summary AS summary,
               p.payload_json AS payload_json,
               p.credential_refs AS credential_refs,
               toString(p.created_at) AS created_at,
               toString(p.updated_at) AS updated_at
        ORDER BY p.updated_at DESC
        LIMIT 50
        """,
        params,
    )


def approve_architecture_proposal(proposal_id: str) -> Dict:
    """Apply a tested architecture proposal as active architecture metadata."""
    rows = _run_read_cypher(
        "MATCH (p:ArchitectureProposal {id: $id}) RETURN p.payload_json AS payload_json, p.tested AS tested",
        {"id": proposal_id},
    )
    if not rows:
        raise ValueError(f"ArchitectureProposal not found: {proposal_id}")
    if not rows[0].get("tested"):
        raise ValueError("Only successfully tested architecture proposals can be approved.")

    payload = json.loads(rows[0]["payload_json"])
    project_id = payload["project_id"]
    replacement_mode = payload.get("replacement_mode", "merge")
    if replacement_mode == "replace":
        _run_write_cypher(
            """
            MATCH (n)
            WHERE n.project_id = $project_id
              AND ('ArchitectureRule' IN labels(n) OR 'ArchitectureConnector' IN labels(n))
            DETACH DELETE n
            """,
            {"project_id": project_id},
        )

    for rule in payload.get("communication_rules", []):
        rule_id = f"{proposal_id}_rule_{uuid.uuid5(uuid.NAMESPACE_URL, rule.get('name', 'rule')).hex[:8]}"
        _run_write_cypher(
            """
            MERGE (r:ArchitectureRule {id: $id})
            SET r.project_id = $project_id,
                r.proposal_id = $proposal_id,
                r.name = $name,
                r.rule = $rule,
                r.status = 'active',
                r.updated_at = datetime()
            WITH r
            MATCH (p:Project {id: $project_id})
            MERGE (p)-[:HAS_ARCHITECTURE_RULE]->(r)
            """,
            {
                "id": rule_id,
                "project_id": project_id,
                "proposal_id": proposal_id,
                "name": rule.get("name"),
                "rule": rule.get("rule"),
            },
        )

    for connector in payload.get("database_connectors", []):
        connector_id = f"{proposal_id}_connector_{connector.get('id')}"
        _run_write_cypher(
            """
            MERGE (c:ArchitectureConnector {id: $id})
            SET c.project_id = $project_id,
                c.proposal_id = $proposal_id,
                c.source_connector_id = $source_connector_id,
                c.name = $name,
                c.type = $type,
                c.description = $description,
                c.version = $version,
                c.status = 'active',
                c.credential_refs = $credential_refs,
                c.updated_at = datetime()
            WITH c
            MATCH (p:Project {id: $project_id})
            MERGE (p)-[:HAS_ARCHITECTURE_CONNECTOR]->(c)
            """,
            {
                "id": connector_id,
                "project_id": project_id,
                "proposal_id": proposal_id,
                "source_connector_id": connector.get("id"),
                "name": connector.get("name"),
                "type": connector.get("type"),
                "description": connector.get("description"),
                "version": connector.get("version", "1.0"),
                "credential_refs": payload.get("credential_refs", []),
            },
        )

    _run_write_cypher(
        """
        MATCH (proposal:ArchitectureProposal {id: $proposal_id})
        SET proposal.status = 'approved',
            proposal.approved_at = datetime(),
            proposal.updated_at = datetime()
        WITH proposal
        MATCH (project:Project {id: $project_id})
        SET project.active_architecture_proposal_id = $proposal_id,
            project.architecture_status = 'approved',
            project.credential_refs = $credential_refs,
            project.updated_at = datetime()
        MERGE (project)-[:APPROVED_ARCHITECTURE]->(proposal)
        """,
        {
            "proposal_id": proposal_id,
            "project_id": project_id,
            "credential_refs": payload.get("credential_refs", []),
        },
    )
    return {"status": "approved", "proposal_id": proposal_id, "project_id": project_id}


def reject_architecture_proposal(proposal_id: str, reason: str = "Rejected by admin") -> Dict:
    _run_write_cypher(
        """
        MATCH (p:ArchitectureProposal {id: $id})
        SET p.status = 'rejected',
            p.rejection_reason = $reason,
            p.updated_at = datetime()
        """,
        {"id": proposal_id, "reason": reason},
    )
    return {"status": "rejected", "proposal_id": proposal_id}


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
