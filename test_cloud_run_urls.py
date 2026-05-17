from urllib.parse import unquote

from src.agents.cloud_run_urls import (
    cloud_run_execution_url,
    cloud_run_job_url,
    cloud_run_logs_url,
)


def test_cloud_run_job_url_without_execution_metadata(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("SANDBOX_GCP_REGION", "us-central1")
    monkeypatch.setenv("SANDBOX_JOB_NAME", "ecolink-sandbox-executor")

    assert cloud_run_job_url({}) == (
        "https://console.cloud.google.com/run/jobs/details/"
        "us-central1/ecolink-sandbox-executor/executions?project=demo-project"
    )


def test_cloud_run_execution_url_requires_execution_id(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("SANDBOX_GCP_REGION", "us-central1")
    monkeypatch.setenv("SANDBOX_JOB_NAME", "ecolink-sandbox-executor")

    assert cloud_run_execution_url({}) is None
    assert cloud_run_execution_url({"execution_id": "exec-123"}) == (
        "https://console.cloud.google.com/run/jobs/details/"
        "us-central1/ecolink-sandbox-executor/executions/exec-123?project=demo-project"
    )


def test_cloud_run_logs_url_uses_execution_log_filter():
    url = cloud_run_logs_url(
        {
            "gcp_project": "demo-project",
            "region": "asia-southeast1",
            "job": "sandbox-job",
            "execution_id": "sandbox-job-abcde",
        }
    )

    assert url is not None
    assert url.startswith("https://console.cloud.google.com/logs/query;query=")
    assert url.endswith("?project=demo-project")
    decoded = unquote(url.split(";query=", 1)[1].split("?project=", 1)[0])
    assert 'resource.type="cloud_run_job"' in decoded
    assert 'resource.labels.job_name="sandbox-job"' in decoded
    assert 'resource.labels.location="asia-southeast1"' in decoded
    assert 'logName="projects/demo-project/logs/run.googleapis.com%2Fstdout"' in decoded
    assert 'labels."run.googleapis.com/execution_name"="sandbox-job-abcde"' in decoded
