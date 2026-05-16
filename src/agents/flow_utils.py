"""
Shared YAML flow utilities used by both nodes.py and tools.py.

Extracted here to avoid circular imports: tools.py needs _extract_flow_references
to build dynamic capability tokens, but tools.py cannot import from nodes.py.
"""
from __future__ import annotations


def _extract_flow_references(flow_def: dict) -> tuple[set[str], set[str]]:
    """Return (skill_ids, connector_ids) referenced in a parsed flow dict."""
    skills: set[str] = set()
    connectors: set[str] = set()

    for step in flow_def.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        skill = step.get("skill")
        if skill:
            skills.add(str(skill))
        connector = step.get("connector") or step.get("connector_id")
        if connector:
            connectors.add(str(connector))

    for key in ("connector", "connector_id", "connector_used", "reads_from"):
        connector = flow_def.get(key)
        if connector:
            connectors.add(str(connector))

    return skills, connectors


def _normalise_flow_def(flow_def: dict) -> dict:
    """Accept either top-level flow fields or {flow_id: {...}} YAML."""
    if "steps" in flow_def or "runs_on" in flow_def:
        return flow_def
    if len(flow_def) != 1:
        return flow_def

    flow_id, nested = next(iter(flow_def.items()))
    if not isinstance(nested, dict):
        return flow_def

    normalised = dict(nested)
    normalised.setdefault("flow_id", str(flow_id))
    return normalised
