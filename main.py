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

from src.realtime.event_bus import publish_event

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")
RUNS_DIR = Path(".agent_runs")
AGENT_NODE_NAMES = {
    "planner",
    "generator",
    "critic",
    "simulator",
    "evaluator",
    "human_approval",
}


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

    if state.get("infra_error"):
        err = state["infra_error"]
        _separator("Infrastructure Error")
        print(f"  Type    : {err.get('error_type')}")
        print(f"  Service : {err.get('service')}")
        print(f"  Action  : {err.get('human_action')}")
        if err.get("activation_url"):
            print(f"  Fix URL : {err.get('activation_url')}")
        print(f"  Tip     : Switch to local sandbox with SANDBOX_MODE=local in .env")

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


def _publish_live_log(
    *,
    thread_id: str,
    node_name: str,
    title: str,
    detail: str,
    payload: dict | None = None,
) -> None:
    source = node_name if node_name in AGENT_NODE_NAMES else "agent"
    publish_event(
        thread_id=thread_id,
        source=source,
        event_type="log",
        title=title,
        detail=detail[:1800],
        payload={"node": node_name, **(payload or {})},
    )


def _save_run_record(thread_id: str, state: dict) -> None:
    proposal_id = state.get("proposal_id")
    if not proposal_id:
        return

    RUNS_DIR.mkdir(exist_ok=True)
    payload = {
        "thread_id": thread_id,
        "proposal_id": proposal_id,
        "goal": state.get("goal", ""),
        "project_id": state.get("project_id"),
        "business_flow_id": state.get("business_flow_id"),
        "human_approval_required": state.get("human_approval_required", False),
    }
    (RUNS_DIR / f"{thread_id}.json").write_text(json.dumps(payload, indent=2))


def _load_run_record(thread_id: str) -> dict | None:
    path = RUNS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def run_new(
    goal: str,
    thread_id: str,
    project_id: str | None = None,
    business_flow_id: str | None = None,
    source_path: str | None = None,
    proposal_only: bool = False,
) -> None:
    from src.agents.graph import build_graph
    from src.agents.tools import verify_neo4j_connection

    print(f"\nStarting new run | thread: {thread_id}")
    print(f"Goal: {goal}\n")

    # run_start envelope event — the only event main.py emits during the run.
    # Individual nodes emit their own events via _emit_node_event() internally.
    publish_event(
        thread_id=thread_id,
        source="ui",
        target="planner",
        event_type="started",
        title="Agent run started",
        detail=goal,
        payload={
            "goal": goal,
            "project_id": project_id,
            "business_flow_id": business_flow_id,
            "source_path": source_path,
            "proposal_only": proposal_only,
        },
    )

    verify_neo4j_connection()

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "thread_id": thread_id,
        "messages": [],
        "goal": goal,
        "goal_industry": "",
        # App profile — empty by default (CLI runs target the EcoLink default graph)
        "app_id": None,
        "app_name": None,
        "source_type": None,
        "source_path": source_path,
        "base_url": None,
        "business_flow_id": business_flow_id,
        "business_flow_context": None,
        "proposal_only": proposal_only,
        "current_hypothesis": "",
        "identified_problem_flow": "",
        "failure_patterns": [],
        "success_patterns": [],
        "software_nodes": [],
        "project_id": project_id,
        "proposed_flow_yaml": "",
        "recommended_actions": [],
        "skills_referenced": [],
        "critic_passed": False,
        "critic_feedback": "",
        "critic_evidence_ids": [],
        "retry_context": None,
        "retry_count": 0,
        "simulation_results": [],
        "baseline_score": 0.0,
        "infra_error": None,
        "simulation_succeeded": False,
        "proposal_id": "",
        "human_approval_required": False,
        "human_approved": None,
        "rejection_reason": None,
        "final_output": "",
    }

    # Stream node-by-node so the user sees progress. Nodes emit semantic events
    # internally; main.py adds terminal-style live logs for the dashboard.
    try:
        for step in graph.stream(initial_state, config=config, stream_mode="updates"):
            for node_name, updates in step.items():
                node_line = f"[{node_name.upper()}] ✓"
                print(node_line)
                _publish_live_log(
                    thread_id=thread_id,
                    node_name=node_name,
                    title=f"{node_name.replace('_', ' ').title()} completed",
                    detail=node_line,
                    payload={"status": "completed"},
                )
                if isinstance(updates, dict):
                    for m in updates.get("messages", []):
                        if hasattr(m, "content"):
                            content = str(m.content)
                            print(f"  → {content}")
                            _publish_live_log(
                                thread_id=thread_id,
                                node_name=node_name,
                                title=f"{node_name.replace('_', ' ').title()} output",
                                detail=content,
                                payload={"status": "message"},
                            )
    except Exception as exc:
        publish_event(
            thread_id=thread_id,
            source="agent",
            event_type="error",
            title="Agent run failed",
            detail=str(exc),
            payload={
                "goal": goal,
                "project_id": project_id,
                "business_flow_id": business_flow_id,
            },
        )
        raise

    final_state = graph.get_state(config).values
    _save_run_record(thread_id, final_state)
    _print_state_summary(final_state)

    # run_end envelope event
    publish_event(
        thread_id=thread_id,
        source="agent",
        event_type="result",
        title="Agent run completed",
        detail=final_state.get("final_output", ""),
        payload={
            "proposal_id": final_state.get("proposal_id", ""),
            "simulation_succeeded": final_state.get("simulation_succeeded", False),
        },
    )

    # If the graph paused for human approval, tell the user how to resume
    pending = graph.get_state(config).next
    if pending:
        publish_event(
            thread_id=thread_id,
            source="human_approval",
            event_type="approval_required",
            title="Graph paused for admin approval",
            detail=f"Approve or reject proposal {final_state.get('proposal_id', '')}",
            payload={"proposal_id": final_state.get("proposal_id", "")},
        )
        print(
            f"\n⏸  Graph paused — awaiting human approval.\n"
            f"   To approve : python main.py --thread-id {thread_id} --approve\n"
            f"   To reject  : python main.py --thread-id {thread_id} --reject "
            f"--reason \"your reason\"\n"
        )


def run_resume(thread_id: str, approved: bool, reason: str) -> None:
    """Resume a paused graph through the LangGraph checkpointer.

    This properly triggers human_approval_node so the LearningEvent feedback
    loop fires and the graph state is persisted correctly. The checkpointer
    must be SqliteSaver (set in graph.py) for cross-process resume to work.
    """
    from langgraph.types import Command
    from src.agents.graph import build_graph
    from src.agents.tools import verify_neo4j_connection

    print(f"\nResuming thread: {thread_id}")
    print(f"Decision: {'APPROVE' if approved else 'REJECT'}")
    if not approved:
        print(f"Reason: {reason}")

    verify_neo4j_connection()

    publish_event(
        thread_id=thread_id,
        source="human_approval",
        event_type="approved" if approved else "rejected",
        title="Admin approved proposal" if approved else "Admin rejected proposal",
        detail=reason if not approved else "Proposal activation requested from CLI.",
    )

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    # Resume the interrupted graph — Command(resume=...) passes the value that
    # interrupt() returns inside human_approval_node.
    graph.invoke(
        Command(resume={"approved": approved, "reason": reason}),
        config=config,
    )

    final_state = graph.get_state(config).values

    # Update local run record with decision
    run_record = _load_run_record(thread_id)
    if run_record:
        run_record["human_approval_required"] = False
        run_record["decision"] = "approved" if approved else "rejected"
        (RUNS_DIR / f"{thread_id}.json").write_text(json.dumps(run_record, indent=2))

    _print_state_summary(
        {
            "goal": final_state.get("goal", run_record.get("goal", "") if run_record else ""),
            "proposal_id": final_state.get("proposal_id", ""),
            "human_approval_required": False,
            "final_output": final_state.get("final_output", ""),
        }
    )

    publish_event(
        thread_id=thread_id,
        source="human_approval",
        event_type="result",
        title="Approval decision completed",
        detail=final_state.get("final_output", ""),
        payload={
            "proposal_id": final_state.get("proposal_id", ""),
            "approved": approved,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EcoLink NeuroCore — multi-agent flow optimiser"
    )
    parser.add_argument("--goal", help="Optimisation goal for the agent")
    parser.add_argument("--thread-id", default=None, help="Session thread ID")
    parser.add_argument("--project-id", default=None, help="Project graph ID to scope optimization")
    parser.add_argument("--business-flow-id", default=None, help="BusinessFlow node ID to optimize")
    parser.add_argument("--source-path", default=None, help="Local source folder for isolated code sandbox")
    parser.add_argument("--proposal-only", action="store_true", help="Create a visual/text proposal only; do not generate code patch actions")
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
        run_new(
            args.goal,
            thread_id,
            project_id=args.project_id,
            business_flow_id=args.business_flow_id,
            source_path=args.source_path,
            proposal_only=args.proposal_only,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None
