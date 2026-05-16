"""
pipeline_builder.py — discovers pipeline chains from extracted WebEntity nodes and
writes Pipeline nodes to Neo4j.

A pipeline is built by token-overlap matching across the three entity types that
the web indexer already extracts:

    Route  →  Feature  →  ContractMethod

Each chain where a Route overlaps with at least one Feature or ContractMethod
becomes a Pipeline node linked to the app's AppProfile.

Public API:
    build_and_write(domain) -> int   # discover + write; returns pipelines written
"""
from __future__ import annotations

import json
import logging
import os
import re

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Neo4j helpers                                                                #
# --------------------------------------------------------------------------- #

def _get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def _db() -> str:
    return os.environ.get("NEO4J_DATABASE", "neo4j")


# --------------------------------------------------------------------------- #
# Name-matching utilities                                                       #
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> set[str]:
    """Lowercase alphanum tokens longer than 2 chars from any name / path."""
    return {t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if len(t) > 2}


def _names_overlap(a: str, b: str) -> bool:
    """True when the two names share at least one meaningful token."""
    return bool(_tokens(a) & _tokens(b))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "root"


# --------------------------------------------------------------------------- #
# Neo4j write helpers                                                           #
# --------------------------------------------------------------------------- #

def _write_calls_edges(session, domain: str, routes: list[dict], methods: list[dict]) -> int:
    """Write Route -[:CALLS]-> ContractMethod edges based on token overlap."""
    written = 0
    for route in routes:
        for method in methods:
            if _names_overlap(route["name"], method["name"]):
                session.run(
                    """
                    MATCH (r:WebEntity {id: $route_id})
                    MATCH (m:WebEntity {id: $method_id})
                    MERGE (r)-[:CALLS]->(m)
                    """,
                    route_id=route["id"],
                    method_id=method["id"],
                )
                written += 1
    return written


def _write_uses_feature_edges(session, domain: str, routes: list[dict], features: list[dict]) -> int:
    """Write Route -[:USES_FEATURE]-> Feature edges based on token overlap."""
    written = 0
    for route in routes:
        for feature in features:
            if _names_overlap(route["name"], feature["name"]):
                session.run(
                    """
                    MATCH (r:WebEntity {id: $route_id})
                    MATCH (f:WebEntity {id: $feature_id})
                    MERGE (r)-[:USES_FEATURE]->(f)
                    """,
                    route_id=route["id"],
                    feature_id=feature["id"],
                )
                written += 1
    return written


# --------------------------------------------------------------------------- #
# Pipeline discovery (pure Python, no DB writes)                               #
# --------------------------------------------------------------------------- #

def discover_pipelines(domain: str) -> list[dict]:
    """
    Query Neo4j for routes, features, and contract methods for *domain*, then
    match them into pipeline chains by token overlap.

    Returns a list of pipeline dicts ready for write_pipelines().
    """
    driver = _get_driver()
    pipelines: list[dict] = []

    with driver.session(database=_db()) as session:
        routes = session.run(
            "MATCH (w:WebSite {domain: $d})-[:EXPOSES_ROUTE]->(r:WebEntity {entity_type: 'Route'}) "
            "RETURN r.id AS id, r.name AS name",
            d=domain,
        ).data()

        features = session.run(
            "MATCH (w:WebSite {domain: $d})-[:SITE_HAS_ENTITY]->(f:WebEntity {entity_type: 'Feature'}) "
            "RETURN f.id AS id, f.name AS name, f.description AS description",
            d=domain,
        ).data()

        methods = session.run(
            "MATCH (w:WebSite {domain: $d})-[:SITE_HAS_ENTITY]->(m:WebEntity {entity_type: 'ContractMethod'}) "
            "RETURN m.id AS id, m.name AS name, m.category AS category",
            d=domain,
        ).data()

        # Write graph edges while we have an open session
        _write_calls_edges(session, domain, routes, methods)
        _write_uses_feature_edges(session, domain, routes, features)

        # If no routes exist, create a synthetic "root" pipeline from all discovered entities
        if not routes and (features or methods):
            routes = [{"id": f"webentity_{_slug(domain)}_route_root", "name": "/"}]

    driver.close()

    for route in routes:
        route_name = route["name"]
        matched_features = [f for f in features if _names_overlap(route_name, f["name"])]
        matched_methods = [m for m in methods if _names_overlap(route_name, m["name"])]

        # Require at least one matched entity beyond the route itself
        if not matched_features and not matched_methods:
            continue

        steps: list[dict] = []
        entity_types: list[str] = ["Route"]

        steps.append({"step": 1, "type": "Route", "id": route["id"], "name": route_name})

        for feat in matched_features:
            steps.append({
                "step": len(steps) + 1,
                "type": "Feature",
                "id": feat["id"],
                "name": feat["name"],
                "description": feat.get("description", ""),
            })
            if "Feature" not in entity_types:
                entity_types.append("Feature")

        for method in matched_methods:
            steps.append({
                "step": len(steps) + 1,
                "type": "ContractMethod",
                "id": method["id"],
                "name": method["name"],
                "category": method.get("category", ""),
            })
            if "ContractMethod" not in entity_types:
                entity_types.append("ContractMethod")

        has_contract = any(s["type"] == "ContractMethod" for s in steps)
        display_name = route_name.strip("/").replace("/", " › ").replace("-", " ").title() or "Root"

        pipelines.append({
            "id": f"pipeline_{_slug(domain)}_{_slug(route_name)}",
            "name": f"{display_name} Pipeline",
            "app_id": domain,
            "entrypoint": route_name,
            "steps": steps,
            "entity_types": entity_types,
            "has_contract": has_contract,
            "step_count": len(steps),
        })

    logger.info("Discovered %d pipelines for domain %s.", len(pipelines), domain)
    return pipelines


# --------------------------------------------------------------------------- #
# Pipeline write                                                                #
# --------------------------------------------------------------------------- #

def write_pipelines(domain: str, pipelines: list[dict]) -> int:
    """Write Pipeline nodes (MERGE) and link them to the AppProfile."""
    if not pipelines:
        return 0

    driver = _get_driver()
    written = 0

    with driver.session(database=_db()) as session:
        for p in pipelines:
            session.run(
                """
                MERGE (pl:Pipeline {id: $id})
                SET pl.name         = $name,
                    pl.app_id       = $app_id,
                    pl.entrypoint   = $entrypoint,
                    pl.steps        = $steps,
                    pl.entity_types = $entity_types,
                    pl.has_contract = $has_contract,
                    pl.step_count   = $step_count,
                    pl.discovered_at = datetime()
                WITH pl
                OPTIONAL MATCH (ap:AppProfile {app_id: $app_id})
                FOREACH (_ IN CASE WHEN ap IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (ap)-[:HAS_PIPELINE]->(pl)
                )
                """,
                id=p["id"],
                name=p["name"],
                app_id=p["app_id"],
                entrypoint=p["entrypoint"],
                steps=json.dumps(p["steps"]),
                entity_types=p["entity_types"],
                has_contract=p["has_contract"],
                step_count=p["step_count"],
            )
            written += 1

    driver.close()
    logger.info("Wrote %d Pipeline nodes for domain %s.", written, domain)
    return written


# --------------------------------------------------------------------------- #
# Public entry point                                                            #
# --------------------------------------------------------------------------- #

def build_and_write(domain: str) -> int:
    """Discover pipelines for *domain* and write them to Neo4j. Returns count written."""
    pipelines = discover_pipelines(domain)
    return write_pipelines(domain, pipelines)
