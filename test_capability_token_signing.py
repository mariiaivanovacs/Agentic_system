"""Tests for orchestrator capability-token payload construction."""
from __future__ import annotations

import jwt


def test_capability_token_uses_kms_signer_and_required_claims(monkeypatch):
    import src.agents.tools as tools

    calls: list[tuple[bytes, str]] = []

    def fake_sign(signing_input: bytes, key_version: str) -> bytes:
        calls.append((signing_input, key_version))
        return b"fake-signature"

    monkeypatch.setenv("CAPABILITY_KMS_KEY_VERSION", "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1")
    monkeypatch.setenv("CAPABILITY_TOKEN_AUDIENCE", "ecolink-sandbox-job")
    monkeypatch.delenv("CAPABILITY_JWT_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(tools, "_kms_sign_rs256", fake_sign)

    token = tools._capability_token(
        "flow-1",
        allowed_skills=["skill_score_calculator", "semantic_similarity"],
        allowed_connectors=["csv_connector_v1"],
        project_id="project-a",
        run_id="run-a",
    )

    claims = jwt.decode(token, options={"verify_signature": False})
    header = jwt.get_unverified_header(token)

    assert header["alg"] == "RS256"
    assert claims["aud"] == "ecolink-sandbox-job"
    assert claims["flow_id"] == "flow-1"
    assert claims["project_id"] == "project-a"
    assert claims["run_id"] == "run-a"
    assert claims["allowed_skills"] == ["score_by_expertise_depth", "semantic_similarity"]
    assert claims["allowed_connectors"] == ["csv_connector_v1"]
    assert calls == [(b".".join(part.encode("ascii") for part in token.split(".")[:2]), "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1")]
