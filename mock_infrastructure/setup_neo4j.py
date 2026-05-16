"""
Seeds Neo4j with Graph A (historical data) and Graph B (functional blueprint).
Run once after setting credentials in .env:
    python mock_infrastructure/setup_neo4j.py

Idempotent — clears existing nodes before inserting.
"""
import os
import json
import csv
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

DATA_DIR = Path(__file__).parent / "data"


def seed(driver):
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    with driver.session(database=db) as s:
        # ------------------------------------------------------------------ #
        # Wipe existing data (dev only)                                       #
        # ------------------------------------------------------------------ #
        s.run("MATCH (n) DETACH DELETE n")
        print("Cleared existing nodes.")

        # ================================================================== #
        # GRAPH B — Functional Blueprint                                      #
        # ================================================================== #

        # Servers
        s.run(
            "CREATE (:Server {id: 'server_1', load: 0.90, error_rate: 0.05}), "
            "       (:Server {id: 'server_2', load: 0.10, error_rate: 0.00})"
        )

        # Connectors
        connectors = [
            {"id": "sql_connector_v1",  "type": "SQL",  "version": "1.0", "language": "Python"},
            {"id": "csv_connector_v1",  "type": "CSV",  "version": "1.0", "language": "Python"},
            {"id": "json_connector_v1", "type": "JSON", "version": "1.0", "language": "Python"},
        ]
        for c in connectors:
            s.run(
                "CREATE (:Connector {id: $id, type: $type, version: $version, language: $language})",
                **c,
            )

        # Skills
        skills = [
            {
                "id": "filter_by_industry_exact",
                "name": "Filter By Industry (Exact)",
                "input_schema":  '{"industry": "string"}',
                "output_schema": '{"mentors": "list"}',
            },
            {
                "id": "random_shuffle",
                "name": "Random Shuffle",
                "input_schema":  '{"items": "list"}',
                "output_schema": '{"items": "list"}',
            },
            {
                "id": "semantic_similarity",
                "name": "Semantic Similarity Matcher",
                "input_schema":  '{"startup_profile": "object", "mentor_profiles": "list"}',
                "output_schema": '{"ranked_mentors": "list", "scores": "list"}',
            },
            {
                "id": "fuzzy_industry_match",
                "name": "Fuzzy Industry Match",
                "input_schema":  '{"industry": "string"}',
                "output_schema": '{"mentors": "list", "confidence": "float"}',
            },
        ]
        for sk in skills:
            s.run(
                "CREATE (:Skill {id: $id, name: $name, "
                "input_schema: $input_schema, output_schema: $output_schema})",
                **sk,
            )

        # Flows
        legacy_yaml = Path(__file__).parent / "flows" / "flow_legacy_v1.yaml"
        legacy_yaml_str = legacy_yaml.read_text() if legacy_yaml.exists() else ""

        s.run(
            "CREATE (:Flow {id: 'legacy_matcher_v1', status: 'active', yaml_config: $yaml})",
            yaml=legacy_yaml_str,
        )

        # Flow -> Skill relationships
        s.run(
            "MATCH (f:Flow {id: 'legacy_matcher_v1'}), (s1:Skill {id: 'filter_by_industry_exact'}), "
            "      (s2:Skill {id: 'random_shuffle'}) "
            "CREATE (f)-[:USES_SKILL]->(s1), (f)-[:USES_SKILL]->(s2)"
        )

        # Flow -> Connector
        s.run(
            "MATCH (f:Flow {id: 'legacy_matcher_v1'}), (c:Connector {id: 'sql_connector_v1'}) "
            "CREATE (f)-[:USES_CONNECTOR]->(c)"
        )

        # Flow -> Server
        s.run(
            "MATCH (f:Flow {id: 'legacy_matcher_v1'}), (sv:Server {id: 'server_1'}) "
            "CREATE (f)-[:RUNS_ON]->(sv)"
        )
        print("Graph B seeded.")

        # ================================================================== #
        # GRAPH A — Historical Data                                           #
        # ================================================================== #

        # Companies (from SQLite — replicated here for Graph A)
        companies = [
            {"id": "c1",  "industry": "Fintech",      "stage": "Seed",      "pain_points": ["payments", "compliance"]},
            {"id": "c2",  "industry": "Healthtech",    "stage": "Series A",  "pain_points": ["FDA approval", "data privacy"]},
            {"id": "c3",  "industry": "E-commerce",    "stage": "Pre-seed",  "pain_points": ["logistics", "conversion"]},
            {"id": "c4",  "industry": "Fintech",       "stage": "Seed",      "pain_points": ["crypto regulation", "DeFi"]},
            {"id": "c5",  "industry": "AI",            "stage": "Seed",      "pain_points": ["model deployment", "data"]},
            {"id": "c6",  "industry": "Fintech",       "stage": "Series A",  "pain_points": ["lending", "risk scoring"]},
            {"id": "c7",  "industry": "Fintech",       "stage": "Pre-seed",  "pain_points": ["regulatory tech"]},
            {"id": "c8",  "industry": "AI",            "stage": "Seed",      "pain_points": ["NLP", "customer support"]},
            {"id": "c9",  "industry": "SaaS",          "stage": "Series A",  "pain_points": ["churn", "enterprise sales"]},
            {"id": "c10", "industry": "E-commerce",    "stage": "Pre-seed",  "pain_points": ["checkout UX", "inventory"]},
        ]
        for co in companies:
            s.run(
                "CREATE (:Company {id: $id, industry: $industry, stage: $stage, pain_points: $pain_points})",
                **co,
            )

        # Mentors (from CSV)
        mentors = []
        csv_path = DATA_DIR / "mentors_raw.csv"
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=1):
                expertise = [e.strip() for e in row["Expertise"].split(",")]
                mentors.append({
                    "id": f"m{i}",
                    "name": row["Name"],
                    "expertise": expertise,
                    "availability": row["Availability"],
                })
        for m in mentors:
            s.run(
                "CREATE (:Mentor {id: $id, expertise: $expertise, availability: $availability})",
                **m,
            )

        # Outcomes + ExecutionTraces from JSON matches
        json_path = DATA_DIR / "matches_2025.json"
        matches = json.loads(json_path.read_text())

        mentor_name_to_id = {m["name"]: m["id"] for m in mentors}
        now = datetime.now(timezone.utc).isoformat()

        for i, match in enumerate(matches):
            score = match.get("outcome_score") or match.get("rating", 0.0)
            company_id = f"c{match['startup_id']}"
            mentor_id = mentor_name_to_id.get(match["mentor_name"])
            if not mentor_id:
                continue

            outcome_id = f"o{i}"
            trace_id = f"et{i}"

            s.run(
                "CREATE (:Outcome {id: $id, score: $score, feedback: $feedback, date: $date})",
                id=outcome_id, score=float(score), feedback=match.get("note", ""), date=now,
            )
            s.run(
                "CREATE (:ExecutionTrace {id: $id, start_time: $t, end_time: $t, "
                "status: 'completed', error_log: ''})",
                id=trace_id, t=now,
            )
            s.run(
                "MATCH (c:Company {id: $cid}), (m:Mentor {id: $mid}) "
                "CREATE (c)-[:MATCHED_WITH {program: 'Cohort2025', date: $date}]->(m)",
                cid=company_id, mid=mentor_id, date=now,
            )
            s.run(
                "MATCH (m:Mentor {id: $mid}), (o:Outcome {id: $oid}) CREATE (m)-[:PRODUCED]->(o)",
                mid=mentor_id, oid=outcome_id,
            )
            s.run(
                "MATCH (et:ExecutionTrace {id: $etid}), (f:Flow {id: 'legacy_matcher_v1'}), "
                "      (c:Company {id: $cid}), (o:Outcome {id: $oid}), (sv:Server {id: 'server_1'}) "
                "CREATE (et)-[:RAN_FLOW]->(f), (et)-[:PROCESSED_COMPANY]->(c), "
                "       (et)-[:RESULTED_IN]->(o), (et)-[:USED_SERVER]->(sv)",
                etid=trace_id, cid=company_id, oid=outcome_id,
            )

        print("Graph A seeded.")
        print("Neo4j setup complete.")


def main():
    uri      = os.environ["NEO4J_URI"]
    username = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ["NEO4J_PASSWORD"]
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        seed(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
