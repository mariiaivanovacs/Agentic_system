"""
graph_writer.py — writes IndexedSystem output to Neo4j.

Validates each node against SchemaValidator, uses MERGE for idempotency,
and writes an IndexRun meta-node recording source + timestamp + counts.
"""

import os
import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase

from src.config.schema_validator import SchemaValidator
from src.indexer.base_indexer import IndexedSystem


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

        driver.close()
        return run_id
