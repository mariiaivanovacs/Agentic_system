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
# This is the correct pattern per PLATFORM_PLAN: all Cypher lives in queries.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ecolink-graph"))
import queries as graph_queries  # noqa: E402

from src.agents.state import AgentState
from src.agents.tools import (
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


class GeneratorOutput(BaseModel):
    flow_yaml: str = Field(description="Complete YAML definition of the proposed new flow")
    skills_referenced: List[str] = Field(description="List of skill IDs used in the YAML")
    target_server_id: str = Field(description="Server ID the flow should run on")


class CriticOutput(BaseModel):
    is_valid: bool = Field(description="True if the YAML passes all checks")
    issues: List[str] = Field(description="List of concrete problems found (empty if valid)")
    suggestions: str = Field(description="Recommendations for the Generator if is_valid is False")


class EvaluatorOutput(BaseModel):
    decision: str = Field(description="'success' if simulation beat baseline, else 'failure'")
    reason: str = Field(description="Explanation of the decision")
    updated_hypothesis: Optional[str] = Field(
        default=None,
        description="Revised hypothesis when decision is 'failure'",
    )


# --------------------------------------------------------------------------- #
# Retry wrapper for structured LLM calls                                       #
# --------------------------------------------------------------------------- #

def _structured_invoke(llm: ChatGoogleGenerativeAI, prompt: str, schema):
    # The google-genai SDK already has built-in exponential-backoff retry for
    # transient errors and 429s. Adding a second tenacity layer causes double
    # retries that burn quota 3× faster — so we call through directly.
    return llm.with_structured_output(schema).invoke(prompt)


def _extract_flow_references(flow_def: dict) -> tuple[set[str], set[str]]:
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

    context = retrieve_context(goal=goal)

    # Semantic GraphRAG augmentation — non-fatal
    try:
        semantic_skills = query_graph_semantic.invoke({
            "query_text": f"{context.industry} {goal} mentor matching",
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

    prompt = build_agent_planner_prompt(goal, context) + semantic_section

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
    the Planner and proposes a matching flow that specifically addresses them.
    The prompt is framed around match quality improvement, not code optimization.
    """
    hypothesis = state.get("current_hypothesis", "")
    critic_feedback = state.get("critic_feedback", "")
    problem_flow = state.get("identified_problem_flow", "")
    goal_industry = state.get("goal_industry", "")
    failure_patterns = state.get("failure_patterns", [])
    success_patterns = state.get("success_patterns", [])

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

    feedback_section = (
        f"\n== Critic feedback — fix these issues before regenerating ==\n{critic_feedback}"
        if critic_feedback else ""
    )

    # Distil pain points and failed skill patterns from Graph A data
    pain_points = list({p.get("pain_points", "") for p in failure_patterns if p.get("pain_points")})
    failed_skills = list({str(p.get("skills", "")) for p in failure_patterns if p.get("skills")})[:3]
    winning_skills = list({str(p.get("skills", "")) for p in success_patterns if p.get("skills")})[:3]

    industry_slug = goal_industry.lower().replace("-", "").replace(" ", "") if goal_industry else "general"

    prompt = f"""You are an Ecosystem Architect for EcoLink, a mentor–startup matching platform.

Industry: {goal_industry}
Hypothesis to test: {hypothesis}
Flow to replace: {problem_flow}
{feedback_section}

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

Generate a new YAML flow that:
1. Has flow_id in format: flow_proposal_{industry_slug}_v<N>   (e.g. flow_proposal_{industry_slug}_v1)
2. Uses ONLY skill IDs from the "Available matching skills" list above
3. Specifies `runs_on: {preferred_server}`
4. Has a `description` that names the specific pain point it targets
5. Orders steps so they address the failure pattern: assess pain_points → match semantically → score
6. Targets MATCH QUALITY improvement (outcome_score), not server latency

Return the complete YAML as a plain string in the flow_yaml field."""

    output: GeneratorOutput = _structured_invoke(_llm(), prompt, GeneratorOutput)

    # Surface any skills the LLM invented that don't exist in Graph B
    valid_skill_ids = {s["id"] for s in valid_skills}
    unknown_referenced = set(output.skills_referenced) - valid_skill_ids
    if unknown_referenced:
        logger.warning(
            "Generator referenced unknown skills %s — writing SkillProposals.", unknown_referenced
        )
        _propose_unknown_skills(unknown_referenced, goal_industry)

    _emit_node_event(
        state,
        source="generator",
        target="critic",
        event_type="message",
        title="Generator drafted flow",
        detail=f"Skills: {output.skills_referenced}; server: {output.target_server_id}",
        payload={"flow_yaml": output.flow_yaml},
    )

    return {
        "messages": [
            AIMessage(content=(
                f"Generated ecosystem flow for {goal_industry}\n"
                f"Hypothesis: {hypothesis}\n"
                f"Skills used: {output.skills_referenced}\n"
                f"Server: {output.target_server_id}"
            ))
        ],
        "proposed_flow_yaml": output.flow_yaml,
    }


# --------------------------------------------------------------------------- #
# Node 3 — Critic                                                              #
# --------------------------------------------------------------------------- #

def critic_node(state: AgentState) -> dict:
    """
    Validates the proposed YAML for syntax, valid skill/connector references,
    and infrastructure health before allowing it to proceed to simulation.
    """
    flow_yaml = state.get("proposed_flow_yaml", "")
    retry_count = state.get("retry_count", 0)

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

    if local_issues:
        suggestions = (
            "Regenerate the YAML using only valid skill IDs from Graph B, "
            "a healthy runs_on server, and steps[*].skill references."
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
            "retry_count": retry_count + 1,
        }

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

Set is_valid=True only if ALL checks pass."""

    output: CriticOutput = _structured_invoke(_llm(), prompt, CriticOutput)

    _emit_node_event(
        state,
        source="critic",
        target="simulator" if output.is_valid else "generator",
        event_type="decision",
        title="Critic passed flow" if output.is_valid else "Critic rejected flow",
        detail="No issues found." if output.is_valid else output.issues,
        payload={"critic_passed": output.is_valid},
    )

    return {
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


# --------------------------------------------------------------------------- #
# Node 4 — Simulator                                                           #
# --------------------------------------------------------------------------- #

def simulator_node(state: AgentState) -> dict:
    """Sends the proposed flow to the Secure Sandbox and stores the metrics.

    Also writes an ExecutionTrace bridge node to Neo4j so future Planner runs
    can learn from accumulated simulation history.
    """
    flow_yaml = state.get("proposed_flow_yaml", "")
    problem_flow_id = state.get("identified_problem_flow", "")

    result: Dict = simulate_flow.invoke({
        "flow_yaml": flow_yaml,
        "dataset_snapshot_id": "snapshot_2025_q4",
    })

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
    On success: calls propose_change and flags for human approval.
    On failure: updates the hypothesis and increments retry_count.
    """
    simulation_results = state.get("simulation_results", [])
    latest = simulation_results[-1] if simulation_results else {}
    baseline_score = state.get("baseline_score", 3.0)
    problem_flow = state.get("identified_problem_flow", "")
    hypothesis = state.get("current_hypothesis", "")
    retry_count = state.get("retry_count", 0)

    # Short-circuit: infrastructure errors are not flow failures — don't retry
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
        }

    prompt = f"""You are the Evaluator agent for EcoLink NeuroCore.

Problem flow: {problem_flow}
Hypothesis under test: {hypothesis}
Historical baseline average score: {baseline_score}

== Simulation result ==
{json.dumps(latest, indent=2)}

Decision rules:
- 'success' if simulation status is 'success' AND simulation match_score > baseline_score * {IMPROVEMENT_THRESHOLD}
- 'failure' in all other cases

If decision is 'failure', provide an updated_hypothesis that proposes a different approach.
Explain your reasoning in the `reason` field."""

    output: EvaluatorOutput = _structured_invoke(_llm(), prompt, EvaluatorOutput)

    updates: dict = {
        "messages": [
            AIMessage(content=(
                f"Evaluation: {output.decision.upper()}. {output.reason}"
            ))
        ],
        "simulation_succeeded": output.decision == "success",
    }

    if output.decision == "success":
        sim_score = latest.get("metrics", {}).get("match_score", 0.0)
        proposal_id = propose_change.invoke({
            "change_type": "new_flow",
            "details": {
                "yaml": state.get("proposed_flow_yaml", ""),
                "hypothesis": hypothesis,
                "simulation_score": sim_score,
                "baseline_score": baseline_score,
            },
        })
        updates["proposal_id"] = proposal_id
        updates["human_approval_required"] = True
        updates["messages"].append(
            AIMessage(content=f"Proposal saved to Neo4j with ID: {proposal_id}")
        )
        
        # Propose skill updates based on simulation performance
        skills_used: List[str] = state.get("skills_referenced", [])
        for skill_id in skills_used:
            try:
                # Calculate improved metrics from simulation
                exec_time_ms = latest.get("metrics", {}).get("execution_time_ms", 0.0)
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
            payload={"proposal_id": proposal_id, "decision": output.decision},
        )
    else:
        updates["human_approval_required"] = False
        updates["retry_count"] = retry_count + 1
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
            payload={"decision": output.decision, "retry_count": retry_count + 1},
        )

    return updates


# --------------------------------------------------------------------------- #
# Node 6 — Human Approval (interrupt)                                          #
# --------------------------------------------------------------------------- #

def human_approval_node(state: AgentState) -> dict:
    """Pauses the graph for human review, then closes the feedback loop.

    On approval:
      1. Activates the Flow in Graph B (status → 'active')
      2. Writes a LearningEvent back to Graph A — this is the feedback loop that
         makes the system smarter over time. Future Planner runs will see this
         event and avoid repeating the same hypothesis.

    Resume payload:
        {"approved": True}
        {"approved": False, "reason": "..."}
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

        # Close the feedback loop: write LearningEvent back to Graph A
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
