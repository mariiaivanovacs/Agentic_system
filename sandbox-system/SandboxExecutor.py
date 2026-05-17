"""
Standalone Cloud Run Job invoker for the EcoLink sandbox.

The orchestrator (tools.py _cloud_run_sandbox) is the primary path; this
module exists as a thin wrapper for manual testing or external callers that
already hold a pre-minted CAPABILITY_TOKEN.

All GCP config is read from env vars so no values are ever hardcoded.
Required env vars for the __main__ block:
  GOOGLE_CLOUD_PROJECT    GCP project ID
  SANDBOX_JOB_NAME        Cloud Run Job name
  CAPABILITY_TOKEN        Pre-minted RS256 JWT capability token

Optional:
  SANDBOX_GCP_REGION      Defaults to us-central1
  RUN_ID                  Injected into the job for correlation; auto-generated if absent
"""
import json
import os
import sys
import uuid

from google.cloud import run_v2


def run_isolated_sandbox(
    project_id: str,
    location: str,
    job_name: str,
    sample_snapshot: dict,
    flow_yaml: str,
    capability_token: str,
    run_id: str = "",
):
    """Trigger the Cloud Run sandbox job with a full env override set.

    Args:
        project_id:        GCP project ID.
        location:          GCP region (e.g. us-central1).
        job_name:          Cloud Run Job name.
        sample_snapshot:   Sanitized {companies, mentors} dict.
        flow_yaml:         Full YAML text of the proposed flow.
        capability_token:  Pre-minted RS256 JWT scoped to this run.
        run_id:            Correlation ID; auto-generated if empty.
    """
    if not run_id:
        run_id = f"run_{uuid.uuid4().hex[:12]}"

    try:
        import yaml  # noqa: PLC0415
        raw = yaml.safe_load(flow_yaml) or {}
        flow_name = raw.get("flow_id", "proposed_flow") if isinstance(raw, dict) else "proposed_flow"
        if "flow_id" not in raw and len(raw) == 1:
            flow_name = next(iter(raw))
    except Exception:
        flow_name = "proposed_flow"

    client = run_v2.JobsClient()
    job_path = f"projects/{project_id}/locations/{location}/jobs/{job_name}"

    request = run_v2.RunJobRequest(
        name=job_path,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="SNAPSHOT_DATA",      value=json.dumps(sample_snapshot)),
                        run_v2.EnvVar(name="PROPOSED_FLOW",      value=flow_name),
                        run_v2.EnvVar(name="PROPOSED_FLOW_YAML", value=flow_yaml),
                        run_v2.EnvVar(name="CAPABILITY_TOKEN",   value=capability_token),
                        run_v2.EnvVar(name="PROJECT_ID",         value=project_id),
                        run_v2.EnvVar(name="RUN_ID",             value=run_id),
                    ]
                )
            ]
        ),
    )

    print(f"Triggering Cloud Run Job '{job_name}' (run_id={run_id})...")

    try:
        operation = client.run_job(request=request)
        print("Job triggered. Waiting for execution to complete...")
        response = operation.result()
        print("Execution complete.")
        return response
    except Exception as exc:
        print(f"CRITICAL FAULT: failed to execute cloud job. Details:\n{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    _region  = os.environ.get("SANDBOX_GCP_REGION", "us-central1")
    _job     = os.environ.get("SANDBOX_JOB_NAME", "")
    _token   = os.environ.get("CAPABILITY_TOKEN", "")
    _run_id  = os.environ.get("RUN_ID", f"run_{uuid.uuid4().hex[:12]}")

    missing = [k for k, v in [
        ("GOOGLE_CLOUD_PROJECT", _project),
        ("SANDBOX_JOB_NAME", _job),
        ("CAPABILITY_TOKEN", _token),
    ] if not v]
    if missing:
        print(f"ERROR: required env vars not set: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    _flow_yaml = (
        "flow_id: test_semantic_flow\n"
        "steps:\n"
        "  - id: s1\n"
        "    skill: semantic_similarity\n"
    )
    _snapshot = {
        "companies": [
            {"id": "C-01", "name": "Nexus AI", "industry": "Fintech",
             "description": "AI-powered payments startup", "pain_points": "scaling payments"},
        ],
        "mentors": [
            {"id": "M-99", "name": "Dr. Kuan Studio",
             "expertise": ["Finance", "Scaling", "Payments"],
             "description": "Fintech scaling expert"},
        ],
    }

    run_isolated_sandbox(_project, _region, _job, _snapshot, _flow_yaml, _token, _run_id)
