from typing import Any

import yaml
from pydantic import BaseModel, model_validator


class FlowStep(BaseModel):
    skill: str
    params: dict[str, Any] = {}


class FlowYAML(BaseModel):
    flow_id: str
    steps: list[FlowStep]

    @model_validator(mode="after")
    def steps_not_empty(self) -> "FlowYAML":
        if not self.steps:
            raise ValueError("steps list must contain at least one entry")
        return self


def validate_flow_yaml(yaml_string: str, available_skill_names: list[str]) -> dict:
    """
    Returns {"valid": bool, "errors": list[str], "parsed": FlowYAML | None}.

    Checks in order:
    1. yaml.safe_load() parses without exception
    2. FlowYAML(**data) passes Pydantic validation
    3. Every step.skill exists in available_skill_names (hallucination guard)
    """
    errors: list[str] = []

    try:
        data = yaml.safe_load(yaml_string)
    except yaml.YAMLError as exc:
        return {"valid": False, "errors": [f"YAML parse error: {exc}"], "parsed": None}

    if not isinstance(data, dict):
        return {
            "valid": False,
            "errors": ["YAML root must be a mapping, not a scalar or list"],
            "parsed": None,
        }

    try:
        parsed = FlowYAML(**data)
    except Exception as exc:
        return {"valid": False, "errors": [f"Schema validation failed: {exc}"], "parsed": None}

    # Hallucination guard
    skill_name_set = set(available_skill_names)
    bad_skills = [step.skill for step in parsed.steps if step.skill not in skill_name_set]
    if bad_skills:
        valid_list = ", ".join(sorted(skill_name_set))
        errors.append(
            f"Hallucinated skills detected: {bad_skills}. "
            f"Valid skills are: [{valid_list}]"
        )

    if errors:
        return {"valid": False, "errors": errors, "parsed": None}

    return {"valid": True, "errors": [], "parsed": parsed}
