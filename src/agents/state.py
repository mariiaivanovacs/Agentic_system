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

    # ---- Planner output ----
    current_hypothesis: str          # Working theory the agent is testing
    identified_problem_flow: str     # Flow ID found to be underperforming
    failure_patterns: List[Dict]     # Historical bad matches from Graph A (for Generator)
    success_patterns: List[Dict]     # Historical good matches from Graph A (for Generator)

    # ---- Generator / Critic cycle ----
    proposed_flow_yaml: str          # YAML produced by the Generator node
    critic_passed: bool              # True if Critic approved the YAML
    critic_feedback: str             # Issues raised by Critic (if any)
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

    # ---- Terminal ----
    final_output: str
