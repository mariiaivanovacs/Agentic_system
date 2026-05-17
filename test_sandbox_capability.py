"""Regression tests for sandbox capability-token enforcement."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


ROOT = Path(__file__).parent
SANDBOX_TASK_PATH = ROOT / "sandbox-system" / "sandbox_task.py"


def _load_sandbox_task():
    spec = importlib.util.spec_from_file_location("sandbox_task_test", SANDBOX_TASK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private, public


def _token(private_key: str, **overrides) -> str:
    now = int(time.time())
    payload = {
        "aud": "ecolink-sandbox-job",
        "flow_id": "flow_proposal_test_v1",
        "project_id": "project-a",
        "run_id": "run-a",
        "allowed_skills": ["semantic_similarity", "score_by_expertise_depth"],
        "allowed_connectors": ["csv_connector_v1"],
        "iat": now,
        "exp": now + 600,
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm="RS256")


def _set_common_env(monkeypatch, public_key: str, token: str) -> None:
    monkeypatch.setenv("CAPABILITY_JWT_PUBLIC_KEY", public_key)
    monkeypatch.setenv("CAPABILITY_TOKEN_AUDIENCE", "ecolink-sandbox-job")
    monkeypatch.setenv("CAPABILITY_TOKEN", token)
    monkeypatch.setenv("PROJECT_ID", "project-a")
    monkeypatch.setenv("RUN_ID", "run-a")


FLOW_YAML = """
flow_id: flow_proposal_test_v1
connector: csv_connector_v1
steps:
  - skill: semantic_similarity
  - skill: skill_score_calculator
"""


def test_valid_token_allows_canonicalized_alias(monkeypatch):
    sandbox_task = _load_sandbox_task()
    private, public = _keys()
    _set_common_env(monkeypatch, public, _token(private))

    claims = sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")

    assert claims["run_id"] == "run-a"


def test_missing_token_is_rejected(monkeypatch):
    sandbox_task = _load_sandbox_task()
    _, public = _keys()
    monkeypatch.setenv("CAPABILITY_JWT_PUBLIC_KEY", public)
    monkeypatch.delenv("CAPABILITY_TOKEN", raising=False)

    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "CAPABILITY_TOKEN is required" in str(exc)
    else:
        raise AssertionError("missing token was accepted")


def test_wrong_audience_is_rejected(monkeypatch):
    sandbox_task = _load_sandbox_task()
    private, public = _keys()
    _set_common_env(monkeypatch, public, _token(private, aud="other-audience"))

    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "Invalid CAPABILITY_TOKEN" in str(exc)
    else:
        raise AssertionError("wrong audience was accepted")


def test_expired_token_is_rejected(monkeypatch):
    sandbox_task = _load_sandbox_task()
    private, public = _keys()
    now = int(time.time())
    _set_common_env(monkeypatch, public, _token(private, iat=now - 700, exp=now - 1))

    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "Invalid CAPABILITY_TOKEN" in str(exc)
    else:
        raise AssertionError("expired token was accepted")


def test_mismatched_flow_project_or_run_is_rejected(monkeypatch):
    sandbox_task = _load_sandbox_task()
    private, public = _keys()
    _set_common_env(monkeypatch, public, _token(private, flow_id="other-flow"))

    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "flow_id" in str(exc)
    else:
        raise AssertionError("mismatched flow was accepted")

    _set_common_env(monkeypatch, public, _token(private, project_id="other-project"))
    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "project_id" in str(exc)
    else:
        raise AssertionError("mismatched project was accepted")

    _set_common_env(monkeypatch, public, _token(private, run_id="other-run"))
    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "run_id" in str(exc)
    else:
        raise AssertionError("mismatched run was accepted")


def test_unauthorized_skill_or_connector_is_rejected(monkeypatch):
    sandbox_task = _load_sandbox_task()
    private, public = _keys()
    _set_common_env(
        monkeypatch,
        public,
        _token(private, allowed_skills=["semantic_similarity"]),
    )
    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "unauthorized skills" in str(exc)
    else:
        raise AssertionError("unauthorized skill was accepted")

    _set_common_env(
        monkeypatch,
        public,
        _token(private, allowed_connectors=["sql_connector_v1"]),
    )
    try:
        sandbox_task._validate_capability(FLOW_YAML, "flow_proposal_test_v1")
    except sandbox_task.CapabilityError as exc:
        assert "unauthorized connectors" in str(exc)
    else:
        raise AssertionError("unauthorized connector was accepted")
