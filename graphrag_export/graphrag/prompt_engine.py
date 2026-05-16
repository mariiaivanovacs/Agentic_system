"""
Prompt engine — pure string construction, no LLM calls, no DB access.

Public API consumed by generator.py and nodes.py (P1):
  build_planner_prompt(goal, context) -> str
  build_critic_prompt(proposed_yaml, context) -> str

Internal builder used by the CLI flow:
  build_prompt(goal, success_patterns_json, ...) -> str
"""
from __future__ import annotations

import json
import re

# TYPE_CHECKING guard keeps the import cheap at runtime
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .models import RetrievedContext

# Regex to extract the YAML block from Gemini's response.
# re.DOTALL must be passed so '.' matches newlines across the block.
YAML_EXTRACTION_PATTERN = r"```yaml\n(.*?)\n```"

SYSTEM_PROMPT = """\
You are an Ecosystem Optimization Expert for EcoLink NeuroCore.
Your task is to analyze historical Company-Mentor matching data and propose an optimized
matching flow as a YAML configuration.

You MUST follow these rules:
1. Only use skills from the AVAILABLE SKILLS list provided below. Do not invent skill names.
2. Your response MUST contain two sections in this exact order:
   a) A plain-English "## Reasoning Trace" section explaining your analysis.
   b) A fenced YAML block (```yaml ... ```) containing the proposed flow.
3. Every skill referenced in the YAML must exactly match a name in AVAILABLE SKILLS.
4. The YAML must conform to this schema:
   flow_id: <string>
   steps:
     - skill: <skill_name>
       params:
         <key>: <value>  # optional
"""

FEW_SHOT_BAD_EXAMPLE = """\
### BAD FLOW EXAMPLE (do NOT replicate this):
```yaml
flow_id: bad_fintech_v0
steps:
  - skill: skill_random_sort
    params: {}
  - skill: skill_exact_match
    params:
      field: industry
```
Why this fails:
- `skill_random_sort` introduces non-determinism and ignores semantic meaning entirely.
- `skill_exact_match` on a single field misses nuanced domain overlap between pain_points and expertise.
"""

FEW_SHOT_GOOD_EXAMPLE = """\
### GOOD FLOW EXAMPLE (replicate this reasoning pattern):
```yaml
flow_id: good_fintech_v1
steps:
  - skill: filter_by_industry_fuzzy
    params:
      industry: Fintech
  - skill: skill_semantic_match
    params:
      source_field: company.pain_points
      target_field: mentor.expertise
  - skill: sort_by_score_desc
    params: {}
  - skill: check_availability
    params: {}
```
Why this works:
- `filter_by_industry_fuzzy` narrows the candidate pool to the relevant sector.
- `skill_semantic_match` captures nuanced alignment between company needs and mentor expertise.
- `sort_by_score_desc` surfaces the best matches first.
- `check_availability` ensures the result is immediately actionable.
"""

_RETRY_TEMPLATE = """\
### PREVIOUS ATTEMPT FAILED — FIX REQUIRED:
The last YAML you generated was rejected with this error:
{prior_error}

Please correct the YAML. Remember: only use skill names from AVAILABLE SKILLS listed below.
"""


def build_prompt(
    goal: str,
    success_patterns_json: str,
    failure_patterns_json: str,
    available_skills_json: str,
    prior_error: str | None = None,
) -> str:
    """Assembles the full prompt string to send to Gemini."""
    parts = [
        SYSTEM_PROMPT,
        FEW_SHOT_BAD_EXAMPLE,
        FEW_SHOT_GOOD_EXAMPLE,
    ]

    if prior_error:
        parts.append(_RETRY_TEMPLATE.format(prior_error=prior_error))

    parts.append(f"## Optimization Goal\n{goal}\n")
    parts.append(f"## Successful Match Patterns (JSON)\n```json\n{success_patterns_json}\n```\n")
    parts.append(f"## Failed Match Patterns (JSON)\n```json\n{failure_patterns_json}\n```\n")
    parts.append(f"## Available Skills (JSON)\n```json\n{available_skills_json}\n```\n")
    parts.append(
        "Now produce your ## Reasoning Trace and the ```yaml flow block."
    )

    return "\n".join(parts)


def extract_yaml_block(text: str) -> str | None:
    """Returns the content inside the first ```yaml fence, or None if absent."""
    match = re.search(YAML_EXTRACTION_PATTERN, text, re.DOTALL)
    return match.group(1).strip() if match else None


def extract_reasoning_trace(text: str) -> str:
    """Returns everything before the first ```yaml fence as the reasoning trace."""
    fence_start = text.find("```yaml")
    if fence_start == -1:
        return text.strip()
    return text[:fence_start].strip()


# --------------------------------------------------------------------------- #
# High-level API — consumed by generator.py and nodes.py (P1 wiring)          #
# --------------------------------------------------------------------------- #

def build_planner_prompt(goal: str, context: "RetrievedContext") -> str:
    """
    Assembles the full planner prompt from a RetrievedContext.
    Wraps build_prompt() so callers only need (goal, context).
    """
    return build_prompt(
        goal=goal,
        success_patterns_json=json.dumps(context.success_patterns, indent=2),
        failure_patterns_json=json.dumps(context.failure_patterns, indent=2),
        available_skills_json=json.dumps(context.available_skills, indent=2),
    )


_CRITIC_TEMPLATE = """\
You are the Critic agent for EcoLink NeuroCore. Review the proposed flow YAML.

== Optimization Goal ==
{goal}

== Proposed Flow YAML ==
{proposed_yaml}

== Historical Success Patterns ==
```json
{success_json}
```

== Historical Failure Patterns ==
```json
{failure_json}
```

== Available Skills (reference for hallucination check) ==
```json
{skills_json}
```

== Infrastructure Status ==
```json
{infra_json}
```

Evaluate the proposed flow on these criteria:
1. Every skill referenced exists in the Available Skills list.
2. The flow addresses the identified failure patterns and avoids repeating them.
3. The flow leverages patterns from the success examples.
4. Steps are logically ordered for a matching/recommendation system.
5. No overloaded servers are targeted (load < 80%, error_rate < 3%).

Return a structured critique: is_valid (bool), issues (list of strings), suggestions (string).
"""


def build_critic_prompt(
    proposed_yaml: str,
    context: "RetrievedContext",
    goal: str = "",
) -> str:
    """Builds the critic validation prompt from a RetrievedContext."""
    return _CRITIC_TEMPLATE.format(
        goal=goal or "(no goal specified)",
        proposed_yaml=proposed_yaml,
        success_json=json.dumps(context.success_patterns, indent=2),
        failure_json=json.dumps(context.failure_patterns, indent=2),
        skills_json=json.dumps(context.available_skills, indent=2),
        infra_json=json.dumps(context.infra_status, indent=2),
    )
