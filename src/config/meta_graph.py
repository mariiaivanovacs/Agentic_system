"""
meta_graph.py — writes the schema itself as :SchemaNode and :SchemaEdge nodes
into Neo4j so the agent can introspect available node types at runtime.

Usage:
    python -m src.config.meta_graph
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

from src.config.schema_validator import SchemaValidator


def write_meta_graph():
    schema_path = Path(__file__).parent / "schema.yaml"
    validator = SchemaValidator(schema_path)

    import yaml
    with open(schema_path) as f:
        raw = yaml.safe_load(f)

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session(database=database) as session:
        for label, defn in raw["nodes"].items():
            session.run("""
                MERGE (s:SchemaNode {label: $label})
                SET s.required_fields = $required,
                    s.optional_fields = $optional
            """, {
                "label": label,
                "required": defn.get("required", []),
                "optional": defn.get("optional", []),
            })

        for rel_type, defn in raw["relationships"].items():
            session.run("""
                MERGE (e:SchemaEdge {rel_type: $rel_type})
                SET e.from_label = $from_label,
                    e.to_label   = $to_label
            """, {
                "rel_type": rel_type,
                "from_label": defn["from"],
                "to_label": defn["to"],
            })

    driver.close()
    print(f"Meta-graph written: {len(raw['nodes'])} SchemaNode(s), "
          f"{len(raw['relationships'])} SchemaEdge(s)")


if __name__ == "__main__":
    write_meta_graph()
