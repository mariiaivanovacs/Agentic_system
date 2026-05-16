from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class FlowStep(BaseModel):
    id: str | None = None
    skill: str
    input: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class FlowYAML(BaseModel):
    flow_id: str
    description: str | None = None
    runs_on: str | None = None
    steps: list[FlowStep]

    @model_validator(mode="after")
    def steps_not_empty(self) -> "FlowYAML":
        if not self.steps:
            raise ValueError("steps list must contain at least one entry")
        return self


def validate_flow_yaml(yaml_string: str, valid_skill_ids: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    try:
        data = yaml.safe_load(yaml_string)
    except yaml.YAMLError as exc:
        return {"valid": False, "errors": [f"YAML parse error: {exc}"], "parsed": None}

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["YAML root must be a mapping"], "parsed": None}

    try:
        parsed = FlowYAML(**data)
    except Exception as exc:
        return {"valid": False, "errors": [f"Schema validation failed: {exc}"], "parsed": None}

    valid = set(valid_skill_ids)
    unknown = [step.skill for step in parsed.steps if step.skill not in valid]
    if unknown:
        errors.append(f"Unknown skill IDs: {sorted(set(unknown))}. Valid skill IDs: {sorted(valid)}")

    return {"valid": not errors, "errors": errors, "parsed": None if errors else parsed}

