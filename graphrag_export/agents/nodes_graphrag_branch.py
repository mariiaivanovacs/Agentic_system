"""
The five core nodes of the EcoLink NeuroCore agentic brain plus the
Human Approval interrupt node.

Node execution order (see graph.py for the wiring):
  planner → generator → critic ─(pass)→ simulator → evaluator
                  ↑                                      │
                  └──────────(fail, retry < 3)───────────┘
                                                         │(success)
                                                   human_approval

P1 wiring (Stream 2 — Leila):
  planner_node and critic_node now use retrieve_context() and build_*_prompt()
  from src/graphrag/ for richer context and decoupled prompt engineering.
  A sys.path fix allows importing from the MyHack root alongside Agentic_system/.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Dict, List, Optional

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from src.agents.state import AgentState
from src.agents.tools import (
    activate_proposal,
    get_infrastructure_status,
    log_execution_trace,
    propose_change,
    query_graph,
    reject_proposal,
    simulate_flow,
)

# --------------------------------------------------------------------------- #
# P1: GraphRAG imports (src/graphrag/ lives at MyHack root, not Agentic_system)#
#                                                                              #
# Import strategy: add MyHack/src/ (not MyHack/) to sys.path and import the   #
# graphrag package directly (as 'graphrag', not 'src.graphrag').              #
# This avoids the 'src' namespace collision between MyHack/src/ and           #
# Agentic_system/src/ which are two different packages with the same name.    #
# --------------------------------------------------------------------------- #

_MYHACK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_GRAPHRAG_SRC = os.path.join(_MYHACK_ROOT, "src")
if _GRAPHRAG_SRC not in sys.path:
    sys.path.insert(0, _GRAPHRAG_SRC)

try:
    from graphrag.retriever import retrieve_context as _graphrag_retrieve
    from graphrag.prompt_engine import build_planner_prompt, build_critic_prompt
    _GRAPHRAG_AVAILABLE = True
    logging.getLogger(__name__).info("GraphRAG integration active (P1)")
except ImportError as _graphrag_err:
    _GRAPHRAG_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "graphrag not importable — planner/critic will use inline prompts (%s)", _graphrag_err
    )

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
IMPROVEMENT_THRESHOLD = 1.1  # simulation must beat baseline by this factor

_INDUSTRY_KEYWORDS = {
    "fintech": "Fintech",
    "healthtech": "Healthtech",
    "health": "Healthtech",
    "edtech": "EdTech",
    "ecommerce": "E-commerce",
    "e-commerce": "E-commerce",
}


def _inline_critic_prompt(
    flow_yaml: str,
    syntax_error: Optional[str],
    valid_skills: set,
    valid_connectors: set,
    infra_summary: dict,
) -> str:
    """Fallback critic prompt (no GraphRAG context)."""
    return (
        f"You are the Critic agent for EcoLink NeuroCore. Review the proposed flow YAML.\n\n"
        f"== Proposed YAML ==\n{flow_yaml}\n\n"
        f"== Syntax check result ==\n"
        f"{'PASS' if syntax_error is None else f'FAIL: {syntax_error}'}\n\n"
        f"== Valid skill IDs in Graph B ==\n{sorted(valid_skills)}\n\n"
        f"== Valid connector IDs in Graph B ==\n{sorted(valid_connectors)}\n\n"
        f"== Server infrastructure status ==\n{json.dumps(infra_summary, indent=2)}\n\n"
        "Check the following and return is_valid + any issues found:\n"
        "1. YAML is syntactically correct.\n"
        "2. Every skill referenced in `steps[*].skill` exists in the valid skills list.\n"
        "3. Every connector referenced (if any) exists in the valid connectors list.\n"
        "4. The `runs_on` server has load < 80% and error_rate < 3%.\n"
        "5. Steps are logically ordered and the flow makes sense for a matching system.\n\n"
        "Set is_valid=True only if ALL checks pass."
    )


def _extract_industry_hint(goal: str) -> str:
    """Best-effort industry extraction from goal string for GraphRAG retriever."""
    lower = goal.lower()
    for keyword, canonical in _INDUSTRY_KEYWORDS.items():
        if keyword in lower:
            return canonical
    return "Fintech"  # default fallback for demo


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
# Node 1 — Planner                                                             #
# --------------------------------------------------------------------------- #

def planner_node(state: AgentState) -> dict:
    """
    Queries Graph A for historical performance and Graph B for active flows,
    then asks the LLM to identify the root cause and form a hypothesis.

    P1 (Stream 2): when src.graphrag is available, enriches the prompt with
    success/failure patterns and skill context from the GraphRAG retriever.
    """
    goal = state["goal"]

    flow_scores: List[Dict] = query_graph.invoke({
        "cypher_query": (
            "MATCH (f:Flow {status: 'active'}) "
            "OPTIONAL MATCH (et:ExecutionTrace)-[:RAN_FLOW]->(f) "
            "OPTIONAL MATCH (et)-[:RESULTED_IN]->(o:Outcome) "
            "WITH f, round(avg(o.score), 2) AS trace_avg, count(et) AS simulation_runs "
            "RETURN f.id AS flow_id, "
            "       coalesce(f.avg_outcome_score, trace_avg, 0.0) AS avg_score, "
            "       simulation_runs "
            "ORDER BY avg_score ASC"
        )
    })

    active_flows: List[Dict] = query_graph.invoke({
        "cypher_query": (
            "MATCH (f:Flow {status: 'active'})-[:USES]->(s:Skill) "
            "RETURN f.id AS flow_id, collect(s.id) AS skills, collect(s.name) AS skill_names"
        )
    })

    # P1 wiring: enrich with GraphRAG context when available
    graphrag_section = ""
    if _GRAPHRAG_AVAILABLE:
        try:
            industry = _extract_industry_hint(goal)
            context = _graphrag_retrieve(industry=industry, goal=goal)
            graphrag_section = (
                f"\n== Historical success patterns (GraphRAG — Graph A) ==\n"
                f"{json.dumps(context.success_patterns, indent=2)}\n"
                f"\n== Historical failure patterns (GraphRAG — Graph A) ==\n"
                f"{json.dumps(context.failure_patterns, indent=2)}\n"
                f"\n== Available skills (GraphRAG — Graph B) ==\n"
                f"{json.dumps(context.available_skills, indent=2)}\n"
            )
        except Exception as exc:
            logger.warning("GraphRAG enrichment failed in planner_node: %s", exc)

    prompt = f"""You are the Planner agent for EcoLink NeuroCore, a mentor–startup matching system.

Goal: {goal}

== Historical flow performance via ExecutionTrace (Graph A) ==
{json.dumps(flow_scores, indent=2)}

== Active flows with their skills (Graph B) ==
{json.dumps(active_flows, indent=2)}
{graphrag_section}
Tasks:
1. Identify the active flow with the worst average match score.
2. Identify which skills in that flow are causing poor performance.
3. Propose a specific, testable hypothesis for how to improve it.
4. Report the baseline average score for the worst flow.

Be specific — name flow IDs and skill IDs in your hypothesis."""

    output: PlannerOutput = _structured_invoke(_llm(), prompt, PlannerOutput)

    return {
        "messages": [
            HumanMessage(content=f"Goal: {goal}"),
            AIMessage(content=(
                f"Hypothesis: {output.hypothesis}\n"
                f"Problem flow: {output.identified_problem_flow}\n"
                f"Baseline score: {output.baseline_score}\n"
                f"Reasoning: {output.reasoning}"
            )),
        ],
        "current_hypothesis": output.hypothesis,
        "identified_problem_flow": output.identified_problem_flow,
        "baseline_score": output.baseline_score,
    }


# --------------------------------------------------------------------------- #
# Node 2 — Generator                                                           #
# --------------------------------------------------------------------------- #

def generator_node(state: AgentState) -> dict:
    """
    Produces a new flow YAML based on the current hypothesis.
    Also considers critic feedback from previous rounds (if any).
    """
    hypothesis = state.get("current_hypothesis", "")
    critic_feedback = state.get("critic_feedback", "")
    problem_flow = state.get("identified_problem_flow", "legacy_matcher_v1")

    # Fetch valid skills and connectors from Graph B
    valid_skills: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (s:Skill) RETURN s.id AS id, s.name AS name"
    })
    valid_connectors: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (c:Connector) RETURN c.id AS id, c.type AS type"
    })

    infra: Dict = get_infrastructure_status.invoke({})
    healthy_servers = [
        sid for sid, stats in infra.items()
        if stats["load"] < 0.80 and stats["error_rate"] < 0.03
    ]
    preferred_server = healthy_servers[0] if healthy_servers else "server_2"

    feedback_section = (
        f"\n== Critic feedback from last round (fix these issues) ==\n{critic_feedback}"
        if critic_feedback else ""
    )

    prompt = f"""You are the Generator agent for EcoLink NeuroCore.

Current hypothesis: {hypothesis}
Flow to improve: {problem_flow}{feedback_section}

== Valid skill IDs in Graph B ==
{json.dumps(valid_skills, indent=2)}

== Valid connector IDs in Graph B ==
{json.dumps(valid_connectors, indent=2)}

== Available healthy servers ==
{json.dumps(healthy_servers)}

Generate a new YAML flow definition that:
1. Has a unique flow_id (format: flow_proposal_<short_id>, e.g. flow_proposal_v2a)
2. Only references skill IDs from the valid skills list above
3. Specifies `runs_on: {preferred_server}`
4. Includes a clear `description` explaining the improvement
5. Replaces the underperforming skills with better alternatives that test the hypothesis

Return the complete YAML as a plain string in the flow_yaml field."""

    output: GeneratorOutput = _structured_invoke(_llm(), prompt, GeneratorOutput)

    return {
        "messages": [
            AIMessage(content=(
                f"Generated flow YAML for hypothesis: {hypothesis}\n"
                f"Skills referenced: {output.skills_referenced}\n"
                f"Target server: {output.target_server_id}"
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

    P1 (Stream 2): when src.graphrag is available, uses build_critic_prompt()
    for richer historical context in the validation prompt.
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

    # Step 2: load valid IDs from Graph B
    valid_skill_records: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (s:Skill) RETURN s.id AS id"
    })
    valid_skills = {r["id"] for r in valid_skill_records}

    valid_conn_records: List[Dict] = query_graph.invoke({
        "cypher_query": "MATCH (c:Connector) RETURN c.id AS id"
    })
    valid_connectors = {r["id"] for r in valid_conn_records}

    infra: Dict = get_infrastructure_status.invoke({})
    infra_summary = {
        sid: f"load={s['load']:.0%} error_rate={s['error_rate']:.1%}"
        for sid, s in infra.items()
    }

    # P1 wiring: use GraphRAG-aware critic prompt when available
    goal = state.get("goal", "")
    if _GRAPHRAG_AVAILABLE:
        try:
            industry = _extract_industry_hint(goal)
            context = _graphrag_retrieve(industry=industry, goal=goal)
            prompt = build_critic_prompt(
                proposed_yaml=flow_yaml,
                context=context,
                goal=goal,
            )
            # Append the local graph checks that aren't in the GraphRAG prompt
            prompt += (
                f"\n\n== Syntax check result ==\n"
                f"{'PASS' if syntax_error is None else f'FAIL: {syntax_error}'}\n"
                f"\n== Valid skill IDs in Graph B ==\n{sorted(valid_skills)}\n"
                f"\n== Valid connector IDs in Graph B ==\n{sorted(valid_connectors)}\n"
                f"\n== Server infrastructure status ==\n{json.dumps(infra_summary, indent=2)}\n"
                "\nSet is_valid=True only if ALL checks pass."
            )
        except Exception as exc:
            logger.warning("GraphRAG critic prompt failed (%s) — using inline prompt", exc)
            prompt = _inline_critic_prompt(
                flow_yaml, syntax_error, valid_skills, valid_connectors, infra_summary
            )
    else:
        prompt = _inline_critic_prompt(
            flow_yaml, syntax_error, valid_skills, valid_connectors, infra_summary
        )

    output: CriticOutput = _structured_invoke(_llm(), prompt, CriticOutput)

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
    else:
        updates["human_approval_required"] = False
        updates["retry_count"] = retry_count + 1
        if output.updated_hypothesis:
            updates["current_hypothesis"] = output.updated_hypothesis
            updates["critic_feedback"] = ""

    return updates


# --------------------------------------------------------------------------- #
# Node 6 — Human Approval (interrupt)                                          #
# --------------------------------------------------------------------------- #

def human_approval_node(state: AgentState) -> dict:
    """
    Pauses the graph and surfaces the proposal to a human operator.
    Execution resumes when the graph is invoked with Command(resume={...}).

    Resume payload:
        {"approved": True}            — activates the flow in Neo4j
        {"approved": False, "reason": "..."} — rejects and logs reason
    """
    proposal_id = state.get("proposal_id", "")
    sim_results = state.get("simulation_results", [])
    proposed_yaml = state.get("proposed_flow_yaml", "")

    response: Dict = interrupt({
        "proposal_id": proposal_id,
        "proposed_flow_yaml": proposed_yaml,
        "simulation_results": sim_results,
        "hypothesis": state.get("current_hypothesis", ""),
        "baseline_score": state.get("baseline_score", 0.0),
        "prompt": "Do you approve activating this flow? Reply with {approved: true/false, reason: '...'}",
    })

    approved = response.get("approved", False)
    reason = response.get("reason", "No reason provided")

    if approved:
        activate_proposal(proposal_id)
        final = f"Proposal {proposal_id} approved and activated in Graph B."
    else:
        reject_proposal(proposal_id, reason)
        final = f"Proposal {proposal_id} rejected. Reason: {reason}"

    return {
        "messages": [AIMessage(content=final)],
        "final_output": final,
    }
