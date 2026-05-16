"""
GraphRAG Generator — Stream 2 (Leila).

Two public entry points:
  generate_flow_proposal(goal, industry) -> FlowProposal
      High-level: retrieves context, builds prompt, calls Gemini, validates.
      Used by src/graphrag/main_graphrag.py and nodes.py (P1 wiring).

  generate_flow(goal, success_patterns_json, ...) -> dict
      Low-level: caller supplies pre-fetched context as JSON strings.
      Used by the CLI (root main_graphrag.py) for backward compatibility.

LLM factory:
  Uses langchain_google_genai.ChatGoogleGenerativeAI (same as nodes.py _llm())
  so both paths share the same model config.
  Env var: GOOGLE_API_KEY (falls back to GEMINI_API_KEY for legacy .env files).
"""
from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger

from .models import FlowProposal, RetrievedContext
from .prompt_engine import (
    build_planner_prompt,
    build_prompt,
    extract_reasoning_trace,
    extract_yaml_block,
)
from .validator import validate_flow_yaml

MODEL_NAME = "gemini-2.5-flash"


# --------------------------------------------------------------------------- #
# Shared LLM factory — matches nodes.py _llm() pattern                        #
# --------------------------------------------------------------------------- #

def _make_llm():
    """Returns a ChatGoogleGenerativeAI instance, matching the Agentic_system pattern."""
    from langchain_google_genai import ChatGoogleGenerativeAI  # lazy import

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No LLM API key found. Set GOOGLE_API_KEY (or GEMINI_API_KEY) in your .env"
        )
    # Ensure GOOGLE_API_KEY is in env so langchain_google_genai internal checks pass
    os.environ.setdefault("GOOGLE_API_KEY", api_key)
    return ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=api_key,
        temperature=0.2,
    )


# --------------------------------------------------------------------------- #
# High-level entry point — used by main_graphrag module and nodes.py           #
# --------------------------------------------------------------------------- #

def generate_flow_proposal(
    goal: str,
    industry: str,
    max_retries: int = 3,
) -> FlowProposal:
    """
    Full GraphRAG pipeline in one call:
      1. retrieve_context(industry, goal)
      2. build_planner_prompt(goal, context)
      3. Call Gemini → extract reasoning + YAML
      4. validate_flow_yaml against available skill names
      5. Return FlowProposal

    Raises RuntimeError if all retries are exhausted.
    """
    from .retriever import retrieve_context  # relative; works as src.graphrag or graphrag

    context: RetrievedContext = retrieve_context(industry, goal)
    skill_names = [s["name"] for s in context.available_skills if "name" in s]

    if not skill_names:
        raise RuntimeError(
            "No Skill nodes in the graph. Run: python scripts/seed_graph.py"
        )

    llm = _make_llm()
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"generate_flow_proposal — attempt {attempt}/{max_retries}")

        prompt = build_planner_prompt(goal, context)
        if last_error and attempt > 1:
            prompt += (
                f"\n\n### PREVIOUS ATTEMPT FAILED — FIX REQUIRED:\n"
                f"{last_error}\n\n"
                f"Please correct the YAML. Only use skill names from AVAILABLE SKILLS."
            )

        response = llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else str(response)

        reasoning_trace = extract_reasoning_trace(response_text)
        yaml_block = extract_yaml_block(response_text)

        if yaml_block is None:
            last_error = "No ```yaml block found. You MUST include a fenced YAML block."
            logger.warning(f"Attempt {attempt}: {last_error}")
            continue

        validation = validate_flow_yaml(yaml_block, skill_names)
        if validation["valid"]:
            parsed = validation["parsed"]
            skills_used = [step.skill for step in parsed.steps]
            logger.success(f"Flow validated on attempt {attempt}")
            return FlowProposal(
                flow_yaml=yaml_block,
                reasoning_trace=reasoning_trace,
                skills_used=skills_used,
                attempts=attempt,
            )

        last_error = "; ".join(validation["errors"])
        logger.warning(f"Attempt {attempt} validation failed: {last_error}")

    raise RuntimeError(
        f"Flow generation failed after {max_retries} attempts. Last error: {last_error}"
    )


# --------------------------------------------------------------------------- #
# Low-level entry point — backward compat with CLI (root main_graphrag.py)    #
# --------------------------------------------------------------------------- #

def generate_flow(
    goal: str,
    success_patterns_json: str,
    failure_patterns_json: str,
    available_skills_json: str,
    available_skill_names: list[str],
    max_retries: int = 3,
) -> dict:
    """
    Retrieve → Reason → Generate → Validate loop.

    Returns:
        {
            "reasoning_trace": str,
            "flow_yaml": str,
            "parsed_flow": FlowYAML,
            "attempts": int,
        }

    Raises RuntimeError if all retries are exhausted.
    """
    llm = _make_llm()
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"Generating flow — attempt {attempt}/{max_retries}")

        prompt = build_prompt(
            goal=goal,
            success_patterns_json=success_patterns_json,
            failure_patterns_json=failure_patterns_json,
            available_skills_json=available_skills_json,
            prior_error=last_error if attempt > 1 else None,
        )

        response = llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else str(response)

        reasoning_trace = extract_reasoning_trace(response_text)
        yaml_block = extract_yaml_block(response_text)

        if yaml_block is None:
            last_error = (
                "No ```yaml block found in response. "
                "You must include a fenced YAML block."
            )
            logger.warning(f"Attempt {attempt}: {last_error}")
            continue

        validation = validate_flow_yaml(yaml_block, available_skill_names)
        if validation["valid"]:
            logger.success(f"Flow generated and validated on attempt {attempt}")
            return {
                "reasoning_trace": reasoning_trace,
                "flow_yaml": yaml_block,
                "parsed_flow": validation["parsed"],
                "attempts": attempt,
            }

        last_error = "; ".join(validation["errors"])
        logger.warning(f"Attempt {attempt} validation failed: {last_error}")

    raise RuntimeError(
        f"Flow generation failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )
