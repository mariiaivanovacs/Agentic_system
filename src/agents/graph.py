"""
Assembles the EcoLink NeuroCore LangGraph StateGraph.

Topology:
  planner → generator → critic
                 ↑          │ (pass)
                 │       simulator → evaluator
                 │                       │ (success)
                 │                 human_approval → END
                 └──────── (fail, retry < MAX) ────┘
                                         │ (fail, retry >= MAX)
                                        END

Checkpointing uses MemorySaver (in-process). To switch to Redis:
    from langgraph.checkpoint.redis import RedisSaver
    memory = RedisSaver.from_conn_string(os.environ["REDIS_URL"])
"""
from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agents.nodes import (
    critic_node,
    evaluator_node,
    generator_node,
    human_approval_node,
    planner_node,
    simulator_node,
    MAX_RETRIES,
)
from src.agents.state import AgentState

# --------------------------------------------------------------------------- #
# Conditional routing functions                                                #
# --------------------------------------------------------------------------- #

def _route_critic(state: AgentState) -> str:
    """After Critic: pass → simulator, fail → generator (up to MAX_RETRIES)."""
    if state.get("critic_passed"):
        return "simulator"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return END
    return "generator"


def _route_evaluator(state: AgentState) -> str:
    """After Evaluator: success → human_approval, fail → generator (up to MAX_RETRIES)."""
    if state.get("human_approval_required"):
        return "human_approval"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return END
    return "generator"


# --------------------------------------------------------------------------- #
# Graph builder                                                                #
# --------------------------------------------------------------------------- #

def build_graph():
    """Build and compile the EcoLink NeuroCore StateGraph.

    Returns a compiled LangGraph app ready for .invoke() / .stream() calls.
    Requires a thread_id in the config to enable checkpointing:
        config = {"configurable": {"thread_id": "my-session-id"}}
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("planner",        planner_node)
    graph.add_node("generator",      generator_node)
    graph.add_node("critic",         critic_node)
    graph.add_node("simulator",      simulator_node)
    graph.add_node("evaluator",      evaluator_node)
    graph.add_node("human_approval", human_approval_node)

    # Entry point
    graph.set_entry_point("planner")

    # Fixed edges
    graph.add_edge("planner",   "generator")
    graph.add_edge("generator", "critic")
    graph.add_edge("simulator", "evaluator")
    graph.add_edge("human_approval", END)

    # Conditional edges
    graph.add_conditional_edges(
        "critic",
        _route_critic,
        {"simulator": "simulator", "generator": "generator", END: END},
    )
    graph.add_conditional_edges(
        "evaluator",
        _route_evaluator,
        {"human_approval": "human_approval", "generator": "generator", END: END},
    )

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)
