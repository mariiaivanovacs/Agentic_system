from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from src.graphrag.models import RetrievedContext


YAML_EXTRACTION_PATTERN = r"```yaml\n(.*?)\n```"


def _j(value) -> str:
    return json.dumps(value, indent=2, default=str)


def build_agent_planner_prompt(
    goal: str,
    context: RetrievedContext,
    software_nodes: Optional[List[Dict]] = None,
) -> str:
    """Planner prompt for the existing LangGraph PlannerOutput schema."""
    codebase_nodes = software_nodes if software_nodes is not None else context.software_nodes
    return f"""You are the Planner agent for EcoLink NeuroCore.

You MUST reason from the retrieved GraphRAG context below. Do not invent graph facts.

Goal: {goal}
Target industry: {context.industry}
Industry baseline score: {context.baseline_score} / 10

== Codebase Evidence ==
Real routes, functions, services, and data stores from the connected project.
Ground your hypothesis in these nodes before referencing pattern data below.
{_j(codebase_nodes)}

== GraphRAG: Industry performance, worst first ==
{_j(context.industry_stats)}

== GraphRAG: Failure subgraph for {context.industry} ==
These are low-scoring Company -> Mentor matches. Identify the skill/pain-point mismatch.
{_j(context.failure_patterns)}

== GraphRAG: Success subgraph for {context.industry} ==
These are high-scoring Company -> Mentor matches. Extract the success pattern.
{_j(context.success_patterns)}

== GraphRAG: Active flows and their current skills ==
{_j(context.active_flows)}

== GraphRAG: Available skills ==
The Generator may only use skills from this graph inventory.
{_j(context.available_skills)}

== GraphRAG: Infrastructure status ==
{_j(context.infra_status)}

== GraphRAG: Optional website/code entities ==
{_j(context.website_entities)}

== GraphRAG: Past learning events ==
{_j(context.learning_events) if context.learning_events else "None yet."}

Tasks:
1. Identify the root cause of poor matching in {context.industry}.
2. Identify which active flow is most responsible or most worth replacing.
3. Form a specific, testable hypothesis using graph evidence.
4. Use baseline_score={context.baseline_score}; do not hallucinate it.

Return PlannerOutput fields only: hypothesis, identified_problem_flow, baseline_score, reasoning."""


def build_critic_prompt(proposed_yaml: str, context: RetrievedContext, goal: str = "") -> str:
    return f"""You are the Critic agent for EcoLink NeuroCore.

Goal: {goal or context.goal}
Target industry: {context.industry}

== Proposed flow YAML ==
{proposed_yaml}

== GraphRAG failure patterns the proposal should address ==
{_j(context.failure_patterns)}

== GraphRAG success patterns the proposal should learn from ==
{_j(context.success_patterns)}

== Valid skills from Graph B ==
{_j(context.available_skills)}

== Valid connectors from Graph B ==
{_j(context.available_connectors)}

== Connected software project facts ==
Reject proposals that are not grounded in these real codebase facts when project facts exist.
{_j(context.software_nodes)}

== Infrastructure status ==
{_j(context.infra_status)}

Evaluate:
1. Every referenced skill exists.
2. Every referenced connector exists.
3. The selected runtime is healthy.
4. The flow addresses the failure subgraph and uses the success pattern.
5. The steps are logically ordered for matching/recommendation.

Return CriticOutput fields only: is_valid, issues, suggestions."""


def extract_yaml_block(text: str) -> str | None:
    match = re.search(YAML_EXTRACTION_PATTERN, text, re.DOTALL)
    return match.group(1).strip() if match else None


def extract_reasoning_trace(text: str) -> str:
    fence_start = text.find("```yaml")
    return text.strip() if fence_start == -1 else text[:fence_start].strip()
