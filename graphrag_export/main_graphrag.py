"""
EcoLink NeuroCore — Flow 4: GraphRAG Optimization Engine

Usage:
    python main_graphrag.py --goal "Optimize Fintech matching" --industry Fintech --output proposed_flow.yaml
"""

import json
import sys

import click
import yaml
from dotenv import load_dotenv
from dotenv import load_dotenv
from loguru import logger

from src.graphrag.generator import generate_flow
from src.graphrag.mock_retriever import MockRetriever
from src.graphrag.retriever import Neo4jRetriever


@click.command()
@click.option("--goal", required=True, help="Natural language optimization goal")
@click.option(
    "--industry",
    required=True,
    help="Industry to retrieve patterns for (e.g. Fintech, Healthtech, E-commerce)",
)
@click.option(
    "--output",
    required=True,
    help="Output file path for the proposed YAML (e.g. proposed_flow.yaml)",
)
@click.option(
    "--min-score",
    default=8.0,
    show_default=True,
    help="Minimum score threshold to classify a match as successful",
)
@click.option(
    "--max-score",
    default=4.0,
    show_default=True,
    help="Maximum score threshold to classify a match as failed",
)
@click.option(
    "--max-retries",
    default=3,
    show_default=True,
    help="Maximum number of LLM generation+validation retry attempts",
)
def main(
    goal: str,
    industry: str,
    output: str,
    min_score: float,
    max_score: float,
    max_retries: int,
) -> None:
    load_dotenv()

    # --- Step 1: Retrieve (Neo4j with automatic fallback to in-memory mock) ---
    try:
        retriever = Neo4jRetriever()
        logger.info("Connected to Neo4j AuraDB")
    except Exception as exc:
        logger.warning(f"Neo4j unavailable ({exc}) — using in-memory mock data")
        retriever = MockRetriever()

    try:
        success = retriever.retrieve_success_patterns(industry, min_score)
        failure = retriever.retrieve_failure_patterns(industry, max_score)
        skills = retriever.get_available_skills()
    finally:
        retriever.close()

    # Handle empty results — never crash, use default messages
    if not success:
        logger.warning(f"No success patterns found for industry='{industry}'. Using best-practice fallback.")
        success = [{"message": f"No historical success data for {industry}. Apply best practices."}]

    if not failure:
        logger.warning(f"No failure patterns found for industry='{industry}'.")
        failure = [{"message": f"No historical failure data for {industry}."}]

    if not skills:
        logger.error("No Skill nodes found in the graph. Run: python scripts/seed_graph.py")
        sys.exit(1)

    skill_names = [s["name"] for s in skills if "name" in s]

    logger.info(
        f"Retrieved {len(success)} success pattern(s), "
        f"{len(failure)} failure pattern(s), "
        f"{len(skill_names)} skill(s)"
    )

    # --- Steps 2–4: Reason → Generate → Validate (with retries) ---
    result = generate_flow(
        goal=goal,
        success_patterns_json=json.dumps(success, indent=2),
        failure_patterns_json=json.dumps(failure, indent=2),
        available_skills_json=json.dumps(skills, indent=2),
        available_skill_names=skill_names,
        max_retries=max_retries,
    )

    # --- Output ---
    click.echo("\n" + "=" * 60)
    click.echo("REASONING TRACE")
    click.echo("=" * 60)
    click.echo(result["reasoning_trace"])
    click.echo("=" * 60 + "\n")

    # Normalise YAML formatting via round-trip through PyYAML
    parsed_dict = yaml.safe_load(result["flow_yaml"])
    normalised_yaml = yaml.dump(parsed_dict, default_flow_style=False, sort_keys=False)

    with open(output, "w", encoding="utf-8") as fh:
        fh.write(normalised_yaml)

    click.echo(
        f"Proposed flow written to '{output}' "
        f"(generated in {result['attempts']} attempt(s))."
    )

    # Print Contract 1 JSON (used by stream 3 API and integration tests)
    contract1 = {
        "goal": goal,
        "industry": industry,
        "reasoning_trace": result["reasoning_trace"],
        "proposed_flow": {
            "flow_id": parsed_dict.get("flow_id", ""),
            "steps": [
                {"skill": s.get("skill", ""), "params": s.get("params", {})}
                for s in parsed_dict.get("steps", [])
            ],
        },
        "status": "valid",
        "errors": [],
    }
    click.echo("\n" + "=" * 60)
    click.echo("CONTRACT 1 JSON (for API integration)")
    click.echo("=" * 60)
    click.echo(json.dumps(contract1, indent=2))


if __name__ == "__main__":
    main()
