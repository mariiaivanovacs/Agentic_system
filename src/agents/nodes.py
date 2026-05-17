"""
The five core nodes of the EcoLink NeuroCore agentic brain plus the
Human Approval interrupt node.

Node execution order (see graph.py for the wiring):
  planner → generator → critic ─(pass)→ simulator → evaluator
                  ↑                                      │
                  └──────────(fail, retry < 3)───────────┘
                                                         │(success)
                                                   human_approval
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

# Make ecolink-graph importable so nodes can call queries.py functions directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ecolink-graph"))
import queries as graph_queries  # noqa: E402

from src.agents.flow_utils import _extract_flow_references, _normalise_flow_def
from src.agents.state import AgentState
from src.agents.tools import (
    _code_sandbox,
    activate_proposal,
    get_infrastructure_status,
    log_execution_trace,
    propose_change,
    propose_skill_update,
    query_graph,
    query_graph_semantic,
    reject_proposal,
    simulate_flow,
)
from src.graphrag.prompt_engine import build_agent_planner_prompt, build_critic_prompt
from src.graphrag.retriever import retrieve_context
from src.realtime.event_bus import publish_event

_KNOWN_INDUSTRIES = ["Fintech", "Healthtech", "E-commerce", "Logistics", "SaaS", "Edtech"]

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
IMPROVEMENT_THRESHOLD = 1.1  # simulation must beat baseline by this factor


def _emit_node_event(
    state: AgentState,
    *,
    source: str,
    event_type: str,
    title: str,
    detail: str = "",
    target: str = "",
    payload: dict | None = None,
) -> None:
    publish_event(
        thread_id=state.get("thread_id", "system"),
        source=source,
        target=target,
        event_type=event_type,
        title=title,
        detail=detail,
        payload=payload or {},
    )


# --------------------------------------------------------------------------- #
# LLM factory                                                                  #
# --------------------------------------------------------------------------- #

def _llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.environ["GOOGLE_API_KEY"],
        temperature=0.2,
    )


# --------------------------------------------------------------------------- #
# Structured output schemas                                                    #
# --------------------------------------------------------------------------- #

class PlannerOutput(BaseModel):
    hypothesis: str = Field(description="Specific, testable hypothesis for improvement")
    identified_problem_flow: str = Field(description="Flow ID with the lowest performance")
    baseline_score: float = Field(description="Average historical match score for that flow")
    reasoning: str = Field(description="Step-by-step reasoning that led to this hypothesis")


class CodePatch(BaseModel):
    file_path: str = Field(description="Relative path of the file to modify within source_path")
    old_code: str = Field(description="Exact string to find and replace (empty string = new file)")
    new_code: str = Field(description="Replacement content")
    description: str = Field(description="What this patch does and why")


class RecommendedAction(BaseModel):
    action_type: str = Field(
        description=(
            "One of: create_skill | modify_workflow | modify_code | add_validation | "
            "add_observability | flag_risk | request_admin_approval | request_schema_extension"
        )
    )
    target_node_id: str = Field(description="ID of the graph node this action applies to")
    evidence_node_ids: List[str] = Field(
        description="IDs of project graph nodes that justify this action"
    )
    description: str = Field(description="What this action does and why")
    flow_yaml: Optional[str] = Field(
        default=None,
        description="Complete YAML flow definition — only for modify_workflow actions",
    )
    code_patch: Optional[CodePatch] = Field(
        default=None,
        description="File patch to apply in the isolated code sandbox — only for modify_code actions",
    )
    schema_label: Optional[str] = Field(
        default=None,
        description="Proposed graph node label — only for request_schema_extension actions",
    )
    schema_required_fields: Optional[List[str]] = Field(
        default=None,
        description="Required fields for the proposed node label",
    )
    schema_optional_fields: Optional[List[str]] = Field(
        default=None,
        description="Optional fields for the proposed node label",
    )


class GeneratorOutput(BaseModel):
    recommended_actions: List[RecommendedAction] = Field(
        description="Ordered list of recommended actions to address the hypothesis"
    )
    hypothesis_tested: str = Field(description="The hypothesis this output is testing")


class CriticOutput(BaseModel):
    is_valid: bool = Field(description="True if the YAML passes all checks")
    issues: List[str] = Field(description="List of concrete problems found (empty if valid)")
    suggestions: str = Field(description="Recommendations for the Generator if is_valid is False")


class EvaluatorOutput(BaseModel):
    reason: str = Field(description="Explanation of the evaluation result")
    updated_hypothesis: Optional[str] = Field(
        default=None,
        description="Revised hypothesis when simulation did not beat baseline",
    )


# --------------------------------------------------------------------------- #
# Retry wrapper for structured LLM calls                                       #
# --------------------------------------------------------------------------- #

def _structured_invoke(llm: ChatGoogleGenerativeAI, prompt: str, schema):
    # The google-genai SDK already has built-in exponential-backoff retry for
    # transient errors and 429s.
    return llm.with_structured_output(schema).invoke(prompt)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _extract_industry(goal: str, industry_stats: List[Dict]) -> str:
    """Extract industry from goal string; fall back to worst-performing industry."""
    goal_lower = goal.lower()
    for ind in _KNOWN_INDUSTRIES:
        if ind.lower() in goal_lower:
            return ind
    if industry_stats:
        return industry_stats[0]["industry"]
    return "Fintech"


# --------------------------------------------------------------------------- #
# Node 1 — Planner                                                             #
# --------------------------------------------------------------------------- #

def planner_node(state: AgentState) -> dict:
    """GraphRAG-backed ecosystem planner."""
    goal = state["goal"]
    app_id = state.get("app_id") or ""
    app_name = state.get("app_name") or ""
    selected_project_id = state.get("project_id") or ""
    selected_business_flow_id = state.get("business_flow_id") or ""

    _emit_node_event(
        state,
        source="planner",
        target="generator",
        event_type="thinking",
        title="Planner is reading graph evidence",
        detail=goal,
        payload={
            "project_id": selected_project_id,
            "business_flow_id": selected_business_flow_id,
            "proposal_only": bool(state.get("proposal_only")),
        },
    )

    # Fetch project-scoped codebase evidence via the retriever (single parameterized query).
    # business_flow_context is still queried separately because it needs ordered steps.
    context = retrieve_context(
        goal=goal,
        project_id=selected_project_id or None,
        app_id=app_id or None,
    )

    # software_nodes starts from retriever output; augmented below with BusinessFlow detail.
    software_nodes: List[Dict] = list(context.software_nodes)

    # Resolve project_id from retrieved Project node, fall back to app_id
    project_node = next((n for n in software_nodes if n.get("_label") == "Project"), None)
    project_id = selected_project_id or (project_node.get("id") if project_node else (app_id or None))

    business_flow_context: Optional[Dict] = None
    if selected_business_flow_id:
        try:
            _bfc_rows = query_graph.invoke({
                "cypher_query": f"""
                MATCH (bf:BusinessFlow {{id: {json.dumps(selected_business_flow_id)}}})
                OPTIONAL MATCH (bf)-[hs:HAS_STEP]->(step:FlowStep)
                OPTIONAL MATCH (step)-[:USES_PRIMITIVE]->(primitive)
                WITH bf, hs, step, primitive
                ORDER BY coalesce(hs.order, step.order), step.name
                RETURN elementId(bf) AS business_flow_element_id,
                       bf.id AS business_flow_id,
                       bf.name AS business_flow,
                       bf.project_id AS project_id,
                       bf.entrypoint AS entrypoint,
                       bf.flow_type AS flow_type,
                       bf.confidence AS confidence,
                       bf.evidence_summary AS evidence_summary,
                       bf.source_paths AS source_paths,
                       collect({{
                           element_id: elementId(step),
                           id: step.id,
                           name: step.name,
                           order: coalesce(hs.order, step.order),
                           step_type: step.step_type,
                           evidence: step.evidence,
                           primitive_element_id: elementId(primitive),
                           primitive_id: primitive.id,
                           primitive_name: primitive.name,
                           primitive_label: labels(primitive)[0],
                           primitive_source_path: primitive.source_path
                       }}) AS steps
                LIMIT 1
                """
            })
            if _bfc_rows:
                flow_record = _bfc_rows[0]
                business_flow_context = flow_record
                software_nodes.append({
                    "_label": "BusinessFlow",
                    "id": flow_record.get("business_flow_id"),
                    "name": flow_record.get("business_flow"),
                    "project_id": flow_record.get("project_id"),
                    "entrypoint": flow_record.get("entrypoint"),
                    "flow_type": flow_record.get("flow_type"),
                    "description": flow_record.get("evidence_summary"),
                    "steps": flow_record.get("steps", []),
                })
                project_id = project_id or flow_record.get("project_id")
        except Exception as exc:
            logger.warning(
                "Could not load selected BusinessFlow %s: %s",
                selected_business_flow_id,
                exc,
            )

    # Guard: without any Skill nodes the generator will invent IDs the Critic rejects,
    # causing a guaranteed 3-retry failure loop with no useful output.
    if not context.available_skills:
        logger.error(
            "Graph B has no Skill nodes — aborting optimization (goal=%r). "
            "Populate the skill inventory before running the agent.",
            goal,
        )
        _emit_node_event(
            state,
            source="planner",
            target="generator",
            event_type="error",
            title="Aborted: empty skill inventory",
            detail=(
                "Graph B contains no Skill nodes. The agent cannot produce grounded "
                "flow proposals without a skill inventory. "
                "Run the indexer or create Skill nodes in Neo4j, then re-run."
            ),
        )
        return {
            "messages": [
                AIMessage(content=(
                    "Optimization aborted: Graph B has no Skill nodes.\n"
                    "The agent cannot generate grounded proposals without a skill inventory.\n"
                    "Fix: run `python -m src.indexer.runner` or add Skill nodes to Neo4j, "
                    "then re-run the agent."
                ))
            ],
            "final_output": "aborted: empty skill inventory in Graph B",
        }

    # Semantic GraphRAG augmentation — non-fatal
    semantic_query = f"{goal} {app_name}" if app_name else f"{goal} {context.industry}"
    try:
        semantic_skills = query_graph_semantic.invoke({
            "query_text": semantic_query,
            "top_k": 5,
        })
    except Exception:
        semantic_skills = []

    semantic_section = ""
    if semantic_skills:
        lines = "\n".join(
            f"  - {s.get('name', s.get('id', '?'))} [{s.get('label', '')}] "
            f"score={s.get('score', 0):.3f}: {str(s.get('description', ''))[:120]}"
            for s in semantic_skills
        )
        semantic_section = f"\n\n== Semantically relevant skills (GraphRAG) ==\n{lines}"

    prompt = (
        build_agent_planner_prompt(
            goal, context,
            software_nodes=software_nodes,
            business_flow_context=business_flow_context,
        )
        + semantic_section
    )

    output: PlannerOutput = _structured_invoke(_llm(), prompt, PlannerOutput)

    # Ensure baseline comes from Graph A, not LLM hallucination
    problem_flow = output.identified_problem_flow
    if not problem_flow and context.active_flows:
        problem_flow = context.active_flows[0]["flow_id"]

    _emit_node_event(
        state,
        source="planner",
        target="generator",
        event_type="message",
        title="Planner formed hypothesis",
        detail=output.hypothesis,
        payload={
            "goal_industry": context.industry,
            "baseline_score": context.baseline_score,
            "identified_problem_flow": problem_flow,
            "graphrag": {
                "failure_patterns": len(context.failure_patterns),
                "success_patterns": len(context.success_patterns),
                "available_skills": len(context.available_skills),
                "active_flows": len(context.active_flows),
            },
        },
    )

    return {
        "messages": [
            HumanMessage(content=f"Goal: {goal}"),
            AIMessage(content=(
                f"Industry: {context.industry} | Baseline: {context.baseline_score}\n"
                f"Hypothesis: {output.hypothesis}\n"
                f"Problem flow: {problem_flow}\n"
                f"Reasoning: {output.reasoning}"
            )),
        ],
        "current_hypothesis": output.hypothesis,
        "identified_problem_flow": problem_flow,
        "baseline_score": context.baseline_score,
        "goal_industry": context.industry,
        "failure_patterns": context.failure_patterns[:5],
        "success_patterns": context.success_patterns[:5],
        "software_nodes": software_nodes,
        "project_id": project_id,
        "business_flow_id": selected_business_flow_id or None,
        "business_flow_context": business_flow_context,
    }


# --------------------------------------------------------------------------- #
# Node 2 — Generator                                                           #
# --------------------------------------------------------------------------- #

def _propose_unknown_skills(unknown_ids: set[str], goal_industry: str) -> None:
    """Write a SkillProposal node for each skill the LLM referenced but doesn't exist."""
    for skill_id in sorted(unknown_ids):
        try:
            graph_queries.create_skill_proposal(
                skill_id=skill_id,
                name=skill_id.replace("_", " ").title(),
                purpose=f"Proposed by generator for {goal_industry} flow optimization",
                input_schema="{}",
                output_schema="{}",
                proposed_by="generator",
            )
            logger.info("SkillProposal created for unknown skill: %s", skill_id)
        except Exception as exc:
            logger.warning("Could not write SkillProposal for %s: %s", skill_id, exc)


def generator_node(state: AgentState) -> dict:
    """Ecosystem-architect generator.

    Receives historical failure patterns (pain points, mentor skill gaps) from
    the Planner and proposes a set of RecommendedActions that address them.
    The primary modify_workflow action carries the YAML; proposed_flow_yaml is
    kept for backward compatibility with the Critic and Simulator nodes.
    When source_path is set, the generator also produces modify_code actions
    with concrete file patches for the code sandbox.
    """
    hypothesis = state.get("current_hypothesis", "")
    critic_feedback = state.get("critic_feedback", "")
    problem_flow = state.get("identified_problem_flow", "")
    goal_industry = state.get("goal_industry", "")
    failure_patterns = state.get("failure_patterns", [])
    success_patterns = state.get("success_patterns", [])
    software_nodes = state.get("software_nodes", [])
    source_path = state.get("source_path") or ""
    business_flow_context = state.get("business_flow_context")
    proposal_only = bool(state.get("proposal_only"))
    selected_business_flow = bool(business_flow_context)
    restrict_to_existing_capabilities = proposal_only or selected_business_flow

    _emit_node_event(
        state,
        source="generator",
        target="critic",
        event_type="thinking",
        title="Generator is designing candidate actions",
        detail=hypothesis or "Preparing proposal from graph evidence.",
        payload={
            "problem_flow": problem_flow,
            "business_flow_id": state.get("business_flow_id"),
            "proposal_only": proposal_only,
            "has_source_path": bool(source_path),
        },
    )

    # ── Graph B: valid skills with descriptions ───────────────────────────────
    valid_skills: List[Dict] = query_graph.invoke({
        "cypher_query": (
            "MATCH (s:Skill) "
            "RETURN s.id AS id, s.name AS name, s.description AS description, "
            "       s.performance_score AS score"
        )
    })
    valid_connectors: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (c:Connector) RETURN c.id AS id, c.name AS name, c.type AS type"
    })

    infra: Dict = get_infrastructure_status.invoke({})
    healthy_servers = [
        sid for sid, stats in infra.items()
        if stats["load"] < 0.80 and stats["error_rate"] < 0.03
    ]
    preferred_server = healthy_servers[0] if healthy_servers else "srv_002"

    # Structured retry context takes priority over freetext critic_feedback.
    # When retry_context is set (from Critic or Evaluator), render it as JSON
    # so the LLM receives exact, actionable constraints rather than prose.
    retry_context = state.get("retry_context") or {}
    if retry_context:
        feedback_section = (
            f"\n== Structured retry context — you MUST address every item below ==\n"
            f"{json.dumps(retry_context, indent=2)}"
        )
    elif critic_feedback:
        feedback_section = (
            f"\n== Critic feedback — fix these issues before regenerating ==\n{critic_feedback}"
        )
    else:
        feedback_section = ""

    # Distil pain points and failed skill patterns from Graph A data
    pain_points = list({p.get("pain_points", "") for p in failure_patterns if p.get("pain_points")})
    failed_skills = list({str(p.get("skills", "")) for p in failure_patterns if p.get("skills")})[:3]
    winning_skills = list({str(p.get("skills", "")) for p in success_patterns if p.get("skills")})[:3]

    industry_slug = goal_industry.lower().replace("-", "").replace(" ", "") if goal_industry else "general"

    codebase_section = (
        f"\n== Codebase Evidence (connected project) ==\n"
        f"Use the 'element_id' value (not 'id') in target_node_id and evidence_node_ids — "
        f"that is the graph-native identifier the Critic validates with elementId(n).\n"
        f"{json.dumps(software_nodes[:20], indent=2)}"
        if software_nodes else ""
    )
    business_flow_section = (
        "\n== Selected BusinessFlow Evidence ==\n"
        f"{json.dumps(business_flow_context, indent=2)}\n"
        "Optimize this exact ordered BusinessFlow/FlowStep chain. Every recommended "
        "action must cite at least one ID from this selected flow or its primitive steps.\n"
        if business_flow_context else ""
    )

    code_patch_section = ""
    if proposal_only:
        code_patch_section = """
== Proposal-only mode ==
This run is for visualization and human review only.
Do NOT generate modify_code actions or code_patch payloads.
Do NOT claim that code has changed.
Recommend workflow, validation, observability, or risk-flag proposals only.
Do NOT generate create_skill actions. If a missing capability is genuinely required,
use request_admin_approval and explain the missing capability in plain language."""
    elif source_path and software_nodes:
        code_patch_section = f"""
== Code Modification (source_path is set: {source_path}) ==
You MAY also include modify_code actions alongside modify_workflow actions.
For modify_code actions, populate code_patch with:
  file_path: relative path within the project (e.g. "src/api/match.py")
  old_code: the exact string to find (or "" to create a new file)
  new_code: the replacement content
  description: what this change does
Use the Codebase Evidence nodes above to identify which files to modify.
Only propose changes you are confident about based on the graph evidence."""

    allowed_action_types = (
        "modify_workflow | add_validation | add_observability | flag_risk | request_admin_approval | request_schema_extension"
        if restrict_to_existing_capabilities
        else "create_skill | modify_workflow | modify_code | add_validation | add_observability | flag_risk | request_admin_approval | request_schema_extension"
    )
    missing_capability_instruction = (
        "Do not propose new skills for selected codebase BusinessFlows. Stay grounded in the existing "
        "BusinessFlow/FlowStep/primitive graph. If the existing graph lacks a capability, use "
        "request_admin_approval rather than create_skill. If the graph schema itself is missing a node type, "
        "use request_schema_extension with schema_label and fields; do not invent a live label."
        if restrict_to_existing_capabilities
        else "Do not invent new executable node types. If a missing capability is required, "
        "use create_skill/request_admin_approval. If a missing graph primitive type is required, "
        "use request_schema_extension so it remains a proposal until reviewed."
    )

    prompt = f"""You are an Ecosystem Architect for EcoLink, a mentor–startup matching platform.

Industry: {goal_industry}
Hypothesis to test: {hypothesis}
Flow to replace: {problem_flow}
{feedback_section}{codebase_section}{business_flow_section}{code_patch_section}

== WHY matches FAIL in {goal_industry} (Graph A evidence) ==
Company pain points that were NOT addressed:
{pain_points}

Mentor skill sets that failed to help:
{failed_skills}

== WHAT made matches SUCCEED in {goal_industry} ==
Mentor skill sets from high-scoring matches:
{winning_skills}

Success pattern examples:
{json.dumps(success_patterns[:3], indent=2)}

== Available matching skills — ONLY use IDs from this list ==
{json.dumps(valid_skills, indent=2)}

== Valid connector IDs ==
{json.dumps([{"id": c["id"], "type": c["type"]} for c in valid_connectors], indent=2)}

== Healthy servers (pick one for runs_on) ==
{healthy_servers}

Generate recommended_actions to address the hypothesis. Each action MUST have:
- action_type: one of {allowed_action_types}
- target_node_id: an ID from the Codebase Evidence or graph above
- evidence_node_ids: IDs from the Codebase Evidence that justify the action
- description: what this action does and why
For request_schema_extension actions, set schema_label, schema_required_fields,
schema_optional_fields, and target_node_id to the Project or closest existing primitive.

In proposal-only mode, action_type MUST NOT be modify_code and MUST NOT include code_patch.

{missing_capability_instruction}

For the primary modify_workflow action include a complete flow_yaml that:
1. Has flow_id: flow_proposal_{industry_slug}_v<N>
2. Uses ONLY skill IDs from "Available matching skills"
3. Specifies runs_on: {preferred_server}
4. Has a description naming the specific pain point it targets
5. Orders steps to address the failure pattern: assess pain_points → match semantically → score
6. Targets MATCH QUALITY improvement (outcome_score), not server latency

Return GeneratorOutput: recommended_actions list + hypothesis_tested."""

    output: GeneratorOutput = _structured_invoke(_llm(), prompt, GeneratorOutput)

    if restrict_to_existing_capabilities:
        output.recommended_actions = [
            action
            for action in output.recommended_actions
            if action.action_type not in {"modify_code", "create_skill"}
        ]

    # Extract the first modify_workflow action's YAML for backward-compat fields
    modify_action = next(
        (a for a in output.recommended_actions if a.action_type == "modify_workflow" and a.flow_yaml),
        None,
    )
    flow_yaml = modify_action.flow_yaml if modify_action else ""

    # Surface any skills the LLM invented that don't exist in Graph B,
    # and capture ALL referenced skill IDs so the evaluator can update metrics.
    valid_skill_ids = {s["id"] for s in valid_skills}
    referenced_skills: set[str] = set()
    if flow_yaml:
        try:
            parsed = yaml.safe_load(flow_yaml)
            if isinstance(parsed, dict):
                flow_def = _normalise_flow_def(parsed)
                referenced_skills, _ = _extract_flow_references(flow_def)
                unknown_referenced = referenced_skills - valid_skill_ids
                if unknown_referenced:
                    if restrict_to_existing_capabilities:
                        logger.warning(
                            "Generator referenced unknown skills %s in selected BusinessFlow/proposal-only mode; "
                            "not writing SkillProposal nodes.",
                            unknown_referenced,
                        )
                    else:
                        logger.warning(
                            "Generator referenced unknown skills %s — writing SkillProposals.",
                            unknown_referenced,
                        )
                        _propose_unknown_skills(unknown_referenced, goal_industry)
        except yaml.YAMLError:
            pass

    action_types = [a.action_type for a in output.recommended_actions]
    _emit_node_event(
        state,
        source="generator",
        target="critic",
        event_type="message",
        title="Generator drafted actions",
        detail=f"Actions: {action_types}",
        payload={
            "recommended_actions": len(output.recommended_actions),
            "flow_yaml": flow_yaml,
        },
    )

    return {
        "messages": [
            AIMessage(content=(
                f"Generated {len(output.recommended_actions)} action(s) for {goal_industry}\n"
                f"Hypothesis: {output.hypothesis_tested}\n"
                f"Action types: {action_types}"
            ))
        ],
        "proposed_flow_yaml": flow_yaml,
        "recommended_actions": [a.model_dump() for a in output.recommended_actions],
        "skills_referenced": sorted(referenced_skills),
    }


# --------------------------------------------------------------------------- #
# Node 3 — Critic                                                              #
# --------------------------------------------------------------------------- #

def critic_node(state: AgentState) -> dict:
    """
    Validates the proposed YAML for syntax, valid skill/connector references,
    and infrastructure health before allowing it to proceed to simulation.
    Also validates that each recommended_action's evidence_node_ids reference
    real nodes in the graph.
    """
    flow_yaml = state.get("proposed_flow_yaml", "")
    retry_count = state.get("retry_count", 0)
    project_id = state.get("project_id")

    _emit_node_event(
        state,
        source="critic",
        target="simulator",
        event_type="thinking",
        title="Critic is validating proposal safety",
        detail="Checking YAML, referenced skills, infrastructure, and graph evidence.",
        payload={
            "retry_count": retry_count,
            "project_id": project_id,
            "recommended_actions": len(state.get("recommended_actions", [])),
        },
    )

    # Step 1: parse YAML locally
    syntax_error: Optional[str] = None
    flow_def: dict = {}
    try:
        parsed = yaml.safe_load(flow_yaml)
        if isinstance(parsed, dict):
            flow_def = _normalise_flow_def(parsed)
        else:
            syntax_error = "YAML must parse to a mapping/object."
    except yaml.YAMLError as exc:
        syntax_error = str(exc)

    # Step 2: load valid records from Graph B + approved SkillProposals.
    # Fetch full name/description so the same records feed both validation and the LLM prompt,
    # eliminating the need for a second retrieve_context() call later.
    valid_skill_records: List[Dict] = query_graph.invoke({
        "cypher_query": (
            "MATCH (s:Skill) "
            "RETURN s.id AS id, s.name AS name, s.description AS description, "
            "       s.performance_score AS performance_score"
        )
    })
    approved_proposals: List[Dict] = query_graph.invoke({
        "cypher_query": (
            "MATCH (s:SkillProposal {status: 'approved'}) "
            "RETURN s.id AS id, s.name AS name"
        )
    })
    valid_skills = {r["id"] for r in valid_skill_records} | {r["id"] for r in approved_proposals}

    valid_conn_records: List[Dict] = query_graph.invoke({
        "cypher_query": (
            "MATCH (c:Connector) "
            "RETURN c.id AS id, c.name AS name, c.type AS type, c.status AS status"
        )
    })
    valid_connectors = {r["id"] for r in valid_conn_records}

    infra: Dict = get_infrastructure_status.invoke({})
    infra_summary = {
        sid: f"load={s['load']:.0%} error_rate={s['error_rate']:.1%}"
        for sid, s in infra.items()
    }

    unknown_skills: List[str] = []
    unknown_connectors: List[str] = []
    local_issues: List[str] = []
    if syntax_error:
        local_issues.append(f"YAML syntax/schema error: {syntax_error}")
    else:
        referenced_skills, referenced_connectors = _extract_flow_references(flow_def)
        unknown_skills = sorted(referenced_skills - valid_skills)
        unknown_connectors = sorted(referenced_connectors - valid_connectors)
        if unknown_skills:
            local_issues.append(f"Unknown skill IDs: {unknown_skills}")
        if unknown_connectors:
            local_issues.append(f"Unknown connector IDs: {unknown_connectors}")

        runs_on = flow_def.get("runs_on")
        if not runs_on:
            local_issues.append("Missing required runs_on server ID.")
        elif runs_on not in infra:
            local_issues.append(f"Unknown runs_on server ID: {runs_on}")
        else:
            stats = infra[runs_on]
            if stats["load"] >= 0.80 or stats["error_rate"] >= 0.03:
                local_issues.append(
                    f"Server {runs_on} is not healthy "
                    f"(load={stats['load']:.0%}, error_rate={stats['error_rate']:.1%})."
                )

        if not referenced_skills:
            local_issues.append("No skills found in steps[*].skill or steps[*].skill_id.")

    # Step 3: validate evidence_node_ids and target_node_id for each recommended_action.
    # Primary check uses elementId(n) (graph-native, as spec requires).
    # Fallback to n.id for property-based IDs from older planner queries.
    valid_evidence: List[str] = []
    for action in state.get("recommended_actions", []):
        for nid in action.get("evidence_node_ids", []):
            if not nid:
                continue
            try:
                scope_clause = ""
                if project_id:
                    project_json = json.dumps(project_id)
                    scope_clause = (
                        "AND (n.project_id = "
                        f"{project_json} OR n.id = {project_json} "
                        "OR EXISTS { MATCH (:Project {id: "
                        f"{project_json}"
                        "})-[:HAS_BUSINESS_FLOW]->(:BusinessFlow)-[:HAS_STEP]->(n) } "
                        "OR EXISTS { MATCH (:Project {id: "
                        f"{project_json}"
                        "})-[:HAS_BUSINESS_FLOW]->(:BusinessFlow)-[:HAS_STEP]->(:FlowStep)-[:USES_PRIMITIVE]->(n) })"
                    )
                nid_json = json.dumps(nid)
                result = query_graph.invoke({
                    "cypher_query": (
                        "MATCH (n) "
                        f"WHERE (elementId(n) = {nid_json} OR n.id = {nid_json}) "
                        f"{scope_clause} "
                        "RETURN coalesce(n.id, elementId(n)) AS eid LIMIT 1"
                    )
                })
                if result:
                    if nid not in valid_evidence:
                        valid_evidence.append(nid)
                else:
                    local_issues.append(
                        f"Action '{action.get('action_type', '?')}' cites a node outside the selected project or a non-existent node: {nid}"
                    )
            except Exception as exc:
                logger.warning("Could not verify evidence node %s: %s", nid, exc)

        # Also verify target_node_id exists — the generator sees only software_nodes[:20]
        # so it can reference nodes it cannot see for large codebases.
        target_id = action.get("target_node_id", "")
        if target_id:
            try:
                scope_clause = ""
                if project_id:
                    project_json = json.dumps(project_id)
                    scope_clause = (
                        "AND (n.project_id = "
                        f"{project_json} OR n.id = {project_json} "
                        "OR EXISTS { MATCH (:Project {id: "
                        f"{project_json}"
                        "})-[:HAS_BUSINESS_FLOW]->(:BusinessFlow)-[:HAS_STEP]->(n) } "
                        "OR EXISTS { MATCH (:Project {id: "
                        f"{project_json}"
                        "})-[:HAS_BUSINESS_FLOW]->(:BusinessFlow)-[:HAS_STEP]->(:FlowStep)-[:USES_PRIMITIVE]->(n) })"
                    )
                tid_json = json.dumps(target_id)
                target_result = query_graph.invoke({
                    "cypher_query": (
                        "MATCH (n) "
                        f"WHERE (elementId(n) = {tid_json} OR n.id = {tid_json}) "
                        f"{scope_clause} "
                        "RETURN coalesce(n.id, elementId(n)) AS eid LIMIT 1"
                    )
                })
                if not target_result:
                    local_issues.append(
                        f"Action '{action.get('action_type', '?')}' has an invalid "
                        f"target_node_id (not found in graph): {target_id}"
                    )
            except Exception as exc:
                logger.warning("Could not verify target_node_id %s: %s", target_id, exc)

    if local_issues:
        suggestions = (
            "Regenerate the YAML using only valid skill IDs from Graph B, "
            "a healthy runs_on server, steps[*].skill or steps[*].skill_id references, "
            "and evidence_node_ids that exist in the graph."
        )
        _emit_node_event(
            state,
            source="critic",
            target="generator",
            event_type="decision",
            title="Critic rejected flow locally",
            detail="; ".join(local_issues),
            payload={
                "issues": local_issues,
                "suggestions": suggestions,
                "retry_count": retry_count + 1,
                "invalid_skills": list(unknown_skills),
                "invalid_connectors": list(unknown_connectors),
                "critic_path": "deterministic",
            },
        )
        return {
            "messages": [
                AIMessage(content=f"Critic result: FAIL\nIssues: {local_issues}")
            ],
            "critic_passed": False,
            "critic_feedback": suggestions,
            "retry_context": {
                "invalid_skills": unknown_skills,
                "invalid_connectors": unknown_connectors,
                "forbidden_pattern": local_issues,
                "required_change": suggestions,
            },
            "retry_count": retry_count + 1,
        }

    # No-evidence guard: if there are no recommended_actions but there IS a
    # flow_yaml, the proposal has zero graph grounding. Add this requirement
    # to the LLM prompt and enforce it post-LLM.
    no_actions = not state.get("recommended_actions")
    evidence_required_note = ""
    if no_actions and flow_yaml:
        evidence_required_note = (
            "\n\n== Evidence grounding required ==\n"
            "This flow has no recommended_actions with evidence_node_ids. "
            "You MUST set is_valid=False unless you can identify at least one "
            "concrete graph node that justifies why this flow addresses the stated goal. "
            "A flow with no graph grounding must be rejected."
        )

    # Build the LLM prompt from state (already populated by the planner's retrieve_context call)
    # plus the skill/connector records fetched above. This avoids a redundant Neo4j round-trip.
    prompt = build_critic_prompt(
        flow_yaml,
        goal=state.get("goal", ""),
        failure_patterns=state.get("failure_patterns", []),
        success_patterns=state.get("success_patterns", []),
        available_skills=valid_skill_records + approved_proposals,
        available_connectors=valid_conn_records,
        software_nodes=state.get("software_nodes", []),
        infra_status=infra,
        industry=state.get("goal_industry", ""),
    )
    prompt += (
        "\n\n== Local deterministic checks already passed ==\n"
        f"Valid skill IDs: {sorted(valid_skills)}\n"
        f"Valid connector IDs: {sorted(valid_connectors)}\n"
        f"Server infrastructure status: {json.dumps(infra_summary, indent=2)}\n"
    )
    prompt += evidence_required_note

    output: CriticOutput = _structured_invoke(_llm(), prompt, CriticOutput)

    # Post-LLM safety override: if no recommended_actions provided any evidence
    # and the flow has no graph grounding, force rejection regardless of LLM output.
    if output.is_valid and no_actions and not valid_evidence:
        logger.warning(
            "Critic LLM returned is_valid=True but no evidence_node_ids were provided "
            "and recommended_actions is empty — forcing rejection."
        )
        output = CriticOutput(
            is_valid=False,
            issues=["Flow proposal has no graph grounding: no recommended_actions with evidence_node_ids."],
            suggestions="Provide recommended_actions with valid evidence_node_ids referencing real graph nodes.",
        )

    _emit_node_event(
        state,
        source="critic",
        target="simulator" if output.is_valid else "generator",
        event_type="decision",
        title="Critic passed flow" if output.is_valid else "Critic rejected flow",
        detail="No issues found." if output.is_valid else str(output.issues),
        payload={
            "critic_passed": output.is_valid,
            "evidence_node_ids": valid_evidence,
            "issues": output.issues if not output.is_valid else [],
            "suggestions": output.suggestions if not output.is_valid else "",
            "retry_count": retry_count + (0 if output.is_valid else 1),
            "critic_path": "llm",
        },
    )

    result: dict = {
        "messages": [
            AIMessage(content=(
                f"Critic result: {'PASS' if output.is_valid else 'FAIL'}\n"
                + (f"Issues: {output.issues}" if not output.is_valid else "No issues found.")
            ))
        ],
        "critic_passed": output.is_valid,
        "critic_feedback": output.suggestions if not output.is_valid else "",
        "retry_count": retry_count + (0 if output.is_valid else 1),
    }
    if output.is_valid:
        result["critic_evidence_ids"] = valid_evidence
        result["retry_context"] = None          # clear stale retry context on pass
    else:
        result["retry_context"] = {
            "invalid_skills": unknown_skills,
            "forbidden_pattern": output.issues,
            "required_change": output.suggestions,
            "evidence_node_ids": valid_evidence,
        }
    return result


# --------------------------------------------------------------------------- #
# Node 4 — Simulator                                                           #
# --------------------------------------------------------------------------- #

def simulator_node(state: AgentState) -> dict:
    """Dispatches to the appropriate sandbox based on what actions are in state.

    Priority:
    1. If source_path is set AND there are modify_code actions with code_patch
       → run _code_sandbox() on an isolated temp copy of the codebase.
    2. Otherwise, if proposed_flow_yaml is non-empty
       → run simulate_flow() (existing flow sandbox).
    3. If neither → fail with a clear error.

    Also writes an ExecutionTrace to Neo4j after every simulation.
    """
    source_path = state.get("source_path") or ""
    recommended_actions = state.get("recommended_actions", [])
    flow_yaml = state.get("proposed_flow_yaml", "")
    problem_flow_id = state.get("identified_problem_flow", "")
    app_id = state.get("app_id") or ""

    _emit_node_event(
        state,
        source="simulator",
        target="evaluator",
        event_type="thinking",
        title="Simulator is preparing sandbox execution",
        detail="Choosing code sandbox, flow sandbox, or graph-review validation.",
        payload={
            "source_path": source_path,
            "has_flow_yaml": bool(flow_yaml),
            "recommended_actions": len(recommended_actions),
            "proposal_only": bool(state.get("proposal_only")),
        },
    )

    # Collect code patches from modify_code actions
    code_actions = [
        a for a in recommended_actions
        if a.get("action_type") == "modify_code" and a.get("code_patch")
    ]

    if state.get("proposal_only") and state.get("business_flow_id"):
        flow_context = state.get("business_flow_context")
        selected_flow = flow_context if isinstance(flow_context, dict) else {}
        steps = selected_flow.get("steps") if isinstance(selected_flow.get("steps"), list) else []
        evidence_ids = {
            evidence_id
            for action in recommended_actions
            if isinstance(action, dict)
            for evidence_id in action.get("evidence_node_ids", [])
            if evidence_id
        }
        confidence = float(selected_flow.get("confidence") or 0)
        result = {
            "status": "success" if recommended_actions and evidence_ids else "fail",
            "metrics": {
                "match_score": round(confidence * 10, 2) if confidence else 0.0,
                "review_confidence": round(confidence, 3) if confidence else 0.0,
                "grounded_actions": len(recommended_actions),
                "evidence_count": len(evidence_ids),
                "sample_size": len(steps),
                "validation_mode": "graph_review",
            },
            "error_log": None if recommended_actions and evidence_ids else (
                "Proposal-only BusinessFlow review needs at least one action with graph evidence."
            ),
            "traces": [
                {
                    "action_type": action.get("action_type"),
                    "target_node_id": action.get("target_node_id"),
                    "evidence_count": len(action.get("evidence_node_ids", [])),
                    "description": action.get("description"),
                }
                for action in recommended_actions
                if isinstance(action, dict)
            ],
        }
    elif source_path and code_actions:
        patches = [a["code_patch"] for a in code_actions]
        _emit_node_event(
            state,
            source="simulator",
            target="evaluator",
            event_type="message",
            title="Running code sandbox",
            detail=f"Applying {len(patches)} patch(es) to isolated copy of {source_path}",
            payload={"patch_count": len(patches), "source_path": source_path},
        )
        result: Dict = _code_sandbox(source_path, patches)
        if result.get("status") != "success" and flow_yaml:
            _emit_node_event(
                state,
                source="simulator",
                target="evaluator",
                event_type="message",
                title="Code sandbox patch failed; falling back to flow sandbox",
                detail=result.get("error_log", "Code patch did not apply cleanly."),
                payload={"code_sandbox_result": result},
            )
            snapshot_id = f"snapshot_{app_id}" if app_id else "snapshot_2025_q4"
            flow_result = simulate_flow.invoke({
                "flow_yaml": flow_yaml,
                "dataset_snapshot_id": snapshot_id,
            })
            flow_result["code_sandbox_result"] = result
            flow_result["sandbox_fallback"] = "flow"
            result = flow_result
    elif flow_yaml:
        snapshot_id = f"snapshot_{app_id}" if app_id else "snapshot_2025_q4"
        result = simulate_flow.invoke({
            "flow_yaml": flow_yaml,
            "dataset_snapshot_id": snapshot_id,
        })
    else:
        result = {
            "status": "fail",
            "metrics": {},
            "error_log": "No flow YAML or code patches to simulate.",
        }

    status = result.get("status", "fail")
    metrics = result.get("metrics", {})
    error_log = result.get("error_log")

    # Detect infrastructure errors — not flow logic failures, don't waste a retry
    infra_error = result.get("infra_error")
    if infra_error:
        _emit_node_event(
            state,
            source="simulator",
            target="evaluator",
            event_type="error",
            title=f"Infrastructure error: {infra_error['error_type']}",
            detail=infra_error["human_action"],
            payload={"infra_error": infra_error},
        )
        return {
            "messages": [
                AIMessage(content=(
                    f"INFRASTRUCTURE ERROR — {infra_error['error_type']}\n"
                    f"Action required: {infra_error['human_action']}\n"
                    f"Service: {infra_error.get('service', '')}"
                ))
            ],
            "simulation_results": [result],
            "infra_error": infra_error,
        }

    if problem_flow_id and not (state.get("proposal_only") and state.get("business_flow_id")):
        sim_score = metrics.get("match_score", 0.0) if status == "success" else 0.0
        # Collect skills_applied across all traces (any trace captures the full list)
        traces = result.get("traces", [])
        skills_applied = traces[0].get("skills_applied", []) if traces else []
        sandbox_baseline = metrics.get("sandbox_baseline_score")
        log_execution_trace(
            flow_id=problem_flow_id,
            result_score=sim_score,
            status=status,
            skills_applied=skills_applied,
            sandbox_baseline_score=sandbox_baseline,
        )

    msg = (
        f"Simulation {status.upper()}. Metrics: {metrics}"
        if status == "success"
        else f"Simulation FAILED. Error: {error_log}"
    )
    _emit_node_event(
        state,
        source="simulator",
        target="evaluator",
        event_type="result",
        title="Sandbox simulation completed",
        detail=msg,
        payload={"status": status, "metrics": metrics, "error_log": error_log},
    )

    return {
        "messages": [AIMessage(content=msg)],
        "simulation_results": [result],
    }


# --------------------------------------------------------------------------- #
# Node 5 — Evaluator                                                           #
# --------------------------------------------------------------------------- #

def evaluator_node(state: AgentState) -> dict:
    """
    Compares simulation results against the historical baseline.
    Decision is computed DETERMINISTICALLY after the LLM provides reasoning:
      success = sim_status == 'success' AND sim_score > baseline * IMPROVEMENT_THRESHOLD
    On success: calls propose_change and flags for human approval.
    On failure: updates the hypothesis and increments retry_count.
    """
    simulation_results = state.get("simulation_results", [])
    latest = simulation_results[-1] if simulation_results else {}
    baseline_score = state.get("baseline_score", 3.0)
    problem_flow = state.get("identified_problem_flow", "")
    hypothesis = state.get("current_hypothesis", "")
    retry_count = state.get("retry_count", 0)

    _emit_node_event(
        state,
        source="evaluator",
        target="human_approval",
        event_type="thinking",
        title="Evaluator is comparing result to decision rule",
        detail=hypothesis or "Reviewing sandbox metrics and graph evidence.",
        payload={
            "baseline_score": baseline_score,
            "retry_count": retry_count,
            "simulation_results": len(simulation_results),
        },
    )

    # Short-circuit: infrastructure errors are not flow failures — don't retry.
    # Set retry_count to MAX_RETRIES so _route_evaluator routes to END.
    latest_infra = (simulation_results[-1].get("infra_error") if simulation_results else None) \
                   or state.get("infra_error")
    if latest_infra:
        action = latest_infra.get("human_action", "Resolve the infrastructure issue and retry.")
        url = latest_infra.get("activation_url", "")
        detail = f"Infrastructure error blocked simulation. {action}"
        if url:
            detail += f" See: {url}"
        _emit_node_event(
            state,
            source="evaluator",
            target="human_approval",
            event_type="error",
            title=f"Infra error — {latest_infra.get('error_type', 'CLOUD_ERROR')}",
            detail=detail,
            payload={"infra_error": latest_infra},
        )
        return {
            "messages": [AIMessage(content=detail)],
            "simulation_succeeded": False,
            "human_approval_required": False,
            "final_output": detail,
            "infra_error": latest_infra,
            "retry_count": MAX_RETRIES,   # force END routing; infra errors are not retryable
        }

    # Prefer the within-sample random baseline (same snapshot, same scale) over
    # the historical baseline for the decision threshold.  The within-sample
    # baseline is stored in metrics.sandbox_baseline_score by sandbox_task.py.
    sim_score          = latest.get("metrics", {}).get("match_score", 0.0)
    sim_status         = latest.get("status", "fail")
    sandbox_baseline   = latest.get("metrics", {}).get("sandbox_baseline_score")
    effective_baseline = sandbox_baseline if sandbox_baseline is not None else baseline_score
    threshold          = effective_baseline * IMPROVEMENT_THRESHOLD
    comparison_note    = (
        f"within-sample random baseline ({effective_baseline})"
        if sandbox_baseline is not None
        else f"historical baseline ({baseline_score})"
    )
    proposal_only_business_flow = bool(state.get("proposal_only") and state.get("business_flow_id"))
    decision_rule = (
        "For this proposal-only BusinessFlow review, success means the critic passed, "
        "the simulation command completed, and at least one recommended action cites graph evidence. "
        "Do not reject only because the generic EcoLink matching sandbox score did not beat its baseline."
        if proposal_only_business_flow
        else f"The decision will be computed deterministically (sim_score > {round(threshold, 3)})."
    )
    evaluator_task = (
        "1. Explain the before-vs-proposed BusinessFlow recommendation in plain English (reason field).\n"
        "2. If the proposal lacks concrete graph evidence, propose an updated_hypothesis that stays grounded in the selected flow."
        if proposal_only_business_flow
        else (
            "1. Explain what the simulation result means in plain English (reason field).\n"
            "2. If the score did NOT beat the threshold, propose an updated_hypothesis that\n"
            "   takes a meaningfully different approach than the current one."
        )
    )

    # LLM provides reasoning and a revised hypothesis — does NOT make the decision.
    prompt = f"""You are the Evaluator agent for EcoLink NeuroCore.

Problem flow: {problem_flow}
Hypothesis under test: {hypothesis}
Historical baseline (GraphRAG): {baseline_score}
Comparison baseline used: {comparison_note}
Threshold to beat:  {round(threshold, 3)}  (= baseline × {IMPROVEMENT_THRESHOLD})

== Simulation result ==
{json.dumps(latest, indent=2)}

{decision_rule}
Your job is to:
{evaluator_task}

Do NOT include a decision field — that is computed automatically."""

    output: EvaluatorOutput = _structured_invoke(_llm(), prompt, EvaluatorOutput)

    # Deterministic decision — LLM cannot override this. Proposal-only
    # BusinessFlow runs are graph-review proposals, not EcoLink match-quality
    # experiments, so the generic matching sandbox score should not block a
    # grounded UI/codebase optimization proposal.
    if state.get("proposal_only") and state.get("business_flow_id"):
        has_grounded_actions = any(
            action.get("evidence_node_ids")
            for action in state.get("recommended_actions", [])
            if isinstance(action, dict)
        )
        decision = "success" if has_grounded_actions and sim_status == "success" else "failure"
    else:
        decision = (
            "success"
            if sim_status == "success" and sim_score > threshold
            else "failure"
        )
    logger.info(
        "Evaluator: reason='%s' | deterministic decision=%s "
        "(sim_score=%.2f, sim_status=%s, threshold=%.2f)",
        output.reason, decision, sim_score, sim_status, threshold,
    )

    updates: dict = {
        "messages": [
            AIMessage(content=f"Evaluation: {decision.upper()}. {output.reason}")
        ],
        "simulation_succeeded": decision == "success",
    }

    if decision == "success":
        flow_context = state.get("business_flow_context")
        selected_flow = flow_context if isinstance(flow_context, dict) else {}
        before_chain = selected_flow.get("ordered_chain") or selected_flow.get("steps") or ""
        before_summary = {
            "business_flow": selected_flow.get("business_flow") or problem_flow,
            "ordered_chain": before_chain,
            "baseline_score": baseline_score,
            "graph_evidence": selected_flow,
        }
        proposed_summary = {
            "title": "Proposed workflow optimization",
            "hypothesis": hypothesis,
            "proposal_mode": "visual_text_only" if state.get("proposal_only") else "sandbox_proposal",
            "code_mutation": "none",
            "recommended_actions": state.get("recommended_actions", []),
            "flow_yaml": state.get("proposed_flow_yaml", ""),
        }
        proposal_id = propose_change.invoke({
            "change_type": "new_flow",
            "details": {
                "yaml": state.get("proposed_flow_yaml", ""),
                "hypothesis": hypothesis,
                "simulation_score": sim_score,
                "baseline_score": baseline_score,
                "project_id": state.get("project_id"),
                "business_flow_id": state.get("business_flow_id"),
                "business_flow_context": flow_context,
                "recommended_actions": state.get("recommended_actions", []),
                "sandbox_result": latest,
                "proposal_mode": proposed_summary["proposal_mode"],
                "code_mutation": "none",
                "before_summary": before_summary,
                "proposed_summary": proposed_summary,
                "justification": output.reason,
            },
        })
        for action in state.get("recommended_actions", []):
            if action.get("action_type") != "request_schema_extension":
                continue
            try:
                schema_proposal_id = propose_change.invoke({
                    "change_type": "schema_extension",
                    "details": {
                        "label": action.get("schema_label") or action.get("description", "NewPrimitive"),
                        "required_fields": action.get("schema_required_fields") or ["id", "name"],
                        "optional_fields": action.get("schema_optional_fields") or [],
                        "reason": action.get("description") or "Agent requested a graph schema extension.",
                        "project_id": state.get("project_id"),
                        "relationship_examples": action.get("evidence_node_ids", []),
                    },
                })
                updates["messages"].append(
                    AIMessage(content=f"Schema extension proposal saved with ID: {schema_proposal_id}")
                )
            except Exception as exc:
                logger.warning("Could not save schema extension proposal: %s", exc)
        updates["proposal_id"] = proposal_id
        updates["human_approval_required"] = True
        updates["retry_context"] = None          # clear stale retry context on success
        updates["messages"].append(
            AIMessage(content=f"Proposal saved to Neo4j with ID: {proposal_id}")
        )
        
        # Propose skill updates based on simulation performance
        skills_used: List[str] = state.get("skills_referenced", [])
        for skill_id in skills_used:
            try:
                # Calculate improved metrics from simulation
                exec_time_ms = latest.get("metrics", {}).get("latency_ms", 0.0)
                if exec_time_ms > 0:
                    propose_skill_update.invoke({
                        "skill_id": skill_id,
                        "performance_score": min(10.0, 5.0 + (sim_score / 2.0)),  # Scale to 0-10
                        "avg_execution_ms": exec_time_ms,
                        "reason": f"Performance improvement from successful simulation (score={sim_score:.2f})",
                    })
                    logger.info(
                        "Proposed skill update for %s: execution_ms=%.2f, score=%.2f",
                        skill_id, exec_time_ms, sim_score
                    )
            except Exception as exc:
                logger.warning("Could not propose skill update for %s: %s", skill_id, exc)
        
        _emit_node_event(
            state,
            source="evaluator",
            target="human_approval",
            event_type="approval_required",
            title="Evaluator saved proposal",
            detail=output.reason,
            payload={
                "proposal_id": proposal_id,
                "decision": decision,
                "sim_score": sim_score,
                "baseline_score": baseline_score,
                "threshold": round(threshold, 3),
                "llm_reason": output.reason,
            },
        )
    else:
        updates["human_approval_required"] = False
        updates["retry_count"] = retry_count + 1
        updates["retry_context"] = {
            "failed_metric": {
                "match_score": sim_score,
                "sim_status": sim_status,
                "threshold": round(threshold, 3),
            },
            "updated_hypothesis": output.updated_hypothesis,
        }
        if output.updated_hypothesis:
            updates["current_hypothesis"] = output.updated_hypothesis
            updates["critic_feedback"] = ""
        _emit_node_event(
            state,
            source="evaluator",
            target="generator",
            event_type="decision",
            title="Evaluator requested retry",
            detail=output.reason,
            payload={
                "decision": decision,
                "retry_count": retry_count + 1,
                "failed_metric": {
                    "match_score": sim_score,
                    "sim_status": sim_status,
                    "threshold": round(threshold, 3),
                },
                "llm_reason": output.reason,
                "updated_hypothesis": output.updated_hypothesis or "",
            },
        )

    return updates


# --------------------------------------------------------------------------- #
# Node 6 — Human Approval (interrupt)                                          #
# --------------------------------------------------------------------------- #

def human_approval_node(state: AgentState) -> dict:
    """Pauses the graph for human review, then closes the feedback loop.

    On approval:
      1. Activates the Flow in Graph B (status → 'active')
      2. Writes a LearningEvent back to Graph A — the feedback loop that makes
         the system smarter over time.

    Resume via:
        graph.invoke(Command(resume={"approved": True, "reason": "..."}), config=config)
    """
    proposal_id = state.get("proposal_id", "")
    sim_results = state.get("simulation_results", [])
    proposed_yaml = state.get("proposed_flow_yaml", "")
    goal_industry = state.get("goal_industry", "")
    hypothesis = state.get("current_hypothesis", "")
    baseline_score = state.get("baseline_score", 0.0)
    sim_score = (
        sim_results[-1].get("metrics", {}).get("match_score", 0.0)
        if sim_results else 0.0
    )

    _emit_node_event(
        state,
        source="human_approval",
        event_type="thinking",
        title="Human approval is waiting for review",
        detail=f"Proposal {proposal_id} is paused for approval.",
        payload={
            "proposal_id": proposal_id,
            "baseline_score": baseline_score,
            "simulation_score": sim_score,
        },
    )

    response: Dict = interrupt({
        "proposal_id": proposal_id,
        "proposed_flow_yaml": proposed_yaml,
        "simulation_results": sim_results,
        "hypothesis": hypothesis,
        "industry": goal_industry,
        "baseline_score": baseline_score,
        "simulation_score": sim_score,
        "score_improvement": round(sim_score - baseline_score, 2),
        "prompt": (
            f"Proposed flow for {goal_industry} — sim score {sim_score:.1f} vs baseline {baseline_score:.1f} "
            f"(+{sim_score - baseline_score:.1f}). Approve? Reply: {{approved: true/false, reason: '...'}}"
        ),
    })

    approved = response.get("approved", False)
    reason = response.get("reason", "No reason provided")

    if approved:
        activate_proposal(proposal_id)

        try:
            learning = graph_queries.log_learning_event(
                flow_id=proposal_id,
                industry=goal_industry,
                hypothesis=hypothesis,
                baseline_score=baseline_score,
                simulation_score=sim_score,
            )
            learning_id = learning.get("learning_event_id", "")
            final = (
                f"Proposal {proposal_id} approved. Flow activated in Graph B. "
                f"LearningEvent {learning_id} written to Graph A "
                f"(+{sim_score - baseline_score:.1f} match score improvement for {goal_industry})."
            )
        except Exception as exc:
            logger.warning("LearningEvent write failed: %s", exc)
            final = f"Proposal {proposal_id} approved and activated in Graph B."
    else:
        reject_proposal(proposal_id, reason)
        final = f"Proposal {proposal_id} rejected. Reason: {reason}"

    _emit_node_event(
        state,
        source="human_approval",
        event_type="approved" if approved else "rejected",
        title="Human approval completed",
        detail=final,
        payload={"proposal_id": proposal_id, "approved": approved},
    )

    return {
        "messages": [AIMessage(content=final)],
        "final_output": final,
    }
