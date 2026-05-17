from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote


def _value(run_meta: dict[str, Any] | None, key: str, env_key: str | None = None) -> str:
    if run_meta and run_meta.get(key):
        return str(run_meta[key]).strip()
    if env_key:
        return os.environ.get(env_key, "").strip()
    return ""


def cloud_run_job_url(run_meta: dict[str, Any] | None = None) -> str | None:
    project = _value(run_meta, "gcp_project", "GOOGLE_CLOUD_PROJECT")
    region = (
        _value(run_meta, "region", "SANDBOX_GCP_REGION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    )
    job = _value(run_meta, "job", "SANDBOX_JOB_NAME")
    if not project or not region or not job:
        return None
    return (
        "https://console.cloud.google.com/run/jobs/details/"
        f"{region}/{job}/executions?project={project}"
    )


def cloud_run_execution_url(run_meta: dict[str, Any] | None) -> str | None:
    project = _value(run_meta, "gcp_project", "GOOGLE_CLOUD_PROJECT")
    region = (
        _value(run_meta, "region", "SANDBOX_GCP_REGION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    )
    job = _value(run_meta, "job", "SANDBOX_JOB_NAME")
    execution_id = _value(run_meta, "execution_id")
    if not project or not region or not job or not execution_id:
        return None
    return (
        "https://console.cloud.google.com/run/jobs/details/"
        f"{region}/{job}/executions/{execution_id}?project={project}"
    )


def cloud_run_logs_url(run_meta: dict[str, Any] | None) -> str | None:
    project = _value(run_meta, "gcp_project", "GOOGLE_CLOUD_PROJECT")
    region = (
        _value(run_meta, "region", "SANDBOX_GCP_REGION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    )
    job = _value(run_meta, "job", "SANDBOX_JOB_NAME")
    execution_id = _value(run_meta, "execution_id")
    if not project or not region or not job or not execution_id:
        return None

    log_filter = (
        'resource.type="cloud_run_job"\n'
        f'resource.labels.job_name="{job}"\n'
        f'resource.labels.location="{region}"\n'
        f'logName="projects/{project}/logs/run.googleapis.com%2Fstdout"\n'
        f'labels."run.googleapis.com/execution_name"="{execution_id}"'
    )
    return (
        "https://console.cloud.google.com/logs/query;query="
        f"{quote(log_filter, safe='')}?project={project}"
    )
