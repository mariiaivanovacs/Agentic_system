"""
queries.py — all graph queries for the EcoLink system.
Your teammates (Agent person, UI person) import from this file.

Usage:
    from queries import get_failed_matches, get_best_mentors_by_industry
"""

import os
from neo4j import GraphDatabase, Query
from dotenv import load_dotenv

load_dotenv()

def env_float(name, default):
    return float(os.getenv(name, default))


driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    connection_timeout=env_float("NEO4J_CONNECTION_TIMEOUT_SECONDS", "5"),
    connection_acquisition_timeout=env_float(
        "NEO4J_CONNECTION_ACQUISITION_TIMEOUT_SECONDS",
        "5",
    ),
    max_transaction_retry_time=env_float("NEO4J_MAX_TRANSACTION_RETRY_SECONDS", "10"),
)

DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
QUERY_TIMEOUT = env_float("NEO4J_QUERY_TIMEOUT_SECONDS", "10")


# ── HELPER ────────────────────────────────────────────────────────────────────
def run_query(cypher, params=None):
    """Run any Cypher query and return list of dicts."""
    with driver.session(database=DATABASE) as session:
        result = session.run(Query(cypher, timeout=QUERY_TIMEOUT), params or {})
        return [dict(record) for record in result]


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH A QUERIES — History
# ══════════════════════════════════════════════════════════════════════════════

def get_failed_matches(threshold=5.0):
    """Find matches where outcome_score is below threshold. Agent uses this."""
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WHERE r.outcome_score < $threshold
        RETURN c.name AS company, c.industry AS industry,
               m.name AS mentor, m.expertise_tags AS mentor_skills,
               r.outcome_score AS score, r.feedback AS feedback,
               r.programme_name AS programme
        ORDER BY r.outcome_score ASC
    """, {"threshold": threshold})


def get_success_patterns(industry: str, min_score: float = 7.0):
    """Successful mentor matches for an industry — used by GraphRAG retriever."""
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WHERE c.industry = $industry AND r.outcome_score >= $min_score
        RETURN c.name AS company, c.pain_points AS pain_points,
               c.stage AS stage,
               m.name AS mentor, m.expertise_tags AS skills,
               r.outcome_score AS score, r.feedback AS feedback
        ORDER BY r.outcome_score DESC LIMIT 10
    """, {"industry": industry, "min_score": min_score})


def get_failure_patterns(industry: str, max_score: float = 4.0):
    """Failed mentor matches for an industry — used by GraphRAG retriever."""
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WHERE c.industry = $industry AND r.outcome_score <= $max_score
        RETURN c.name AS company, c.pain_points AS pain_points,
               m.name AS mentor, m.expertise_tags AS skills,
               r.outcome_score AS score, r.feedback AS feedback
        ORDER BY r.outcome_score ASC LIMIT 10
    """, {"industry": industry, "max_score": max_score})


def get_best_mentors_by_industry(industry):
    """Top mentors for a given industry based on past scores."""
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WHERE c.industry = $industry AND r.outcome_score >= 7
        RETURN m.name AS mentor, m.expertise_tags AS skills,
               m.industry_focus AS focus,
               avg(r.outcome_score) AS avg_score,
               count(r) AS total_matches
        ORDER BY avg_score DESC
        LIMIT 5
    """, {"industry": industry})


def get_company_history(company_id):
    """All past mentor matches for a specific company."""
    return run_query("""
        MATCH (c:Company {id: $company_id})-[r:MATCHED_WITH]->(m:Mentor)
        RETURN c.name AS company, m.name AS mentor,
               r.outcome_score AS score, r.date AS date,
               r.programme_name AS programme
        ORDER BY r.date DESC
    """, {"company_id": company_id})


def get_unmatched_companies():
    """Companies that have never had a match scoring above 6."""
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WITH c, max(r.outcome_score) AS best_score
        WHERE best_score < 6
        RETURN c.id AS id, c.name AS company,
               c.industry AS industry, c.stage AS stage,
               best_score
        ORDER BY best_score ASC
    """)


def get_ecosystem_stats():
    """Summary numbers for the admin UI dashboard."""
    return run_query("""
        MATCH (c:Company) WITH count(c) AS total_companies
        MATCH (m:Mentor)  WITH total_companies, count(m) AS total_mentors
        MATCH ()-[r:MATCHED_WITH]->()
        RETURN total_companies, total_mentors,
               count(r) AS total_matches,
               round(avg(r.outcome_score), 2) AS avg_score
    """)


def get_mentors_by_skill(skill):
    """Find mentors who have a specific skill tag."""
    return run_query("""
        MATCH (m:Mentor)
        WHERE $skill IN m.expertise_tags
        RETURN m.id AS id, m.name AS name,
               m.expertise_tags AS skills,
               m.past_success_score AS success_score,
               m.availability AS availability
        ORDER BY m.past_success_score DESC
    """, {"skill": skill})


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH B QUERIES — Blueprint
# ══════════════════════════════════════════════════════════════════════════════

def get_all_flows():
    """Return all flows with their status and performance score."""
    return run_query("""
        MATCH (fl:Flow)
        RETURN fl.id AS id, fl.name AS name,
               fl.status AS status,
               fl.avg_outcome_score AS avg_score,
               fl.description AS description
        ORDER BY fl.avg_outcome_score DESC
    """)


def get_flow_details(flow_id):
    """Return a flow with all its skills, connector and server."""
    return run_query("""
        MATCH (fl:Flow {id: $flow_id})-[:USES]->(sk:Skill)
        MATCH (fl)-[:READS_FROM]->(cn:Connector)
        MATCH (fl)-[:RUNS_ON]->(sv:Server)
        RETURN fl.name AS flow, fl.status AS status,
               fl.avg_outcome_score AS avg_score,
               collect(sk.name) AS skills,
               cn.name AS connector,
               cn.error_rate AS connector_error_rate,
               sv.name AS server,
               sv.current_load AS server_load,
               sv.status AS server_status
    """, {"flow_id": flow_id})


def get_deprecated_flows():
    """Find flows marked as deprecated."""
    return run_query("""
        MATCH (fl:Flow {status: 'deprecated'})-[:USES]->(sk:Skill)
        RETURN fl.id AS flow_id, fl.name AS flow_name,
               fl.avg_outcome_score AS avg_score,
               collect(sk.name) AS skills_used
    """)


def get_proposed_flows():
    """Return flows proposed by the agent waiting for admin approval."""
    return run_query("""
        MATCH (fl:Flow {status: 'proposed'})
        OPTIONAL MATCH (fl)-[:USES]->(sk:Skill)
        OPTIONAL MATCH (fl)-[:READS_FROM]->(cn:Connector)
        OPTIONAL MATCH (fl)-[:RUNS_ON]->(sv:Server)
        RETURN fl.id AS id, fl.name AS name,
               fl.description AS description,
               collect(sk.name) AS skills,
               cn.name AS connector,
               sv.name AS server,
               sv.current_load AS server_load
    """)


def get_high_error_connectors():
    """Find connectors with high error rates."""
    return run_query("""
        MATCH (cn:Connector)
        WHERE cn.error_rate > 0.05
        RETURN cn.id AS id, cn.name AS name,
               cn.error_rate AS error_rate,
               cn.status AS status
        ORDER BY cn.error_rate DESC
    """)


def get_best_skills():
    """Return skills ranked by performance score."""
    return run_query("""
        MATCH (sk:Skill)
        RETURN sk.id AS id, sk.name AS name,
               sk.performance_score AS score,
               sk.avg_execution_ms AS speed_ms,
               sk.language AS language
        ORDER BY sk.performance_score DESC
    """)


def approve_proposed_flow(flow_id):
    """Admin approves a proposed flow — marks it active in Graph B."""
    run_query("""
        MATCH (fl:Flow {id: $flow_id})
        SET fl.status = 'active'
    """, {"flow_id": flow_id})
    return {"status": f"Flow {flow_id} approved and set to active"}


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH B — SERVER / INFRASTRUCTURE QUERIES (Part C)
# ══════════════════════════════════════════════════════════════════════════════

def get_all_servers():
    """Return all servers and their current load status."""
    return run_query("""
        MATCH (sv:Server)
        RETURN sv.id AS id, sv.name AS name,
               sv.current_load AS current_load,
               sv.cpu_capacity AS cpu_capacity,
               sv.status AS status,
               sv.region AS region
        ORDER BY sv.current_load DESC
    """)


def get_overloaded_servers():
    """Find servers that are overloaded or critical — agent uses this."""
    return run_query("""
        MATCH (sv:Server)
        WHERE sv.status IN ['overloaded', 'critical']
        RETURN sv.id AS id, sv.name AS name,
               sv.current_load AS load,
               sv.status AS status,
               sv.error_rate_history AS error_history,
               sv.region AS region
        ORDER BY sv.current_load DESC
    """)


def get_flows_on_bad_servers():
    """Find flows running on overloaded or critical servers."""
    return run_query("""
        MATCH (fl:Flow)-[:RUNS_ON]->(sv:Server)
        WHERE sv.status IN ['overloaded', 'critical']
        RETURN fl.name AS flow, fl.status AS flow_status,
               sv.name AS server, sv.current_load AS load,
               sv.status AS server_status
    """)


def get_infrastructure_status():
    """Full infrastructure overview — used by agent critic and UI monitor."""
    return run_query("""
        MATCH (sv:Server)
        OPTIONAL MATCH (fl:Flow)-[:RUNS_ON]->(sv)
        RETURN sv.name AS server, sv.status AS status,
               sv.current_load AS load,
               sv.region AS region,
               collect(fl.name) AS flows_running
        ORDER BY sv.current_load DESC
    """)


# ══════════════════════════════════════════════════════════════════════════════
# BRIDGE QUERY — Links Graph A and Graph B
# ══════════════════════════════════════════════════════════════════════════════

def log_execution_trace(flow_id, result_score, status="completed", company_id=None, mentor_id=None):
    """
    Creates an ExecutionTrace bridge node linking Graph B (Flow) to Graph A (Outcome).
    Called after every sandbox run.

    The schema matches what the Planner agent queries:
        (et:ExecutionTrace)-[:RAN_FLOW]->(f:Flow)
        (et)-[:RESULTED_IN]->(o:Outcome)
    Company and mentor links are optional extras used by the UI.
    """
    import uuid
    trace_id = f"trace_{uuid.uuid4().hex[:8]}"

    run_query("""
        MATCH (f:Flow {id: $flow_id})
        CREATE (et:ExecutionTrace {
            id:        $trace_id,
            status:    $status,
            timestamp: datetime()
        })
        CREATE (o:Outcome {score: $result_score, date: date()})
        CREATE (et)-[:RAN_FLOW]->(f)
        CREATE (et)-[:RESULTED_IN]->(o)
    """, {
        "flow_id":      flow_id,
        "trace_id":     trace_id,
        "result_score": result_score,
        "status":       status,
    })

    if company_id:
        run_query("""
            MATCH (et:ExecutionTrace {id: $trace_id})
            MATCH (c:Company {id: $company_id})
            CREATE (et)-[:PROCESSED_COMPANY]->(c)
        """, {"trace_id": trace_id, "company_id": company_id})

    if mentor_id:
        run_query("""
            MATCH (et:ExecutionTrace {id: $trace_id})
            MATCH (m:Mentor {id: $mentor_id})
            CREATE (et)-[:PROCESSED_MENTOR]->(m)
        """, {"trace_id": trace_id, "mentor_id": mentor_id})

    return {"status": "trace logged", "trace_id": trace_id}


# ── QUICK TEST ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Ecosystem Stats ===")
    for row in get_ecosystem_stats():
        print(row)

    print("\n=== Failed Matches (score < 5) ===")
    for row in get_failed_matches(threshold=5)[:3]:
        print(row)

    print("\n=== Best Mentors for Fintech ===")
    for row in get_best_mentors_by_industry("Fintech"):
        print(row)

    print("\n=== Success Patterns — Fintech (score >= 7) ===")
    for row in get_success_patterns("Fintech")[:3]:
        print(row)

    print("\n=== Failure Patterns — Healthtech (score <= 4) ===")
    for row in get_failure_patterns("Healthtech")[:3]:
        print(row)

    print("\n=== All Flows (Graph B) ===")
    for row in get_all_flows():
        print(row)

    print("\n=== Infrastructure Status (Part C) ===")
    for row in get_infrastructure_status():
        print(row)

    print("\n=== Overloaded Servers ===")
    for row in get_overloaded_servers():
        print(row)

    print("\n=== Flows on Bad Servers ===")
    for row in get_flows_on_bad_servers():
        print(row)

    print("\nAll queries working!")
