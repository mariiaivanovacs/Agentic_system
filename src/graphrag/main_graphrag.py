from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from src.graphrag.retriever import retrieve_context


def run(goal: str, industry: str | None = None) -> dict:
    context = retrieve_context(industry=industry, goal=goal)
    return {
        "goal": goal,
        "industry": context.industry,
        "baseline_score": context.baseline_score,
        "failure_patterns": context.failure_patterns,
        "success_patterns": context.success_patterns,
        "active_flows": context.active_flows,
        "available_skills": context.available_skills,
        "website_entities": context.website_entities,
        "status": "retrieved",
    }


def _cli() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="EcoLink GraphRAG context retriever")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--industry", default=None)
    args = parser.parse_args()
    print(json.dumps(run(goal=args.goal, industry=args.industry), indent=2, default=str))


if __name__ == "__main__":
    _cli()

