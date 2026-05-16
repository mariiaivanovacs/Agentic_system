import json
import csv
import random
from faker import Faker

fake = Faker()

INDUSTRIES = ["Fintech", "Healthtech", "Edtech", "Agritech", "Logistics", "E-commerce"]
STAGES = ["Pre-seed", "Seed", "Series A", "Series B"]
EXPERTISE = ["Fundraising", "Product", "Marketing", "Technology", "Operations", "Finance", "Legal", "Scaling"]
PROGRAMMES = ["CIP Spark", "CIP 500", "NEXUS Accelerator", "MyHack Bootcamp"]
PAIN_POINTS = [
    "Need help with investor pitching",
    "Struggling to scale operations",
    "Looking for product-market fit",
    "Need marketing and growth strategy",
    "Require legal and compliance guidance",
    "Need technical architecture advice",
]

# --- Generate Companies ---
companies = []
for i in range(1, 31):
    companies.append({
        "id": f"c{i:03d}",
        "name": fake.company(),
        "industry": random.choice(INDUSTRIES),
        "stage": random.choice(STAGES),
        "pain_points": random.choice(PAIN_POINTS),
        "revenue": random.randint(0, 5000000),
        "founded_year": random.randint(2018, 2024),
    })

with open("data/companies.json", "w") as f:
    json.dump(companies, f, indent=2)
print(f"Generated {len(companies)} companies")

# --- Generate Mentors ---
mentors = []
for i in range(1, 21):
    tags = random.sample(EXPERTISE, k=random.randint(2, 4))
    mentors.append({
        "id": f"m{i:03d}",
        "name": fake.name(),
        "expertise_tags": tags,
        "industry_focus": random.choice(INDUSTRIES),
        "availability": random.choice(["Full-time", "Part-time", "Weekends"]),
        "past_success_score": round(random.uniform(3.0, 5.0), 1),
        "years_experience": random.randint(5, 25),
    })

with open("data/mentors.json", "w") as f:
    json.dump(mentors, f, indent=2)
print(f"Generated {len(mentors)} mentors")

# --- Generate Interactions ---
interactions = []
company_ids = [c["id"] for c in companies]
mentor_ids = [m["id"] for m in mentors]

for i in range(1, 101):
    company_id = random.choice(company_ids)
    mentor_id = random.choice(mentor_ids)
    outcome_score = round(random.uniform(1, 10), 1)
    interactions.append({
        "interaction_id": f"i{i:03d}",
        "company_id": company_id,
        "mentor_id": mentor_id,
        "programme_name": random.choice(PROGRAMMES),
        "outcome_score": outcome_score,
        "feedback": "Good match" if outcome_score >= 6 else "Poor match — skills misaligned",
        "date": fake.date_between(start_date="-3y", end_date="today").strftime("%Y-%m-%d"),
    })

with open("data/interactions.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=interactions[0].keys())
    writer.writeheader()
    writer.writerows(interactions)
print(f"Generated {len(interactions)} interactions")

# ── GRAPH B DATA ──────────────────────────────────────────────────────────────

# --- Connectors ---
connectors = [
    {
        "id": "conn_csv_v1",
        "name": "CSV Connector v1",
        "type": "csv",
        "description": "Reads company and mentor data from CSV files",
        "version": "1.0",
        "status": "active",
        "error_rate": 0.01,
    },
    {
        "id": "conn_sql_v1",
        "name": "SQL Connector v1",
        "type": "sql",
        "description": "Reads from legacy PostgreSQL database",
        "version": "1.0",
        "status": "deprecated",
        "error_rate": 0.25,
    },
    {
        "id": "conn_sql_v2",
        "name": "SQL Connector v2",
        "type": "sql",
        "description": "Improved SQL connector with connection pooling",
        "version": "2.0",
        "status": "active",
        "error_rate": 0.01,
    },
    {
        "id": "conn_api_v1",
        "name": "API Connector v1",
        "type": "api",
        "description": "Reads from external partner APIs",
        "version": "1.0",
        "status": "active",
        "error_rate": 0.02,
    },
]

with open("data/connectors.json", "w") as f:
    json.dump(connectors, f, indent=2)
print(f"Generated {len(connectors)} connectors")

# --- Skills ---
skills = [
    {
        "id": "skill_filter_industry",
        "name": "Filter by Industry",
        "description": "Filters mentors by matching industry to company",
        "language": "python",
        "performance_score": 7.5,
        "avg_execution_ms": 120,
    },
    {
        "id": "skill_random_sort",
        "name": "Random Sort",
        "description": "Randomly sorts mentor list — baseline only",
        "language": "python",
        "performance_score": 3.0,
        "avg_execution_ms": 50,
    },
    {
        "id": "skill_semantic_similarity",
        "name": "Semantic Similarity",
        "description": "Uses embeddings to match mentor skills to company pain points",
        "language": "python",
        "performance_score": 9.2,
        "avg_execution_ms": 850,
    },
    {
        "id": "skill_score_calculator",
        "name": "Score Calculator",
        "description": "Calculates weighted match score from multiple factors",
        "language": "python",
        "performance_score": 8.1,
        "avg_execution_ms": 200,
    },
    {
        "id": "skill_availability_check",
        "name": "Availability Check",
        "description": "Filters out mentors who are unavailable",
        "language": "python",
        "performance_score": 8.8,
        "avg_execution_ms": 80,
    },
    {
        "id": "skill_stage_filter",
        "name": "Stage Filter",
        "description": "Matches mentor experience to company funding stage",
        "language": "python",
        "performance_score": 7.9,
        "avg_execution_ms": 100,
    },
]

with open("data/skills.json", "w") as f:
    json.dump(skills, f, indent=2)
print(f"Generated {len(skills)} skills")

# --- Flows ---
flows = [
    {
        "id": "flow_basic_match",
        "name": "Basic Match Flow",
        "description": "Simple industry filter then random sort — old approach",
        "status": "deprecated",
        "avg_outcome_score": 4.2,
        "skills_used": ["skill_filter_industry", "skill_random_sort"],
        "connector_used": "conn_sql_v1",
        "server_id": "srv_001",
    },
    {
        "id": "flow_smart_match_v1",
        "name": "Smart Match Flow v1",
        "description": "Filters by industry and availability then scores",
        "status": "active",
        "avg_outcome_score": 7.1,
        "skills_used": ["skill_filter_industry", "skill_availability_check", "skill_score_calculator"],
        "connector_used": "conn_sql_v2",
        "server_id": "srv_002",
    },
    {
        "id": "flow_semantic_match_v1",
        "name": "Semantic Match Flow v1",
        "description": "Uses AI embeddings for deep matching — best performing",
        "status": "active",
        "avg_outcome_score": 8.8,
        "skills_used": ["skill_semantic_similarity", "skill_availability_check", "skill_stage_filter", "skill_score_calculator"],
        "connector_used": "conn_api_v1",
        "server_id": "srv_002",
    },
    {
        "id": "flow_proposed_healthtech",
        "name": "Proposed Healthtech Flow",
        "description": "Agent proposed flow optimised for Healthtech startups",
        "status": "proposed",
        "avg_outcome_score": 0.0,
        "skills_used": ["skill_semantic_similarity", "skill_stage_filter", "skill_score_calculator"],
        "connector_used": "conn_api_v1",
        "server_id": "srv_003",
    },
]

with open("data/flows.json", "w") as f:
    json.dump(flows, f, indent=2)
print(f"Generated {len(flows)} flows")

# --- Servers (Part C — Infrastructure) ---
servers = [
    {
        "id": "srv_001",
        "name": "Server Alpha",
        "cpu_capacity": 100,
        "current_load": 85,
        "status": "overloaded",
        "error_rate_history": [0.01, 0.05, 0.12, 0.20, 0.25],
        "region": "KL-East",
    },
    {
        "id": "srv_002",
        "name": "Server Beta",
        "cpu_capacity": 100,
        "current_load": 42,
        "status": "healthy",
        "error_rate_history": [0.01, 0.01, 0.02, 0.01, 0.01],
        "region": "KL-West",
    },
    {
        "id": "srv_003",
        "name": "Server Gamma",
        "cpu_capacity": 100,
        "current_load": 10,
        "status": "healthy",
        "error_rate_history": [0.00, 0.01, 0.00, 0.01, 0.00],
        "region": "KL-Central",
    },
    {
        "id": "srv_004",
        "name": "Server Delta",
        "cpu_capacity": 100,
        "current_load": 95,
        "status": "critical",
        "error_rate_history": [0.10, 0.18, 0.25, 0.30, 0.35],
        "region": "KL-North",
    },
]

with open("data/servers.json", "w") as f:
    json.dump(servers, f, indent=2)
print(f"Generated {len(servers)} servers")

print("\nAll data generated successfully!")
print("Graph A (History):   companies, mentors, interactions, programmes")
print("Graph B (Blueprint): connectors, skills, flows, servers")