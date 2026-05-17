"""
EcoLink Sandbox — real flow execution engine.

Reads three environment variables:
  SNAPSHOT_DATA       JSON string: {"companies": [...], "mentors": [...]}
  PROPOSED_FLOW_YAML  Full YAML text of the proposed flow (new — enables real execution)
  PROPOSED_FLOW       Flow ID string (legacy fallback when no YAML is provided)

Executes the flow YAML steps in sequence against the snapshot data, producing
a real match score based on actual skill logic rather than random numbers.

Output protocol (unchanged):
  DATA_STREAM_START
  <JSON list of trace dicts>
  DATA_STREAM_END

Each trace dict:
  company_id              str
  mentor_id               str
  flow_used               str
  simulated_outcome_score float  (1.0–10.0)
  status                  "SIMULATION_SUCCESS"
  skills_applied          list[str]
"""
from __future__ import annotations

import tarfile
import json
import os
import random
import re
import shutil
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

import jwt


def _log(severity: str, message: str) -> None:
    """Write a single structured JSON log line consumed by Cloud Logging."""
    print(json.dumps({"severity": severity, "message": message}), flush=True)

# PyYAML — available in local mode (same venv as orchestrator) and in Cloud Run
# if the Dockerfile installs it.  Graceful fallback for minimal environments.
try:
    import yaml as _yaml
    def _parse_yaml(text: str):
        return _yaml.safe_load(text)
except ImportError:
    def _parse_yaml(text: str):  # type: ignore[misc]
        return None


# --------------------------------------------------------------------------- #
# Keyword helpers                                                               #
# --------------------------------------------------------------------------- #

_STOP = {
    "the","a","an","in","for","of","and","or","is","are","to","at","by",
    "from","with","on","as","its","this","that","we","our","their","be",
    "was","been","have","has","had","it","not","but","also","can","will",
}

def _keywords(entity: dict) -> set:
    """Extract a bag-of-words from any combination of entity fields."""
    words: set = set()
    for field in (
        "industry", "name", "expertise", "expertise_tags",
        "pain_points", "description", "sector", "domain",
    ):
        val = entity.get(field, "")
        if isinstance(val, list):
            for item in val:
                words.update(re.findall(r"[a-z]{3,}", str(item).lower()))
        elif val:
            words.update(re.findall(r"[a-z]{3,}", str(val).lower()))
    return words - _STOP


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _best_fuzzy(source: str, targets: list) -> float:
    """Return the highest SequenceMatcher ratio between source and any target."""
    if not targets:
        return 0.0
    src = source.lower()
    return max(
        SequenceMatcher(None, src, str(t).lower()).ratio()
        for t in targets
    )


# --------------------------------------------------------------------------- #
# Skill implementations                                                         #
# --------------------------------------------------------------------------- #
# Each skill takes a candidate list and params dict, returns a scored list.
# Candidate shape: {company_id, mentor_id, company, mentor, score, flow_used}

def skill_semantic_similarity(candidates: list, params: dict) -> list:
    """Score by keyword Jaccard overlap between company profile and mentor expertise.

    Score mapping:  0% overlap → 4.0  |  50% overlap → 7.0  |  100% → 10.0
    """
    out = []
    for c in candidates:
        company_kw = _keywords(c["company"])
        mentor_kw  = _keywords(c["mentor"])
        j = _jaccard(company_kw, mentor_kw)
        score = round(4.0 + j * 6.0, 2)
        out.append({**c, "score": score})
    return out


def skill_filter_by_industry_exact(candidates: list, params: dict) -> list:
    """Boost pairs where mentor expertise contains the company's exact industry tag.

    +2.5 on match, -1.5 on mismatch.  Applied on top of the current score.
    """
    industry_filter = str(params.get("industry", "")).lower()
    out = []
    for c in candidates:
        company_industry = str(c["company"].get("industry", "")).lower()
        mentor_exp = [str(e).lower() for e in (c["mentor"].get("expertise") or [])]
        target = industry_filter if industry_filter else company_industry
        matched = any(target in exp or exp in target for exp in mentor_exp)
        delta = 2.5 if matched else -1.5
        score = max(1.0, min(10.0, c["score"] + delta))
        out.append({**c, "score": round(score, 2), "industry_match": matched})
    return out


def skill_fuzzy_industry_match(candidates: list, params: dict) -> list:
    """Boost pairs where mentor expertise fuzzy-matches the company's industry.

    Best SequenceMatcher ratio against all expertise tags maps to a -1 to +3 delta.
    """
    out = []
    for c in candidates:
        company_industry = str(c["company"].get("industry", ""))
        mentor_exp = c["mentor"].get("expertise") or []
        ratio = _best_fuzzy(company_industry, mentor_exp)
        delta = round(ratio * 4.0 - 1.0, 2)
        score = max(1.0, min(10.0, c["score"] + delta))
        out.append({**c, "score": round(score, 2), "fuzzy_ratio": round(ratio, 3)})
    return out


def skill_random_shuffle(candidates: list, params: dict) -> list:
    """Baseline: assigns random scores (3.0–7.0) — represents unintelligent matching."""
    out = [{**c, "score": round(random.uniform(3.0, 7.0), 2)} for c in candidates]
    random.shuffle(out)
    return out


def skill_score_by_expertise_depth(candidates: list, params: dict) -> list:
    """Bonus for mentors who cover more expertise areas (max +1.5)."""
    out = []
    for c in candidates:
        depth = len(c["mentor"].get("expertise") or [])
        bonus = min(1.5, depth * 0.25)
        score = max(1.0, min(10.0, c["score"] + bonus))
        out.append({**c, "score": round(score, 2)})
    return out


def skill_pain_point_match(candidates: list, params: dict) -> list:
    """Boost pairs where mentor keywords directly address the company's stated pain points.

    Uses keyword overlap specifically between pain_points field and mentor expertise.
    """
    out = []
    for c in candidates:
        pain_text = str(c["company"].get("pain_points", ""))
        pain_kw = set(re.findall(r"[a-z]{3,}", pain_text.lower())) - _STOP
        mentor_kw = _keywords(c["mentor"])
        if pain_kw:
            overlap = len(pain_kw & mentor_kw) / len(pain_kw)
            delta = round(overlap * 3.0, 2)
        else:
            delta = 0.0
        score = max(1.0, min(10.0, c["score"] + delta))
        out.append({**c, "score": round(score, 2)})
    return out


SKILL_REGISTRY: dict = {
    "semantic_similarity":       skill_semantic_similarity,
    "filter_by_industry_exact":  skill_filter_by_industry_exact,
    "fuzzy_industry_match":      skill_fuzzy_industry_match,
    "random_shuffle":            skill_random_shuffle,
    "score_by_expertise_depth":  skill_score_by_expertise_depth,
    "pain_point_match":          skill_pain_point_match,
}

SKILL_ALIASES: dict = {
    "skill_semantic_similarity": "semantic_similarity",
    "skill_filter_by_industry_exact": "filter_by_industry_exact",
    "skill_fuzzy_industry_match": "fuzzy_industry_match",
    "skill_random_shuffle": "random_shuffle",
    "skill_score_by_expertise_depth": "score_by_expertise_depth",
    "skill_pain_point_match": "pain_point_match",
    "skill_score_calculator": "score_by_expertise_depth",
    "score_calculator": "score_by_expertise_depth",
}


def _resolve_skill(skill_id: str) -> tuple[str, object]:
    canonical = SKILL_ALIASES.get(skill_id, skill_id)
    if canonical not in SKILL_REGISTRY and canonical.startswith("skill_"):
        canonical = canonical.removeprefix("skill_")
    return canonical, SKILL_REGISTRY.get(canonical)


# --------------------------------------------------------------------------- #
# Capability token validation                                                  #
# --------------------------------------------------------------------------- #

class CapabilityError(RuntimeError):
    """Raised when a sandbox capability token does not authorize the run."""


def _public_key() -> str:
    key = os.getenv("CAPABILITY_JWT_PUBLIC_KEY", "").strip()
    if key:
        return key.replace("\\n", "\n")

    key_path = os.getenv("CAPABILITY_JWT_PUBLIC_KEY_PATH", "").strip()
    if key_path:
        return Path(key_path).expanduser().read_text(encoding="utf-8")

    raise CapabilityError("CAPABILITY_JWT_PUBLIC_KEY or CAPABILITY_JWT_PUBLIC_KEY_PATH is required.")


def _flow_references(flow_def: dict) -> tuple[set[str], set[str]]:
    skills: set[str] = set()
    connectors: set[str] = set()
    for step in flow_def.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        skill = step.get("skill") or step.get("skill_id")
        if skill:
            skills.add(_resolve_skill(str(skill))[0])
        connector = step.get("connector") or step.get("connector_id")
        if connector:
            connectors.add(str(connector))

    for key in ("connector", "connector_id", "connector_used", "reads_from"):
        connector = flow_def.get(key)
        if connector:
            connectors.add(str(connector))
    return skills, connectors


def _parse_flow_for_auth(flow_yaml_text: str, legacy_flow_id: str) -> dict:
    if not flow_yaml_text:
        return {"flow_id": legacy_flow_id, "steps": []}
    raw = _parse_yaml(flow_yaml_text) or {}
    flow_def = _normalise_flow(raw)
    if not isinstance(flow_def, dict):
        raise CapabilityError("PROPOSED_FLOW_YAML must parse to a flow object.")
    if "flow_id" not in flow_def and isinstance(raw, dict) and len(raw) == 1:
        flow_def = dict(flow_def)
        flow_def.setdefault("flow_id", next(iter(raw)))
    flow_def.setdefault("flow_id", legacy_flow_id)
    return flow_def


def _validate_capability(flow_yaml_text: str, legacy_flow_id: str) -> dict:
    token = os.getenv("CAPABILITY_TOKEN", "").strip()
    if not token:
        raise CapabilityError("CAPABILITY_TOKEN is required.")

    expected_audience = os.getenv("CAPABILITY_TOKEN_AUDIENCE", "ecolink-sandbox-job")
    try:
        claims = jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            audience=expected_audience,
            options={
                "require": [
                    "aud",
                    "exp",
                    "iat",
                    "flow_id",
                    "project_id",
                    "run_id",
                    "allowed_skills",
                    "allowed_connectors",
                ]
            },
        )
    except jwt.PyJWTError as exc:
        raise CapabilityError(f"Invalid CAPABILITY_TOKEN: {exc}") from exc

    flow_def = _parse_flow_for_auth(flow_yaml_text, legacy_flow_id)
    expected_flow_id = str(flow_def.get("flow_id") or legacy_flow_id)
    if str(claims.get("flow_id")) != expected_flow_id:
        raise CapabilityError("CAPABILITY_TOKEN flow_id does not match PROPOSED_FLOW_YAML.")

    expected_project = os.getenv("PROJECT_ID", "").strip()
    if expected_project and str(claims.get("project_id")) != expected_project:
        raise CapabilityError("CAPABILITY_TOKEN project_id does not match PROJECT_ID.")

    expected_run = os.getenv("RUN_ID", "").strip()
    if expected_run and str(claims.get("run_id")) != expected_run:
        raise CapabilityError("CAPABILITY_TOKEN run_id does not match RUN_ID.")

    allowed_skills = set(str(s) for s in claims.get("allowed_skills") or [])
    allowed_connectors = set(str(c) for c in claims.get("allowed_connectors") or [])
    flow_skills, flow_connectors = _flow_references(flow_def)

    denied_skills = sorted(flow_skills - allowed_skills)
    if denied_skills:
        raise CapabilityError(f"Flow uses unauthorized skills: {', '.join(denied_skills)}")

    denied_connectors = sorted(flow_connectors - allowed_connectors)
    if denied_connectors:
        raise CapabilityError(f"Flow uses unauthorized connectors: {', '.join(denied_connectors)}")

    return claims


def _download_source_bundle() -> None:
    uri = os.getenv("SOURCE_BUNDLE_GCS_URI", "").strip()
    if not uri:
        return
    if not uri.startswith("gs://"):
        raise CapabilityError("SOURCE_BUNDLE_GCS_URI must be a gs:// URI.")

    try:
        from google.cloud import storage  # noqa: PLC0415
    except ImportError as exc:
        raise CapabilityError("google-cloud-storage is required to download source bundles.") from exc

    bucket_name, blob_name = uri.removeprefix("gs://").split("/", 1)
    target_root = Path("/tmp/source_bundle")
    shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=True)

    archive_path = Path(tempfile.mkdtemp(prefix="source_bundle_")) / "source.tar.gz"
    try:
        client = storage.Client()
        client.bucket(bucket_name).blob(blob_name).download_to_filename(str(archive_path))
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                target = (target_root / member.name).resolve()
                if not str(target).startswith(str(target_root.resolve())):
                    raise CapabilityError("Source bundle contains an unsafe path.")
            tar.extractall(target_root)
        _log("INFO", f"source bundle downloaded to {target_root}")
    finally:
        shutil.rmtree(str(archive_path.parent), ignore_errors=True)


# --------------------------------------------------------------------------- #
# Flow YAML normalisation                                                       #
# --------------------------------------------------------------------------- #

def _normalise_flow(flow_def: dict) -> dict:
    """Accept top-level flow dicts or nested {flow_id: {...}} YAML shapes."""
    if not isinstance(flow_def, dict):
        return {}
    if "steps" in flow_def or "runs_on" in flow_def:
        return flow_def
    if len(flow_def) == 1:
        flow_id, inner = next(iter(flow_def.items()))
        if isinstance(inner, dict):
            normalised = dict(inner)
            normalised.setdefault("flow_id", str(flow_id))
            return normalised
    return flow_def


# --------------------------------------------------------------------------- #
# Flow executor                                                                 #
# --------------------------------------------------------------------------- #

def run_flow(flow_yaml_text: str, companies: list, mentors: list) -> list:
    """Parse flow YAML and execute each step against all company-mentor pairs.

    Returns a list of trace dicts with one entry per company (best match kept).
    """
    # Parse YAML ---------------------------------------------------------------
    try:
        raw = _parse_yaml(flow_yaml_text)
        flow_def = _normalise_flow(raw or {})
    except Exception as exc:
        _log("WARNING", f"Could not parse flow YAML ({exc}). Using random_shuffle fallback.")
        flow_def = {}

    flow_id = flow_def.get("flow_id", "proposed_flow")
    steps   = flow_def.get("steps") or []

    # Initial candidates: every (company, mentor) pair at neutral score 5.0 ----
    candidates = [
        {
            "company_id": c.get("id", "?"),
            "mentor_id":  m.get("id", "?"),
            "company":    c,
            "mentor":     m,
            "score":      5.0,
            "flow_used":  flow_id,
        }
        for c in companies
        for m in mentors
    ]

    if not candidates:
        _log("WARNING", "No candidate pairs — empty companies or mentors list.")
        return []

    # Execute each step in sequence -------------------------------------------
    applied: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        skill_id = step.get("skill")
        if not skill_id:
            continue
        params   = step.get("params") or step.get("input") or {}
        canonical_skill, skill_fn = _resolve_skill(str(skill_id))
        if skill_fn:
            candidates = skill_fn(candidates, params)
            applied.append(canonical_skill)
            n = len(candidates)
            avg = round(sum(x["score"] for x in candidates) / n, 2) if n else 0.0
            _log("INFO", f"[{canonical_skill}] {n} candidates, avg_score={avg}")
        else:
            _log("WARNING", f"Unknown skill '{skill_id}' — skipping step.")

    if not applied:
        _log("WARNING", "No recognisable skills in flow. Applying random_shuffle baseline.")
        candidates = skill_random_shuffle(candidates, {})
        applied = ["random_shuffle"]

    # Keep only the best-scoring mentor per company ----------------------------
    best: dict = {}
    for c in candidates:
        cid = c["company_id"]
        if cid not in best or c["score"] > best[cid]["score"]:
            best[cid] = c

    traces = [
        {
            "company_id":             c["company_id"],
            "mentor_id":              c["mentor_id"],
            "flow_used":              c.get("flow_used", flow_id),
            "simulated_outcome_score": c["score"],
            "status":                 "SIMULATION_SUCCESS",
            "skills_applied":         applied,
        }
        for c in best.values()
    ]

    if traces:
        avg = round(sum(t["simulated_outcome_score"] for t in traces) / len(traces), 2)
        _log("INFO", f"{len(traces)} matches produced. avg_score={avg}, skills={applied}")

    return traces


# --------------------------------------------------------------------------- #
# Entry point                                                                   #
# --------------------------------------------------------------------------- #

def run_simulation() -> None:
    _log("INFO", "SANDBOX: INITIALIZING")

    snapshot_str    = os.getenv("SNAPSHOT_DATA", "{}")
    flow_yaml_text  = os.getenv("PROPOSED_FLOW_YAML", "")
    legacy_flow_id  = os.getenv("PROPOSED_FLOW", "unnamed_flow")

    try:
        claims = _validate_capability(flow_yaml_text, legacy_flow_id)
        _log("INFO", (
            f"capability token accepted "
            f"run_id={claims.get('run_id')} project_id={claims.get('project_id')}"
        ))
        _download_source_bundle()
    except CapabilityError as exc:
        _log("ERROR", str(exc))
        sys.exit(2)

    # Parse snapshot -----------------------------------------------------------
    try:
        snapshot = json.loads(snapshot_str)
    except json.JSONDecodeError as exc:
        _log("ERROR", f"SNAPSHOT_DATA is not valid JSON: {exc}")
        print("DATA_STREAM_START")
        print(json.dumps([]))
        print("DATA_STREAM_END")
        return

    companies = snapshot.get("companies", [])
    mentors   = snapshot.get("mentors", [])
    _log("INFO", f"{len(companies)} companies, {len(mentors)} mentors loaded.")

    # Execute flow or fall back to legacy random path --------------------------
    if flow_yaml_text:
        _log("INFO", f"Executing flow YAML ({len(flow_yaml_text)} chars).")
        traces = run_flow(flow_yaml_text, companies, mentors)
    else:
        # Legacy path — no YAML provided, random baseline so old callers don't break
        _log("WARNING", f"PROPOSED_FLOW_YAML not set. Using random baseline for '{legacy_flow_id}'.")
        traces = []
        for company in companies:
            mentor = random.choice(mentors) if mentors else None
            if mentor:
                traces.append({
                    "company_id":              company.get("id"),
                    "mentor_id":               mentor.get("id"),
                    "flow_used":               legacy_flow_id,
                    "simulated_outcome_score": round(random.uniform(3.0, 7.0), 2),
                    "status":                  "SIMULATION_SUCCESS",
                    "skills_applied":          [],
                })

    # ── Within-sample random baseline for relative comparison ─────────────────
    # Run random_shuffle on the SAME snapshot so the evaluator can measure
    # improvement relative to a no-intelligence baseline rather than against
    # potentially incompatible historical averages.
    _random_yaml = "flow_id: _baseline\nsteps:\n  - id: b1\n    skill: random_shuffle\n"
    baseline_traces = run_flow(_random_yaml, companies, mentors)
    baseline_avg = (
        round(sum(t["simulated_outcome_score"] for t in baseline_traces) / len(baseline_traces), 2)
        if baseline_traces else 5.0
    )
    _log("INFO", f"within-sample random baseline avg={baseline_avg}")

    # Output as a dict so downstream can extract sandbox_baseline_score.
    # _parse_sandbox_output in tools.py handles both list and dict formats.
    output = {
        "traces":                 traces,
        "sandbox_baseline_score": baseline_avg,
    }
    print("DATA_STREAM_START")
    print(json.dumps(output))
    print("DATA_STREAM_END")


if __name__ == "__main__":
    run_simulation()
