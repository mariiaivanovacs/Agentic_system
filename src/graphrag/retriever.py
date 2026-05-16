from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any

from src.agents.tools import get_infrastructure_status, query_graph
from src.graphrag.models import RetrievedContext


logger = logging.getLogger(__name__)

_KNOWN_INDUSTRIES = ["Fintech", "Healthtech", "E-commerce", "Logistics", "SaaS", "Edtech"]


def _load_graph_queries():
    import sys

    graph_path = Path(__file__).resolve().parents[2] / "ecolink-graph"
    if str(graph_path) not in sys.path:
        sys.path.insert(0, str(graph_path))
    import queries as graph_queries  # type: ignore

    return graph_queries


def _run(cypher: str) -> list[dict[str, Any]]:
    return query_graph.invoke({"cypher_query": cypher})


def extract_industry(goal: str, industry_stats: list[dict[str, Any]] | None = None) -> str:
    goal_lower = goal.lower()
    for industry in _KNOWN_INDUSTRIES:
        if industry.lower() in goal_lower:
            return industry
    if industry_stats:
        return str(industry_stats[0].get("industry") or "Fintech")
    return "Fintech"


def get_industry_stats(limit: int = 6) -> list[dict[str, Any]]:
    return _run(
        f"""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        RETURN c.industry AS industry,
               round(avg(r.outcome_score), 2) AS avg_score,
               count(r) AS match_count
        ORDER BY avg_score ASC
        LIMIT {int(limit)}
        """
    )


def get_active_flows() -> list[dict[str, Any]]:
    return _run(
        """
        MATCH (f:Flow {status: 'active'})
        OPTIONAL MATCH (f)-[:USES]->(s:Skill)
        OPTIONAL MATCH (f)-[:READS_FROM]->(c:Connector)
        OPTIONAL MATCH (f)-[:RUNS_ON]->(sv:Server)
        RETURN f.id AS flow_id,
               coalesce(f.name, f.id) AS name,
               f.description AS description,
               f.avg_outcome_score AS avg_score,
               collect(DISTINCT s.id) AS skill_ids,
               collect(DISTINCT s.name) AS skill_names,
               c.id AS connector_id,
               sv.id AS server_id
        ORDER BY f.avg_outcome_score ASC
        """
    )


def get_available_skills() -> list[dict[str, Any]]:
    return _run(
        """
        MATCH (s:Skill)
        RETURN s.id AS id,
               s.name AS name,
               s.description AS description,
               s.language AS language,
               s.performance_score AS performance_score,
               s.avg_execution_ms AS avg_execution_ms
        ORDER BY coalesce(s.performance_score, 0) DESC, s.id
        """
    )


def get_available_connectors() -> list[dict[str, Any]]:
    return _run(
        """
        MATCH (c:Connector)
        RETURN c.id AS id,
               c.name AS name,
               c.type AS type,
               c.status AS status,
               c.error_rate AS error_rate
        ORDER BY c.id
        """
    )


def get_website_entities(limit: int = 30) -> list[dict[str, Any]]:
    return _run(
        f"""
        MATCH (w:WebSite)-[:SITE_HAS_ENTITY]->(e:WebEntity)
        RETURN w.domain AS domain,
               e.id AS id,
               e.name AS name,
               e.entity_type AS entity_type,
               e.category AS category,
               e.description AS description,
               e.source AS source
        ORDER BY e.entity_type, e.name
        LIMIT {int(limit)}
        """
    )


def get_software_nodes(limit: int = 60) -> list[dict[str, Any]]:
    return _run(
        f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN [
            'Project', 'Repository', 'File', 'Route', 'Service', 'Function',
            'DatabaseModel', 'DatabaseTable', 'DataStore', 'Entity', 'Workflow',
            'Integration', 'Artifact', 'Risk'
        ])
        RETURN labels(n)[0] AS type,
               n.id AS id,
               n.name AS name,
               n.project_id AS project_id,
               n.source_path AS source_path,
               n.confidence AS confidence
        ORDER BY type, source_path, name
        LIMIT {int(limit)}
        """
    )


def get_learning_events(industry: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    industry_clause = f"AND l.industry = {json.dumps(industry)}" if industry else ""
    rows = _run(
        f"""
        MATCH (l)
        WHERE 'LearningEvent' IN labels(l)
        {industry_clause}
        RETURN properties(l) AS props
        LIMIT {int(limit)}
        """
    )
    return [row.get("props", {}) for row in rows]


def retrieve_context(
    industry: str | None = None,
    goal: str = "",
    min_score: float = 7.0,
    max_score: float = 4.0,
) -> RetrievedContext:
    """Retrieve live graph context. This does not fall back to synthetic data."""
    graph_queries = _load_graph_queries()

    industry_stats = get_industry_stats()
    selected_industry = industry or extract_industry(goal, industry_stats)
    baseline_rows = graph_queries.get_industry_avg_score(selected_industry)
    baseline_score = baseline_rows[0]["avg_score"] if baseline_rows else 5.0

    context = RetrievedContext(
        goal=goal,
        industry=selected_industry,
        industry_stats=industry_stats,
        failure_patterns=graph_queries.get_failure_patterns(selected_industry, max_score=max_score)[:8],
        success_patterns=graph_queries.get_success_patterns(selected_industry, min_score=min_score)[:8],
        active_flows=get_active_flows(),
        available_skills=get_available_skills(),
        available_connectors=get_available_connectors(),
        infra_status=get_infrastructure_status.invoke({}),
        learning_events=get_learning_events(selected_industry),
        website_entities=get_website_entities(),
        software_nodes=get_software_nodes(),
        baseline_score=baseline_score,
    )
    logger.info(
        "GraphRAG context retrieved | industry=%s failures=%d successes=%d skills=%d flows=%d",
        context.industry,
        len(context.failure_patterns),
        len(context.success_patterns),
        len(context.available_skills),
        len(context.active_flows),
    )
    return context


def retrieve_semantic_context(
    query: str,
    top_k: int = 5,
    labels: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Vector similarity search over graph nodes.

    Generates a query embedding then searches the Neo4j vector index.
    Falls back to a keyword CONTAINS search when the vector index does not
    exist yet, so the function never raises on a fresh database.

    Args:
        query: Natural-language description of what to find.
        top_k: Number of top results to return.
        labels: Node labels to search. Defaults to ["Skill"].

    Returns:
        List of dicts with id, name, description, label, score.
    """
    if labels is None:
        labels = ["Skill"]

    from src.agents.tools import _run_read_cypher  # noqa: PLC0415

    try:
        from src.graphrag.embedder import generate_embedding  # noqa: PLC0415
        query_vector = generate_embedding(query)
    except Exception as exc:
        logger.warning("Embedding generation failed, skipping semantic search: %s", exc)
        return []

    # Try vector index first; fall back to keyword search on any exception
    try:
        rows = _run_read_cypher(
            f"""
            CALL db.index.vector.queryNodes('skill_embedding', {int(top_k)}, $query_vector)
            YIELD node, score
            RETURN node.id AS id,
                   node.name AS name,
                   node.description AS description,
                   labels(node)[0] AS label,
                   score
            """,
            {"query_vector": query_vector},
        )
        return rows
    except Exception as exc:
        logger.warning(
            "Vector index query failed (%s); falling back to keyword search.", exc
        )

    # Keyword fallback — works on any Neo4j instance without a vector index
    keyword = query[:120]
    results: list[dict[str, Any]] = []
    for label in labels:
        try:
            rows = _run_read_cypher(
                f"""
                MATCH (n:{label})
                WHERE toLower(n.description) CONTAINS toLower($keyword)
                   OR toLower(n.name) CONTAINS toLower($keyword)
                RETURN n.id AS id,
                       n.name AS name,
                       n.description AS description,
                       '{label}' AS label,
                       0.0 AS score
                LIMIT {int(top_k)}
                """,
                {"keyword": keyword},
            )
            results.extend(rows)
        except Exception as kw_exc:
            logger.warning("Keyword fallback also failed for %s: %s", label, kw_exc)
    return results[:top_k]
