"""
graph_writer.py — writes IndexedSystem output to Neo4j.

Validates each node against SchemaValidator, uses MERGE for idempotency,
and writes an IndexRun meta-node recording source + timestamp + counts.
"""

import os
import re
import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase

from src.config.schema_validator import SchemaValidator
from src.indexer.base_indexer import CodeNodeSpec, IndexedSystem


CODE_NODE_LABELS = {
    "Project",
    "Repository",
    "Package",
    "File",
    "Module",
    "Route",
    "Controller",
    "Service",
    "Function",
    "DatabaseModel",
    "DatabaseTable",
    "DataStore",
    "Entity",
    "Workflow",
    "BusinessFlow",
    "FlowStep",
    "Integration",
    "Artifact",
    "Risk",
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return slug or "root"


def _driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    )


class GraphWriter:
    def __init__(self):
        self._validator = SchemaValidator()
        self._database = os.getenv("NEO4J_DATABASE", "neo4j")

    def write(self, system: IndexedSystem) -> str:
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        source = system.metadata.get("source", "unknown")
        source_type = system.metadata.get("source_type", "unknown")

        driver = _driver()
        with driver.session(database=self._database) as session:
            session.run("""
                MERGE (r:IndexRun {id: $id})
                SET r.source      = $source,
                    r.source_type = $source_type,
                    r.indexed_at  = $now,
                    r.connector_count = $cc,
                    r.skill_count     = $sc,
                    r.flow_count      = $fc
            """, {
                "id": run_id, "source": source, "source_type": source_type,
                "now": now,
                "cc": len(system.connectors),
                "sc": len(system.skills),
                "fc": len(system.flows),
            })

            for c in system.connectors:
                props = c.__dict__
                self._validator.validate_node("Connector", props)
                session.run("""
                    MERGE (cn:Connector {id: $id})
                    SET cn.name        = $name,
                        cn.type        = $type,
                        cn.description = $description,
                        cn.version     = $version,
                        cn.status      = $status,
                        cn.error_rate  = $error_rate
                """, props)
                session.run("""
                    MATCH (cn:Connector {id: $cid})
                    MATCH (r:IndexRun  {id: $rid})
                    MERGE (cn)-[:INDEXED_BY]->(r)
                """, {"cid": c.id, "rid": run_id})

            for s in system.skills:
                props = s.__dict__
                self._validator.validate_node("Skill", props)
                session.run("""
                    MERGE (sk:Skill {id: $id})
                    SET sk.name              = $name,
                        sk.description       = $description,
                        sk.language          = $language,
                        sk.performance_score = $performance_score,
                        sk.avg_execution_ms  = $avg_execution_ms
                """, props)
                session.run("""
                    MATCH (sk:Skill   {id: $sid})
                    MATCH (r:IndexRun {id: $rid})
                    MERGE (sk)-[:DISCOVERED_BY]->(r)
                """, {"sid": s.id, "rid": run_id})

            for f in system.flows:
                props = f.__dict__
                self._validator.validate_node("Flow", props)
                session.run("""
                    MERGE (fl:Flow {id: $id})
                    SET fl.name              = $name,
                        fl.description       = $description,
                        fl.status            = $status,
                        fl.avg_outcome_score = $avg_outcome_score
                """, props)

            self._write_code_graph(session, system, run_id)

        driver.close()
        return run_id

    def _write_code_graph(self, session, system: IndexedSystem, run_id: str) -> None:
        project_id = system.metadata.get("project_id")
        if project_id:
            session.run(
                """
                MATCH (n)
                WHERE n.project_id = $project_id
                  AND any(label IN labels(n) WHERE label IN $labels)
                  AND NOT 'Project' IN labels(n)
                DETACH DELETE n
                """,
                {"project_id": project_id, "labels": sorted(CODE_NODE_LABELS - {"Project"})},
            )

        for node in system.code_nodes:
            self._validate_code_node(node)
            props = {
                "id": node.id,
                "name": node.name,
                "project_id": node.project_id,
                "scan_id": node.scan_id,
                "source_path": node.source_path,
                "confidence": node.confidence,
                **node.properties,
            }
            session.run(
                f"""
                MERGE (n:`{node.label}` {{id: $id}})
                SET n += $props
                """,
                {"id": node.id, "props": props},
            )
            if node.label != "Project":
                session.run(
                    """
                    MATCH (n {id: $node_id})
                    MATCH (r:IndexRun {id: $run_id})
                    MERGE (n)-[:DISCOVERED_BY]->(r)
                    """,
                    {"node_id": node.id, "run_id": run_id},
                )

        for rel in system.code_relationships:
            session.run(
                f"""
                MATCH (a {{id: $from_id}})
                MATCH (b {{id: $to_id}})
                MERGE (a)-[r:`{rel.rel_type}`]->(b)
                SET r += $props
                """,
                {
                    "from_id": rel.from_id,
                    "to_id": rel.to_id,
                    "props": rel.properties,
                },
            )

        for node in system.code_nodes:
            if node.label != "Function":
                continue
            skill_id = f"skill_{_slug(node.id)}"
            session.run(
                """
                MATCH (s:Skill {id: $skill_id})
                MATCH (fn:Function {id: $function_id})
                MERGE (s)-[:SKILL_DERIVED_FROM_FUNCTION]->(fn)
                """,
                {"skill_id": skill_id, "function_id": node.id},
            )

        project_id = system.metadata.get("project_id")
        scan_id = system.metadata.get("scan_id")
        if project_id and scan_id:
            session.run(
                """
                MATCH (p:Project {id: $project_id})
                SET p.last_scan_id = $scan_id,
                    p.analysis_status = 'analysis_complete',
                    p.permission_status = coalesce(p.permission_status, 'approved'),
                    p.updated_at = datetime()
                """,
                {"project_id": project_id, "scan_id": scan_id},
            )

    def _validate_code_node(self, node: CodeNodeSpec) -> None:
        if node.label not in CODE_NODE_LABELS:
            raise ValueError(f"Unsupported code node label: {node.label}")
        props = {
            "id": node.id,
            "name": node.name,
            "project_id": node.project_id,
            "scan_id": node.scan_id,
            "source_path": node.source_path,
            "confidence": node.confidence,
            **node.properties,
        }
        self._validator.validate_node(node.label, props)
