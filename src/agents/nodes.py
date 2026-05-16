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
            "add_observability | flag_risk | request_admin_approval"
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

    # Query the connected project's codebase graph before retrieve_context so we
    # can pass real software evidence into the prompt.
    software_nodes: List[Dict] = []
    if selected_project_id:
        where_clause = f"WHERE n.project_id = {json.dumps(selected_project_id)} OR n.id = {json.dumps(selected_project_id)}"
    elif app_id:
        where_clause = f"WHERE n.app_id = {json.dumps(app_id)}"
    else:
        where_clause = ""
    for label in ("Project", "BusinessFlow", "FlowStep", "File", "Route", "Function", "DataStore"):
        try:
            rows: List[Dict] = query_graph.invoke({
                "cypher_query": (
                    f"MATCH (n:{label}) {where_clause} "
                    "RETURN elementId(n) AS element_id, n.id AS id, n.name AS name, "
                    "n.path AS path, n.source_path AS source_path, "
                    "n.description AS description, n.app_id AS app_id, n.project_id AS project_id "
                    "LIMIT 20"
                )
            })
            for row in rows:
                row["_label"] = label
            software_nodes.extend(rows)
        except Exception:
            pass

    # Resolve project_id from the first Project node found, fall back to app_id
    project_node = next((n for n in software_nodes if n.get("_label") == "Project"), None)
    project_id = selected_project_id or (project_node.get("id") if project_node else (app_id or None))

    business_flow_context: List[Dict] = []
    if selected_business_flow_id:
        try:
            business_flow_context = query_graph.invoke({
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
            if business_flow_context:
                flow_record = business_flow_context[0]
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

    context = retrieve_context(goal=goal)

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

    selected_flow_section = ""
    if business_flow_context:
        selected_flow_section = (
            "\n\n== Selected BusinessFlow to optimize ==\n"
            f"{json.dumps(business_flow_context[0], indent=2)}\n"
            "Use these BusinessFlow, FlowStep, and primitive IDs as the primary evidence. "
            "Do not invent executable graph node types; if a missing capability is needed, "
            "create a proposal action instead."
        )

    prompt = (
        build_agent_planner_prompt(goal, context, software_nodes=software_nodes)
        + selected_flow_section
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
    business_flow_context = state.get("business_flow_context", [])

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
        f"{json.dumps(business_flow_context[0], indent=2)}\n"
        "Optimize this exact ordered BusinessFlow/FlowStep chain. Every recommended "
        "action must cite at least one ID from this selected flow or its primitive steps.\n"
        if business_flow_context else ""
    )

    code_patch_section = ""
    if source_path and software_nodes:
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
{json.dumps([{{"id": c["id"], "type": c["type"]}} for c in valid_connectors], indent=2)}

== Healthy servers (pick one for runs_on) ==
{healthy_servers}

Generate recommended_actions to address the hypothesis. Each action MUST have:
- action_type: one of create_skill | modify_workflow | modify_code | add_validation | add_observability | flag_risk | request_admin_approval
- target_node_id: an ID from the Codebase Evidence or graph above
- evidence_node_ids: IDs from the Codebase Evidence that justify the action
- description: what this action does and why

Do not invent new executable node types. If a missing capability is required,
use create_skill/request_admin_approval so it remains a proposal until reviewed.

For the primary modify_workflow action include a complete flow_yaml that:
1. Has flow_id: flow_proposal_{industry_slug}_v<N>
2. Uses ONLY skill IDs from "Available matching skills"
3. Specifies runs_on: {preferred_server}
4. Has a description naming the specific pain point it targets
5. Orders steps to address the failure pattern: assess pain_points → match semantically → score
6. Targets MATCH QUALITY improvement (outcome_score), not server latency

Return GeneratorOutput: recommended_actions list + hypothesis_tested."""

    output: GeneratorOutput = _structured_invoke(_llm(), prompt, GeneratorOutput)

    # Extract the first modify_workflow action's YAML for backward-compat fields
    modify_action = next(
        (a for a in output.recommended_actions if a.action_type == "modify_workflow" and a.flow_yaml),
        None,
    )
    flow_yaml = modify_action.flow_yaml if modify_action else ""

    # Surface any skills the LLM invented that don't exist in Graph B
    valid_skill_ids = {s["id"] for s in valid_skills}
    if flow_yaml:
        try:
            parsed = yaml.safe_load(flow_yaml)
            if isinstance(parsed, dict):
                flow_def = _normalise_flow_def(parsed)
                referenced_skills, _ = _extract_flow_references(flow_def)
                unknown_referenced = referenced_skills - valid_skill_ids
                if unknown_referenced:
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

    # Step 2: load valid IDs from Graph B + approved SkillProposals
    valid_skill_records: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (s:Skill) RETURN s.id AS id"
    })
    approved_proposals: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (s:SkillProposal {status: 'approved'}) RETURN s.id AS id"
    })
    valid_skills = {r["id"] for r in valid_skill_records} | {r["id"] for r in approved_proposals}

    valid_conn_records: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (c:Connector) RETURN c.id AS id"
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
            local_issues.append("No skills found in steps[*].skill.")

    # Step 3: validate evidence_node_ids for each recommended_action.
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

    if local_issues:
        suggestions = (
            "Regenerate the YAML using only valid skill IDs from Graph B, "
            "a healthy runs_on server, steps[*].skill references, "
            "and evidence_node_ids that exist in the graph."
        )
        _emit_node_event(
            state,
            source="critic",
            target="generator",
            event_type="decision",
            title="Critic rejected flow locally",
            detail="; ".join(local_issues),
            payload={"issues": local_issues, "retry_count": retry_count + 1},
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

    try:
        context = retrieve_context(
            industry=state.get("goal_industry") or None,
            goal=state.get("goal", ""),
        )
        prompt = build_critic_prompt(flow_yaml, context, goal=state.get("goal", ""))
        prompt += (
            "\n\n== Local deterministic checks already passed ==\n"
            f"Valid skill IDs: {sorted(valid_skills)}\n"
            f"Valid connector IDs: {sorted(valid_connectors)}\n"
            f"Server infrastructure status: {json.dumps(infra_summary, indent=2)}\n"
        )
        prompt += evidence_required_note
    except Exception as exc:
        logger.warning("GraphRAG critic context failed; using local critic prompt: %s", exc)
        prompt = f"""You are the Critic agent for EcoLink NeuroCore. Review the proposed flow YAML.

== Proposed YAML ==
{flow_yaml}

== Syntax check result ==
{"PASS" if syntax_error is None else f"FAIL: {syntax_error}"}

== Valid skill IDs in Graph B ==
{sorted(valid_skills)}

== Valid connector IDs in Graph B ==
{sorted(valid_connectors)}

== Server infrastructure status ==
{json.dumps(infra_summary, indent=2)}

Check the following and return is_valid + any issues found:
1. YAML is syntactically correct.
2. Every skill referenced in `steps[*].skill` exists in the valid skills list.
3. Every connector referenced (if any) exists in the valid connectors list.
4. The `runs_on` server has load < 80% and error_rate < 3%.
5. Steps are logically ordered and the flow makes sense for a matching system.

Set is_valid=True only if ALL checks pass.{evidence_required_note}"""

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

    # Collect code patches from modify_code actions
    code_actions = [
        a for a in recommended_actions
        if a.get("action_type") == "modify_code" and a.get("code_patch")
    ]

    if source_path and code_actions:
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

    if problem_flow_id:
        sim_score = metrics.get("match_score", 0.0) if status == "success" else 0.0
        log_execution_trace(
            flow_id=problem_flow_id,
            result_score=sim_score,
            status=status,
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

    # LLM provides reasoning and a revised hypothesis on failure — does NOT decide.
    prompt = f"""You are the Evaluator agent for EcoLink NeuroCore.

Problem flow: {problem_flow}
Hypothesis under test: {hypothesis}
Historical baseline average score: {baseline_score}

== Simulation result ==
{json.dumps(latest, indent=2)}

The decision will be computed deterministically (score > baseline * {IMPROVEMENT_THRESHOLD}).
Your job is to:
1. Explain what the simulation result means in plain English (reason field).
2. If the score did NOT beat the threshold, propose an updated_hypothesis that
   takes a meaningfully different approach than the current one.

Do NOT include a decision field — that is computed automatically."""

    output: EvaluatorOutput = _structured_invoke(_llm(), prompt, EvaluatorOutput)

    # Deterministic decision — LLM cannot override this
    sim_score = latest.get("metrics", {}).get("match_score", 0.0)
    sim_status = latest.get("status", "fail")
    threshold = baseline_score * IMPROVEMENT_THRESHOLD
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
        proposal_id = propose_change.invoke({
            "change_type": "new_flow",
            "details": {
                "yaml": state.get("proposed_flow_yaml", ""),
                "hypothesis": hypothesis,
                "simulation_score": sim_score,
                "baseline_score": baseline_score,
                "project_id": state.get("project_id"),
                "business_flow_id": state.get("business_flow_id"),
                "business_flow_context": state.get("business_flow_context", []),
                "recommended_actions": state.get("recommended_actions", []),
                "sandbox_result": latest,
                "justification": output.reason,
            },
        })
        updates["proposal_id"] = proposal_id
        updates["human_approval_required"] = True
        updates["retry_context"] = None          # clear stale retry context on success
        updates["messages"].append(
            AIMessage(content=f"Proposal saved to Neo4j with ID: {proposal_id}")
        )
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
