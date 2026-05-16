"""
In-memory fallback retriever — same interface as Neo4jRetriever.

Used automatically when the Neo4j connection is unavailable (no .env, network down, etc.).
Data mirrors scripts/seed_graph.py so the fallback output is realistic.
"""
from __future__ import annotations

_COMPANIES = [
    {"id": "C1", "name": "RegTech Solutions", "industry": "Fintech",
     "pain_points": "Needs Regulatory Help", "stage": "Series A"},
    {"id": "C2", "name": "FintechUI Co",       "industry": "Fintech",
     "pain_points": "Needs UI Design",        "stage": "Pre-seed"},
    {"id": "C3", "name": "ClinicalBridge",     "industry": "Healthtech",
     "pain_points": "Needs Clinical Trials",  "stage": "Seed"},
    {"id": "C4", "name": "HealthMktg Inc",     "industry": "Healthtech",
     "pain_points": "Needs Marketing",        "stage": "Pre-seed"},
    {"id": "C5", "name": "CartFlow Ltd",       "industry": "E-commerce",
     "pain_points": "Needs Checkout UX",      "stage": "Series A"},
]

_MENTORS = [
    {"id": "M1", "name": "Alice Chen",   "expertise": "Compliance, Law",
     "availability": "available"},
    {"id": "M2", "name": "Bob Kumar",    "expertise": "Backend, Python",
     "availability": "available"},
    {"id": "M3", "name": "Carol Smith",  "expertise": "Clinical Research, FDA",
     "availability": "available"},
    {"id": "M4", "name": "David Lee",    "expertise": "Backend, DevOps",
     "availability": "busy"},
    {"id": "M5", "name": "Eva Torres",   "expertise": "UX Design, Product",
     "availability": "available"},
]

_MATCHES = [
    ("C1", "M1", 9.0),
    ("C2", "M2", 2.0),
    ("C3", "M3", 8.5),
    ("C4", "M4", 1.5),
    ("C5", "M5", 9.2),
]

_SKILLS = [
    {"name": "skill_semantic_match",     "description": "Compares text fields for semantic similarity",
     "input_schema": '{"source_field":"string","target_field":"string"}', "performance_score": 9.1},
    {"name": "skill_exact_match",        "description": "Compares tags for exact string equality",
     "input_schema": '{"field":"string"}',                                "performance_score": 6.0},
    {"name": "skill_random_sort",        "description": "Shuffles results randomly (anti-pattern)",
     "input_schema": "{}",                                                "performance_score": 2.0},
    {"name": "filter_by_industry_fuzzy", "description": "Filters by industry with fuzzy tolerance",
     "input_schema": '{"industry":"string"}',                             "performance_score": 8.5},
    {"name": "sort_by_score_desc",       "description": "Sorts candidates by score descending",
     "input_schema": "{}",                                                "performance_score": 8.0},
    {"name": "check_availability",       "description": "Filters out unavailable mentors",
     "input_schema": "{}",                                                "performance_score": 7.5},
]

_MOCK_INFRA = {
    "server_1": {"load": 0.45, "error_rate": 0.01, "status": "healthy"},
    "server_2": {"load": 0.62, "error_rate": 0.02, "status": "healthy"},
    "server_3": {"load": 0.88, "error_rate": 0.05, "status": "overloaded"},
}

_COMPANY_MAP = {c["id"]: c for c in _COMPANIES}
_MENTOR_MAP  = {m["id"]: m for m in _MENTORS}


def _build_record(company_id: str, mentor_id: str, score: float) -> dict:
    c = _COMPANY_MAP[company_id]
    m = _MENTOR_MAP[mentor_id]
    return {
        "company_name":        c["name"],
        "company_pain_points": c["pain_points"],
        "company_stage":       c.get("stage", ""),
        "mentor_name":         m["name"],
        "mentor_expertise":    m["expertise"],
        "mentor_availability": m["availability"],
        "score":               score,
        "feedback":            None,
    }


class MockRetriever:
    """Identical interface to Neo4jRetriever — reads from in-memory data."""

    def retrieve_success_patterns(self, industry: str, min_score: float = 7.0) -> list[dict]:
        results = [
            _build_record(cid, mid, score)
            for cid, mid, score in _MATCHES
            if _COMPANY_MAP[cid]["industry"] == industry and score >= min_score
        ]
        return sorted(results, key=lambda r: r["score"], reverse=True)[:10]

    def retrieve_failure_patterns(self, industry: str, max_score: float = 4.0) -> list[dict]:
        results = [
            _build_record(cid, mid, score)
            for cid, mid, score in _MATCHES
            if _COMPANY_MAP[cid]["industry"] == industry and score <= max_score
        ]
        return sorted(results, key=lambda r: r["score"])[:10]

    def get_available_skills(self) -> list[dict]:
        return list(_SKILLS)

    def get_infrastructure_status(self) -> dict:
        return dict(_MOCK_INFRA)

    def close(self) -> None:
        pass
