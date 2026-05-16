"""
GraphRAG pipeline module — Stream 2 (Leila).

Stream 3 (API) imports this as:
    from src.graphrag.main_graphrag import run as graphrag_run

The run() function is the single public surface that connects:
    retriever → prompt_engine → generator → validator

and returns the Contract 1 JSON structure defined in response.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure project root is on sys.path when this file is run directly
# (python src/graphrag/main_graphrag.py) rather than as a module.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import yaml
from dotenv import load_dotenv
from loguru import logger

from src.graphrag.generator import generate_flow_proposal
from src.graphrag.validator import validate_flow_yaml


def run(goal: str, industry: str, max_retries: int = 3) -> dict:
    """
    End-to-end GraphRAG pipeline.

    Returns Contract 1 JSON:
    {
      "goal": str,
      "industry": str,
      "reasoning_trace": str,
      "proposed_flow": {
        "flow_id": str,
        "steps": [{"skill": str, "params": {...}}, ...]
      },
      "status": "valid" | "invalid",
      "errors": []
    }
    """
    try:
        proposal = generate_flow_proposal(goal=goal, industry=industry, max_retries=max_retries)
    except RuntimeError as exc:
        logger.error(f"GraphRAG pipeline failed: {exc}")
        return {
            "goal": goal,
            "industry": industry,
            "reasoning_trace": "",
            "proposed_flow": {"flow_id": "", "steps": []},
            "status": "invalid",
            "errors": [str(exc)],
        }

    # Parse the validated YAML into the Contract 1 shape
    try:
        flow_dict = yaml.safe_load(proposal.flow_yaml)
        proposed_flow = {
            "flow_id": flow_dict.get("flow_id", ""),
            "steps": [
                {"skill": step.get("skill", ""), "params": step.get("params", {})}
                for step in flow_dict.get("steps", [])
            ],
        }
    except Exception as exc:
        logger.error(f"Failed to parse validated YAML: {exc}")
        proposed_flow = {"flow_id": "", "steps": []}

    return {
        "goal": goal,
        "industry": industry,
        "reasoning_trace": proposal.reasoning_trace,
        "proposed_flow": proposed_flow,
        "status": "valid",
        "errors": [],
    }


# --------------------------------------------------------------------------- #
# CLI entry point                                                               #
# --------------------------------------------------------------------------- #

def _cli() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="EcoLink NeuroCore — GraphRAG pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python src/graphrag/main_graphrag.py --goal 'Optimize Fintech' --industry Fintech\n"
            "  python src/graphrag/main_graphrag.py --goal 'Improve Healthtech' --industry Healthtech --output flow.yaml\n"
        ),
    )
    parser.add_argument("--goal",     required=True, help="Natural-language optimization goal")
    parser.add_argument("--industry", required=True, help="Industry (Fintech, Healthtech, E-commerce…)")
    parser.add_argument("--output",   default=None,  help="Optional YAML output file path")
    parser.add_argument("--retries",  default=3, type=int, help="LLM retry attempts")
    args = parser.parse_args()

    result = run(goal=args.goal, industry=args.industry, max_retries=args.retries)

    # Print Contract 1 JSON to stdout
    print(json.dumps(result, indent=2))

    if args.output and result["status"] == "valid" and result["proposed_flow"]["steps"]:
        flow_yaml_str = yaml.dump(result["proposed_flow"], default_flow_style=False, sort_keys=False)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(flow_yaml_str)
        logger.info(f"Flow written to '{args.output}'")

    if result["status"] == "invalid":
        sys.exit(1)


if __name__ == "__main__":
    _cli()
