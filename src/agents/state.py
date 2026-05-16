from __future__ import annotations

from typing import Annotated, Dict, List, Optional, Sequence, TypedDict
import operator

from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    # Conversation history (append-only via operator.add)
    messages: Annotated[Sequence[BaseMessage], operator.add]

    # ---- Core intent ----
    thread_id: str                   # Run/session id used by realtime event stream
    goal: str                        # e.g. "Optimize Fintech matching"
    goal_industry: str               # Industry extracted from goal (e.g. "Fintech")

    # ---- Connected application profile (set at run start; optional) ----
    app_id: Optional[str]            # Domain or explicit ID; None = EcoLink default graph
    app_name: Optional[str]          # Human-readable name of the connected app
    source_type: Optional[str]       # "website" | "codebase" | "api" | "database" | "hybrid"
    source_path: Optional[str]       # Local source folder used during indexing
    base_url: Optional[str]          # Root URL of the connected app
    business_flow_id: Optional[str]  # Selected BusinessFlow node to optimize
    business_flow_context: List[Dict]  # BusinessFlow/FlowStep graph evidence
    proposal_only: bool              # True when UI wants explanation/proposal only, no code patch actions

    # ---- Planner output ----
    current_hypothesis: str          # Working theory the agent is testing
    identified_problem_flow: str     # Flow ID found to be underperforming
    failure_patterns: List[Dict]     # Historical bad matches from Graph A (for Generator)
    success_patterns: List[Dict]     # Historical good matches from Graph A (for Generator)
    software_nodes: List[Dict]       # Project -> File -> Route -> Function -> DataStore
    project_id: Optional[str]        # scopes all queries to one indexed project

    # ---- Generator / Critic cycle ----
    proposed_flow_yaml: str          # YAML produced by the Generator node (backward compat)
    recommended_actions: List[Dict]  # Generator output — primary recommendation list
    critic_passed: bool              # True if Critic approved the YAML
    critic_feedback: str             # Issues raised by Critic (freetext, backward compat)
    critic_evidence_ids: List[str]   # graph node IDs the Critic accepted as grounding
    retry_context: Optional[Dict]    # structured retry payload from Critic or Evaluator
    retry_count: int                 # How many Generator→Critic loops so far

    # ---- Simulation ----
    simulation_results: List[Dict]   # Output from simulate_flow tool
    baseline_score: float            # Historical avg score for problem flow
    infra_error: Optional[Dict]      # set when sandbox fails due to infra (not flow logic)

    # ---- Evaluator / Proposal ----
    simulation_succeeded: bool       # True if simulation beat baseline
    proposal_id: str                 # Neo4j node ID of the persisted proposal

    # ---- Human approval ----
    human_approval_required: bool
    human_approved: Optional[bool]      # set via Command(resume=...) from run_resume
    rejection_reason: Optional[str]     # set via Command(resume=...) from run_resume

    # ---- Terminal ----
    final_output: str
