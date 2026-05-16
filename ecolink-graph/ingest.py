import json
import csv
import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

URI      = os.getenv("NEO4J_URI")
USERNAME = os.getenv("NEO4J_USERNAME")
PASSWORD = os.getenv("NEO4J_PASSWORD")
DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

def env_float(name, default):
    return float(os.getenv(name, default))


driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD),
    connection_timeout=env_float("NEO4J_CONNECTION_TIMEOUT_SECONDS", "5"),
    connection_acquisition_timeout=env_float(
        "NEO4J_CONNECTION_ACQUISITION_TIMEOUT_SECONDS",
        "5",
    ),
    max_transaction_retry_time=env_float("NEO4J_MAX_TRANSACTION_RETRY_SECONDS", "10"),
)


# ── CLEAR ─────────────────────────────────────────────────────────────────────
def clear_all(tx):
    tx.run("MATCH (n) DETACH DELETE n")


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH A — HISTORY
# ══════════════════════════════════════════════════════════════════════════════

def create_company(tx, c):
    tx.run("""
        MERGE (co:Company {id: $id})
        SET co.name         = $name,
            co.industry     = $industry,
            co.stage        = $stage,
            co.pain_points  = $pain_points,
            co.revenue      = $revenue,
            co.founded_year = $founded_year
    """, **c)

def create_mentor(tx, m):
    tx.run("""
        MERGE (me:Mentor {id: $id})
        SET me.name               = $name,
            me.expertise_tags     = $expertise_tags,
            me.industry_focus     = $industry_focus,
            me.availability       = $availability,
            me.past_success_score = $past_success_score,
            me.years_experience   = $years_experience
    """, **m)

def create_interaction(tx, row):
    tx.run("""
        MATCH (co:Company {id: $company_id})
        MATCH (me:Mentor  {id: $mentor_id})
        MERGE (co)-[r:MATCHED_WITH {interaction_id: $interaction_id}]->(me)
        SET r.programme_name = $programme_name,
            r.outcome_score  = toFloat($outcome_score),
            r.feedback       = $feedback,
            r.date           = $date
    """, **row)

def create_programme(tx, name):
    tx.run("MERGE (p:Programme {name: $name})", name=name)

def link_company_to_programme(tx, company_id, programme_name):
    tx.run("""
        MATCH (co:Company  {id:   $company_id})
        MATCH (p:Programme {name: $programme_name})
        MERGE (co)-[:ENROLLED_IN]->(p)
    """, company_id=company_id, programme_name=programme_name)


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH B — BLUEPRINT
# ══════════════════════════════════════════════════════════════════════════════

def create_connector(tx, c):
    tx.run("""
        MERGE (cn:Connector {id: $id})
        SET cn.name        = $name,
            cn.type        = $type,
            cn.description = $description,
            cn.version     = $version,
            cn.status      = $status,
            cn.error_rate  = $error_rate
    """, **c)

def create_skill(tx, s):
    tx.run("""
        MERGE (sk:Skill {id: $id})
        SET sk.name              = $name,
            sk.description       = $description,
            sk.language          = $language,
            sk.performance_score = $performance_score,
            sk.avg_execution_ms  = $avg_execution_ms
    """, **s)

def create_server(tx, s):
    tx.run("""
        MERGE (sv:Server {id: $id})
        SET sv.name               = $name,
            sv.cpu_capacity       = $cpu_capacity,
            sv.current_load       = $current_load,
            sv.status             = $status,
            sv.error_rate_history = $error_rate_history,
            sv.region             = $region
    """, **s)

def create_flow(tx, f):
    tx.run("""
        MERGE (fl:Flow {id: $id})
        SET fl.name              = $name,
            fl.description       = $description,
            fl.status            = $status,
            fl.avg_outcome_score = $avg_outcome_score
    """, id=f["id"], name=f["name"], description=f["description"],
         status=f["status"], avg_outcome_score=f["avg_outcome_score"])

def link_flow_to_skill(tx, flow_id, skill_id):
    tx.run("""
        MATCH (fl:Flow  {id: $flow_id})
        MATCH (sk:Skill {id: $skill_id})
        MERGE (fl)-[:USES]->(sk)
    """, flow_id=flow_id, skill_id=skill_id)

def link_flow_to_connector(tx, flow_id, connector_id):
    tx.run("""
        MATCH (fl:Flow      {id: $flow_id})
        MATCH (cn:Connector {id: $connector_id})
        MERGE (fl)-[:READS_FROM]->(cn)
    """, flow_id=flow_id, connector_id=connector_id)

def link_flow_to_server(tx, flow_id, server_id):
    tx.run("""
        MATCH (fl:Flow   {id: $flow_id})
        MATCH (sv:Server {id: $server_id})
        MERGE (fl)-[:RUNS_ON]->(sv)
    """, flow_id=flow_id, server_id=server_id)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    driver.verify_connectivity()

    with driver.session(database=DATABASE) as session:

        print("Clearing existing data...")
        session.execute_write(clear_all)

        # ── Graph A ───────────────────────────────────────────────────────────
        companies = json.load(open("data/companies.json"))
        for c in companies:
            session.execute_write(create_company, c)
        print(f"[Graph A] Loaded {len(companies)} companies")

        mentors = json.load(open("data/mentors.json"))
        for m in mentors:
            session.execute_write(create_mentor, dict(m))
        print(f"[Graph A] Loaded {len(mentors)} mentors")

        programmes_seen = set()
        with open("data/interactions.csv", newline="", encoding="latin-1") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            pname = row["programme_name"]
            if pname not in programmes_seen:
                session.execute_write(create_programme, pname)
                programmes_seen.add(pname)
            session.execute_write(create_interaction, row)
            session.execute_write(link_company_to_programme, row["company_id"], pname)
        print(f"[Graph A] Loaded {len(rows)} interactions and {len(programmes_seen)} programmes")

        # ── Graph B ───────────────────────────────────────────────────────────
        connectors = json.load(open("data/connectors.json"))
        for c in connectors:
            session.execute_write(create_connector, c)
        print(f"[Graph B] Loaded {len(connectors)} connectors")

        skills = json.load(open("data/skills.json"))
        for s in skills:
            session.execute_write(create_skill, s)
        print(f"[Graph B] Loaded {len(skills)} skills")

        servers = json.load(open("data/servers.json"))
        for s in servers:
            session.execute_write(create_server, s)
        print(f"[Graph B] Loaded {len(servers)} servers")

        flows = json.load(open("data/flows.json"))
        for fl in flows:
            session.execute_write(create_flow, fl)
            for skill_id in fl["skills_used"]:
                session.execute_write(link_flow_to_skill, fl["id"], skill_id)
            session.execute_write(link_flow_to_connector, fl["id"], fl["connector_used"])
            session.execute_write(link_flow_to_server, fl["id"], fl["server_id"])
        print(f"[Graph B] Loaded {len(flows)} flows with skills, connectors and servers")

        print("\nFull dual graph is ready!")
        print("Graph A: companies, mentors, interactions, programmes")
        print("Graph B: connectors, skills, flows, servers")

    driver.close()


if __name__ == "__main__":
    main()
