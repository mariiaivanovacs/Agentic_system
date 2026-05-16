from __future__ import annotations

from typing import Annotated, Dict, List, Optional, Sequence, TypedDict
import operator

from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    # Conversation history (append-only via operator.add)
    messages: Annotated[Sequence[BaseMessage], operator.add]

    # ---- Core intent ----
    goal: str                        # e.g. "Optimize Fintech matching"

    # ---- Planner output ----
    current_hypothesis: str          # Working theory the agent is testing
    identified_problem_flow: str     # Flow ID found to be underperforming

    # ---- Generator / Critic cycle ----
    proposed_flow_yaml: str          # YAML produced by the Generator node
    critic_passed: bool              # True if Critic approved the YAML
    critic_feedback: str             # Issues raised by Critic (if any)
    retry_count: int                 # How many Generator→Critic loops so far

    # ---- Simulation ----
    simulation_results: List[Dict]   # Output from simulate_flow tool
    baseline_score: float            # Historical avg score for problem flow

    # ---- Evaluator / Proposal ----
    simulation_succeeded: bool       # True if simulation beat baseline
    proposal_id: str                 # Neo4j node ID of the persisted proposal

    # ---- Human approval ----
    human_approval_required: bool

    # ---- Terminal ----
    final_output: str
