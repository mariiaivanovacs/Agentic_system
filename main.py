"""
EcoLink NeuroCore — CLI entry point.

Usage:
    python main.py --goal "Improve match quality for Healthtech startups"

Optional flags:
    --thread-id   Reuse an existing checkpoint session (for resuming after approval).
    --approve     Resume a paused graph and approve the pending proposal.
    --reject      Resume a paused graph and reject the pending proposal.
    --reason      Rejection reason (used with --reject).

Examples:
    # Start a new optimisation run
    python main.py --goal "Optimize Fintech matching"

    # Approve the pending proposal from a previous run
    python main.py --thread-id abc123 --approve

    # Reject the pending proposal with a reason
    python main.py --thread-id abc123 --reject --reason "Too risky for prod"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")
RUNS_DIR = Path(".agent_runs")


def _separator(title: str = "") -> None:
    width = 60
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, width - len(title) - 6)}\n")
    else:
        print("─" * width)


def _print_state_summary(state: dict) -> None:
    _separator("Run Summary")
    print(f"  Goal              : {state.get('goal', '')}")
    print(f"  Hypothesis        : {state.get('current_hypothesis', '')}")
    print(f"  Problem flow      : {state.get('identified_problem_flow', '')}")
    print(f"  Baseline score    : {state.get('baseline_score', 'N/A')}")
    print(f"  Critic passed     : {state.get('critic_passed', False)}")
    print(f"  Retry count       : {state.get('retry_count', 0)}")

    sim_results = state.get("simulation_results", [])
    if sim_results:
        latest = sim_results[-1]
        _separator("Simulation Result")
        print(f"  Status  : {latest.get('status', 'unknown').upper()}")
        metrics = latest.get("metrics", {})
        for k, v in metrics.items():
            print(f"  {k:<12}: {v}")
        if latest.get("error_log"):
            print(f"  Error   : {latest['error_log']}")

    if state.get("proposed_flow_yaml"):
        _separator("Proposed Flow YAML")
        print(state["proposed_flow_yaml"])

    if state.get("proposal_id"):
        _separator("Proposal")
        print(f"  Proposal ID : {state['proposal_id']}")
        print(f"  Approval required: {state.get('human_approval_required', False)}")

    if state.get("final_output"):
        _separator("Final Output")
        print(f"  {state['final_output']}")


def _save_run_record(thread_id: str, state: dict) -> None:
    proposal_id = state.get("proposal_id")
    if not proposal_id:
        return

    RUNS_DIR.mkdir(exist_ok=True)
    payload = {
        "thread_id": thread_id,
        "proposal_id": proposal_id,
        "goal": state.get("goal", ""),
        "human_approval_required": state.get("human_approval_required", False),
    }
    (RUNS_DIR / f"{thread_id}.json").write_text(json.dumps(payload, indent=2))


def _load_run_record(thread_id: str) -> dict | None:
    path = RUNS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def run_new(goal: str, thread_id: str) -> None:
    from src.agents.graph import build_graph
    from src.agents.tools import verify_neo4j_connection

    print(f"\nStarting new run | thread: {thread_id}")
    print(f"Goal: {goal}\n")

    verify_neo4j_connection()

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "messages": [],
        "goal": goal,
        "current_hypothesis": "",
        "identified_problem_flow": "",
        "proposed_flow_yaml": "",
        "critic_passed": False,
        "critic_feedback": "",
        "retry_count": 0,
        "simulation_results": [],
        "baseline_score": 0.0,
        "simulation_succeeded": False,
        "proposal_id": "",
        "human_approval_required": False,
        "final_output": "",
    }

    # Stream node-by-node so the user sees progress
    for step in graph.stream(initial_state, config=config, stream_mode="updates"):
        for node_name, updates in step.items():
            print(f"[{node_name.upper()}] ✓")
            if not isinstance(updates, dict):
                continue
            msgs = updates.get("messages", [])
            for m in msgs:
                if hasattr(m, "content"):
                    print(f"  → {m.content}")

    final_state = graph.get_state(config).values
    _save_run_record(thread_id, final_state)
    _print_state_summary(final_state)

    # If the graph paused for human approval, tell the user how to resume
    pending = graph.get_state(config).next
    if pending:
        print(
            f"\n⏸  Graph paused — awaiting human approval.\n"
            f"   To approve : python main.py --thread-id {thread_id} --approve\n"
            f"   To reject  : python main.py --thread-id {thread_id} --reject "
            f"--reason \"your reason\"\n"
        )


def run_resume(thread_id: str, approved: bool, reason: str) -> None:
    from src.agents.tools import activate_proposal, reject_proposal, verify_neo4j_connection

    print(f"\nResuming thread: {thread_id}")
    print(f"Decision: {'APPROVE' if approved else 'REJECT'}")
    if not approved:
        print(f"Reason: {reason}")

    verify_neo4j_connection()

    run_record = _load_run_record(thread_id)
    if not run_record:
        raise RuntimeError(
            f"No local run record found for thread {thread_id}. "
            "Run the agent again and use the thread id printed by that run, "
            "or approve/reject directly from the Streamlit Proposals page."
        )

    proposal_id = run_record["proposal_id"]
    if approved:
        activate_proposal(proposal_id)
        final = f"Proposal {proposal_id} approved and activated in Graph B."
    else:
        reject_proposal(proposal_id, reason)
        final = f"Proposal {proposal_id} rejected. Reason: {reason}"

    run_record["human_approval_required"] = False
    run_record["decision"] = "approved" if approved else "rejected"
    (RUNS_DIR / f"{thread_id}.json").write_text(json.dumps(run_record, indent=2))

    _print_state_summary(
        {
            "goal": run_record.get("goal", ""),
            "proposal_id": proposal_id,
            "human_approval_required": False,
            "final_output": final,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EcoLink NeuroCore — multi-agent flow optimiser"
    )
    parser.add_argument("--goal", help="Optimisation goal for the agent")
    parser.add_argument("--thread-id", default=None, help="Session thread ID")
    parser.add_argument("--approve", action="store_true", help="Approve the pending proposal")
    parser.add_argument("--reject", action="store_true", help="Reject the pending proposal")
    parser.add_argument("--reason", default="No reason provided", help="Rejection reason")
    args = parser.parse_args()

    thread_id = args.thread_id or uuid.uuid4().hex[:8]

    if args.approve or args.reject:
        if not args.thread_id:
            parser.error("--thread-id is required when using --approve or --reject")
        run_resume(thread_id, approved=args.approve, reason=args.reason)
    elif args.goal:
        run_new(args.goal, thread_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None
