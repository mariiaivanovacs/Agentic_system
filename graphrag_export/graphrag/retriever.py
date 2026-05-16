"""
GraphRAG Retriever — Stream 2 (Leila).

Provides two interfaces:
  1. Neo4jRetriever class — used by the CLI (main_graphrag.py)
  2. retrieve_context() module function — used by generator and nodes.py (P1)

Design notes:
  - No raw Cypher outside this file.  When Stream 1 delivers get_success_patterns()
    and get_failure_patterns() in ecolink-graph/queries.py, swap in the adapter at
    the bottom of this file and the rest of the code stays unchanged.
  - COALESCE(r.outcome_score, r.score, 0.0) makes queries compatible with both
    scripts/seed_graph.py (uses r.score) and ecolink-graph/ingest.py (uses r.outcome_score).
  - All DB errors are logged and re-raised; callers decide on fallback strategy.
"""
from __future__ import annotations

import os

import neo4j
import neo4j.exceptions
from loguru import logger

from .models import RetrievedContext

# --------------------------------------------------------------------------- #
# Neo4j helper                                                                  #
# --------------------------------------------------------------------------- #

def _make_driver() -> neo4j.Driver:
    uri = (
        os.environ["NEO4J_URI"]
        .replace("neo4j+s://", "neo4j://")
        .replace("neo4j+ssc://", "neo4j://")
    )
    return neo4j.GraphDatabase.driver(
        uri,
        auth=(
            os.environ.get("NEO4J_USERNAME", "neo4j"),
            os.environ["NEO4J_PASSWORD"],
        ),
        encrypted=True,
        trusted_certificates=neo4j.TrustAll(),
    )


# --------------------------------------------------------------------------- #
# Retriever class (Graph A + Graph B, read-only)                               #
# --------------------------------------------------------------------------- #

class Neo4jRetriever:
    """Read-only access to Graph A (historical matches) and Graph B (skills, infra)."""

    def __init__(self) -> None:
        self._driver = _make_driver()
        self._driver.verify_connectivity()
        logger.info("Neo4jRetriever connected")

    def close(self) -> None:
        self._driver.close()

    # ── Graph A queries ──────────────────────────────────────────────────────

    def retrieve_success_patterns(
        self, industry: str, min_score: float = 7.0
    ) -> list[dict]:
        """Top successful Company→Mentor matches for the industry."""
        cypher = (
            "MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor) "
            "WHERE c.industry = $industry "
            "  AND COALESCE(r.outcome_score, r.score, 0.0) >= $min_score "
            "RETURN c.name AS company_name, "
            "       c.pain_points AS company_pain_points, "
            "       c.stage AS company_stage, "
            "       m.name AS mentor_name, "
            "       COALESCE(m.expertise, m.expertise_tags, '') AS mentor_expertise, "
            "       COALESCE(m.available, m.availability, '') AS mentor_availability, "
            "       COALESCE(r.outcome_score, r.score, 0.0) AS score, "
            "       r.feedback AS feedback "
            "ORDER BY score DESC "
            "LIMIT 10"
        )
        return self._run(cypher, industry=industry, min_score=min_score)

    def retrieve_failure_patterns(
        self, industry: str, max_score: float = 4.0
    ) -> list[dict]:
        """Top failed Company→Mentor matches for the industry."""
        cypher = (
            "MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor) "
            "WHERE c.industry = $industry "
            "  AND COALESCE(r.outcome_score, r.score, 0.0) <= $max_score "
            "RETURN c.name AS company_name, "
            "       c.pain_points AS company_pain_points, "
            "       m.name AS mentor_name, "
            "       COALESCE(m.expertise, m.expertise_tags, '') AS mentor_expertise, "
            "       COALESCE(r.outcome_score, r.score, 0.0) AS score, "
            "       r.feedback AS feedback "
            "ORDER BY score ASC "
            "LIMIT 10"
        )
        return self._run(cypher, industry=industry, max_score=max_score)

    # ── Graph B queries ──────────────────────────────────────────────────────

    def get_available_skills(self) -> list[dict]:
        """All Skill nodes from Graph B."""
        cypher = (
            "MATCH (s:Skill) "
            "RETURN s.name AS name, "
            "       s.description AS description, "
            "       s.input_schema AS input_schema, "
            "       s.performance_score AS performance_score"
        )
        return self._run(cypher)

    def get_infrastructure_status(self) -> dict:
        """Server load map from Graph B: {server_id: {load, error_rate}}."""
        cypher = (
            "MATCH (s:Server) "
            "RETURN s.id AS id, "
            "       COALESCE(s.current_load, 0) / 100.0 AS load, "
            "       COALESCE(last(s.error_rate_history), 0.0) AS error_rate, "
            "       s.status AS status"
        )
        records = self._run(cypher)
        return {
            r["id"]: {
                "load": r["load"],
                "error_rate": r["error_rate"],
                "status": r.get("status", "unknown"),
            }
            for r in records
            if r.get("id")
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _run(self, cypher: str, **params) -> list[dict]:
        try:
            with self._driver.session() as session:
                result = session.run(cypher, **params)
                records = result.data()
                return records if records else []
        except neo4j.exceptions.Neo4jError as exc:
            logger.error(
                f"Cypher failed | query={cypher!r} | params={params} | error={exc}"
            )
            raise RuntimeError(f"Database query failed: {exc}") from exc


# --------------------------------------------------------------------------- #
# Module-level convenience function — used by generator and nodes.py (P1)      #
# --------------------------------------------------------------------------- #

def retrieve_context(
    industry: str,
    goal: str,  # noqa: ARG001 — reserved for future semantic filtering
    min_score: float = 7.0,
    max_score: float = 4.0,
) -> RetrievedContext:
    """
    Primary entry point for the GraphRAG pipeline.

    Tries Neo4j first; falls back to MockRetriever if the database is unreachable.
    Returns a RetrievedContext ready for prompt_engine.build_planner_prompt().

    Stream 1 integration note:
        When ecolink-graph/queries.py gains get_success_patterns() and
        get_failure_patterns(), replace the Neo4jRetriever calls below with:
            from ecolink_graph.queries import get_success_patterns, get_failure_patterns
            success = get_success_patterns(industry, min_score)
            failure = get_failure_patterns(industry, max_score)
        Everything else (skills, infra, RetrievedContext assembly) stays unchanged.
    """
    from .mock_retriever import MockRetriever  # local import avoids cycle; relative = works as both src.graphrag and graphrag package

    try:
        retriever: Neo4jRetriever | MockRetriever = Neo4jRetriever()
        logger.info("retrieve_context: using live Neo4j")
    except Exception as exc:
        logger.warning(f"Neo4j unavailable ({exc}) — falling back to MockRetriever")
        retriever = MockRetriever()

    try:
        success = retriever.retrieve_success_patterns(industry, min_score)
        failure = retriever.retrieve_failure_patterns(industry, max_score)
        skills = retriever.get_available_skills()
        infra = retriever.get_infrastructure_status()
    finally:
        retriever.close()

    return RetrievedContext(
        success_patterns=success,
        failure_patterns=failure,
        available_skills=skills,
        infra_status=infra,
    )
