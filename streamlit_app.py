from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv

from src.agents.tools import (
    activate_proposal,
    approve_skill_proposal,
    log_execution_trace,
    query_graph,
    reject_proposal,
    reject_skill_proposal,
    simulate_flow,
    verify_neo4j_connection,
)
from src.indexer.web_indexer import crawl as crawl_website
from src.indexer.codebase_analyzer import CodebaseAnalyzer
from src.indexer.graph_writer import GraphWriter
from src.indexer.project_store import approve_project, mark_project_status
from src.connectors.base import ConnectorInput
from src.connectors.registry import CONNECTOR_REGISTRY, get_connector
from src.graphrag.retriever import retrieve_context as retrieve_graphrag_context
from src.realtime.event_bus import publish_event, read_events
from src.realtime.ui import agent_map_html, live_comms_html


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
REALTIME_API_BASE = os.environ.get("REALTIME_API_BASE", "http://127.0.0.1:8765")
REALTIME_WS_URL = os.environ.get("REALTIME_WS_URL", "ws://127.0.0.1:8765/ws/events")

st.set_page_config(
    page_title="EcoLink NeuroCore",
    page_icon="EC",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    :root {
        --ink: #19211f;
        --muted: #65706d;
        --paper: #f5f2eb;
        --panel: #fffaf0;
        --line: #d8d1c2;
        --accent: #0f7b63;
        --warn: #a55b19;
        --bad: #a73737;
        --good: #167447;
    }
    .stApp {
        background:
            linear-gradient(90deg, rgba(25,33,31,.04) 1px, transparent 1px),
            linear-gradient(180deg, rgba(25,33,31,.035) 1px, transparent 1px),
            var(--paper);
        background-size: 26px 26px;
        color: var(--ink);
    }
    h1, h2, h3 {
        color: var(--ink);
        letter-spacing: 0;
    }
    h1 {
        font-family: Georgia, "Times New Roman", serif;
        font-size: 2.35rem;
        line-height: 1.02;
        margin-bottom: .2rem;
    }
    section[data-testid="stSidebar"] {
        background: #201f1b;
    }
    section[data-testid="stSidebar"] * {
        color: #f7f1e4;
    }
    div[data-testid="stMetric"] {
        background: rgba(255,250,240,.92);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px 14px 10px;
        box-shadow: 0 8px 22px rgba(33,30,22,.06);
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 8px;
        overflow: hidden;
    }
    .status-pill {
        display: inline-block;
        padding: 3px 9px;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: #fffaf0;
        font-size: .78rem;
        margin-right: 6px;
    }
    .status-good { color: var(--good); border-color: rgba(22,116,71,.35); }
    .status-warn { color: var(--warn); border-color: rgba(165,91,25,.35); }
    .status-bad { color: var(--bad); border-color: rgba(167,55,55,.35); }
    .small-muted { color: var(--muted); font-size: .9rem; }
    .block-title {
        font-weight: 700;
        color: var(--ink);
        margin: 16px 0 8px;
    }
    .legend-box {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        padding: 12px 16px;
        background: rgba(255,250,240,0.9);
        border: 1px solid #d8d1c2;
        border-radius: 10px;
        margin-bottom: 10px;
    }
    .legend-item {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.78rem;
        color: #19211f;
        font-weight: 500;
    }
    .legend-dot {
        width: 13px;
        height: 13px;
        border-radius: 50%;
        border: 2px solid;
        flex-shrink: 0;
    }
    .graph-tip {
        background: rgba(15,123,99,0.06);
        border: 1px solid rgba(15,123,99,0.2);
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 0.82rem;
        color: #0f7b63;
        margin-bottom: 10px;
    }
    .agent-log {
        background: #1a1f1d;
        color: #a8f0d0;
        border-radius: 10px;
        padding: 14px 18px;
        font-family: 'Courier New', monospace;
        font-size: 0.8rem;
        line-height: 1.7;
        max-height: 200px;
        overflow-y: auto;
        border: 1px solid #2e2c28;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def run_read(cypher: str) -> list[dict[str, Any]]:
    return query_graph.invoke({"cypher_query": cypher})


def df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=20)
def load_overview() -> dict[str, Any]:
    counts = run_read(
        """
        MATCH (c:Company) WITH count(c) AS companies
        MATCH (m:Mentor) WITH companies, count(m) AS mentors
        MATCH (f:Flow) WITH companies, mentors, count(f) AS flows
        MATCH (s:Server) WITH companies, mentors, flows, count(s) AS servers
        RETURN companies, mentors, flows, servers
        """
    )
    avg = run_read(
        """
        MATCH (:Company)-[r:MATCHED_WITH]->(:Mentor)
        RETURN round(avg(r.outcome_score), 2) AS avg_match_score,
               count(r) AS historical_matches
        """
    )
    proposed = run_read("MATCH (f:Flow {status: 'proposed'}) RETURN count(f) AS proposed")
    traces = run_read("MATCH (et:ExecutionTrace) RETURN count(et) AS traces")
    return {
        **(counts[0] if counts else {}),
        **(avg[0] if avg else {}),
        "proposed": proposed[0]["proposed"] if proposed else 0,
        "traces": traces[0]["traces"] if traces else 0,
    }


@st.cache_data(ttl=20)
def load_flows() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (f:Flow)
            OPTIONAL MATCH (f)-[:USES]->(sk:Skill)
            OPTIONAL MATCH (f)-[:READS_FROM]->(cn:Connector)
            OPTIONAL MATCH (f)-[:RUNS_ON]->(sv:Server)
            RETURN f.id AS id,
                   coalesce(f.name, f.id) AS name,
                   f.status AS status,
                   f.avg_outcome_score AS avg_score,
                   collect(DISTINCT sk.name) AS skills,
                   cn.name AS connector,
                   sv.name AS server,
                   f.yaml_config AS yaml_config
            ORDER BY status, avg_score DESC, id
            """
        )
    )


@st.cache_data(ttl=20)
def load_servers() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (s:Server)
            OPTIONAL MATCH (f:Flow)-[:RUNS_ON]->(s)
            RETURN s.id AS id,
                   s.name AS name,
                   s.status AS status,
                   s.current_load AS load_percent,
                   last(s.error_rate_history) AS error_rate,
                   s.region AS region,
                   collect(f.id) AS flows
            ORDER BY load_percent DESC
            """
        )
    )


@st.cache_data(ttl=20)
def load_traces() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (et:ExecutionTrace)-[:RAN_FLOW]->(f:Flow)
            OPTIONAL MATCH (et)-[:RESULTED_IN]->(o:Outcome)
            RETURN et.id AS trace_id,
                   f.id AS flow_id,
                   et.status AS status,
                   o.score AS score,
                   toString(et.timestamp) AS timestamp
            ORDER BY timestamp DESC
            LIMIT 50
            """
        )
    )


@st.cache_data(ttl=20)
def load_matches() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
            RETURN c.name AS company,
                   c.industry AS industry,
                   m.name AS mentor,
                   r.outcome_score AS score,
                   r.feedback AS feedback,
                   r.programme_name AS programme
            ORDER BY r.outcome_score ASC
            LIMIT 30
            """
        )
    )


@st.cache_data(ttl=20)
def load_websites() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (w:WebSite)
            OPTIONAL MATCH (w)-[:HAS_PAGE]->(p:WebPage)
            OPTIONAL MATCH (w)-[:SITE_HAS_ENTITY]->(e:WebEntity)
            RETURN w.domain AS domain,
                   w.start_url AS start_url,
                   toString(w.indexed_at) AS indexed_at,
                   count(DISTINCT p) AS pages,
                   count(DISTINCT e) AS entities
            ORDER BY indexed_at DESC
            """
        )
    )


@st.cache_data(ttl=20)
def load_web_entities(domain: str | None = None) -> pd.DataFrame:
    if domain:
        query = f"""
        MATCH (w:WebSite {{domain: {json.dumps(domain)}}})-[:SITE_HAS_ENTITY]->(e:WebEntity)
        RETURN e.id AS id,
               e.name AS name,
               e.entity_type AS type,
               e.category AS category,
               e.value AS value,
               e.description AS description,
               e.source AS source
        ORDER BY type, name
        """
    else:
        query = """
        MATCH (e:WebEntity)
        RETURN e.id AS id,
               e.name AS name,
               e.entity_type AS type,
               e.category AS category,
               e.value AS value,
               e.description AS description,
               e.source AS source
        ORDER BY type, name
        LIMIT 200
        """
    return df(run_read(query))


@st.cache_data(ttl=20)
def load_isolation_status() -> dict[str, Any]:
    """Return isolation health metrics: how many nodes have app_id stamped."""
    def _count(q: str) -> int:
        rows = run_read(q)
        return int(rows[0].get("n", 0)) if rows else 0

    page_total   = _count("MATCH (p:WebPage)                                RETURN count(p) AS n")
    page_scoped  = _count("MATCH (p:WebPage)   WHERE p.app_id IS NOT NULL   RETURN count(p) AS n")
    entity_total = _count("MATCH (e:WebEntity)                              RETURN count(e) AS n")
    entity_scoped= _count("MATCH (e:WebEntity) WHERE e.app_id IS NOT NULL   RETURN count(e) AS n")

    fully_isolated = (
        page_total > 0
        and page_total == page_scoped
        and entity_total > 0
        and entity_total == entity_scoped
    )
    return {
        "page_total": page_total,
        "page_scoped": page_scoped,
        "entity_total": entity_total,
        "entity_scoped": entity_scoped,
        "fully_isolated": fully_isolated,
    }


@st.cache_data(ttl=20)
def load_per_app_isolation() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (ap:AppProfile)
            OPTIONAL MATCH (ap)-[:HAS_WEBSITE]->(w:WebSite)-[:HAS_PAGE]->(p:WebPage)
            OPTIONAL MATCH (ap)-[:HAS_WEBSITE]->(w2:WebSite)-[:SITE_HAS_ENTITY]->(e:WebEntity)
            OPTIONAL MATCH (ap)-[:HAS_PIPELINE]->(pl:Pipeline)
            RETURN ap.app_id        AS app_id,
                   ap.source_type   AS source_type,
                   toString(ap.last_indexed_at) AS last_indexed_at,
                   count(DISTINCT p)  AS pages,
                   count(DISTINCT e)  AS entities,
                   count(DISTINCT pl) AS pipelines
            ORDER BY last_indexed_at DESC
            """
        )
    )


@st.cache_data(ttl=20)
def load_pipelines(app_id: str | None = None) -> pd.DataFrame:
    if app_id:
        query = f"""
        MATCH (pl:Pipeline)
        WHERE pl.app_id = {json.dumps(app_id)}
        RETURN pl.id          AS id,
               pl.name        AS name,
               pl.app_id      AS app_id,
               pl.entrypoint  AS entrypoint,
               pl.step_count  AS steps,
               pl.has_contract AS has_contract,
               pl.entity_types AS entity_types,
               toString(pl.discovered_at) AS discovered_at
        ORDER BY pl.step_count DESC, pl.name
        """
    else:
        query = """
        MATCH (pl:Pipeline)
        RETURN pl.id          AS id,
               pl.name        AS name,
               pl.app_id      AS app_id,
               pl.entrypoint  AS entrypoint,
               pl.step_count  AS steps,
               pl.has_contract AS has_contract,
               pl.entity_types AS entity_types,
               toString(pl.discovered_at) AS discovered_at
        ORDER BY pl.app_id, pl.step_count DESC
        """
    return df(run_read(query))


@st.cache_data(ttl=20)
def load_pipeline_steps(pipeline_id: str) -> list[dict[str, Any]]:
    rows = run_read(
        f"MATCH (pl:Pipeline {{id: {json.dumps(pipeline_id)}}}) RETURN pl.steps AS steps"
    )
    if not rows or not rows[0].get("steps"):
        return []
    try:
        return json.loads(rows[0]["steps"])
    except (TypeError, json.JSONDecodeError):
        return []


@st.cache_data(ttl=20)
def load_app_profiles() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (ap:AppProfile)
            OPTIONAL MATCH (ap)-[:HAS_WEBSITE]->(w:WebSite)
            OPTIONAL MATCH (w)-[:HAS_PAGE]->(p:WebPage)
            OPTIONAL MATCH (w)-[:SITE_HAS_ENTITY]->(e:WebEntity)
            RETURN ap.app_id        AS app_id,
                   ap.app_name      AS app_name,
                   ap.source_type   AS source_type,
                   ap.base_url      AS base_url,
                   ap.source_path   AS source_path,
                   toString(ap.last_indexed_at) AS last_indexed_at,
                   count(DISTINCT p) AS pages,
                   count(DISTINCT e) AS entities
            ORDER BY last_indexed_at DESC
            """
        )
    )


@st.cache_data(ttl=20)
def load_projects() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (p)
            WHERE 'Project' IN labels(p)
            OPTIONAL MATCH (p)-[:PROJECT_HAS_REPOSITORY]->(r:Repository)
            OPTIONAL MATCH (r)-[:REPOSITORY_HAS_FILE]->(f:File)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_FUNCTION]->(fn:Function)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_ROUTE]->(rt:Route)
            OPTIONAL MATCH (f)-[:FILE_USES_DATASTORE]->(ds:DataStore)
            OPTIONAL MATCH (f)-[:FILE_USES_INTEGRATION]->(it:Integration)
            OPTIONAL MATCH (f)-[:RISK_FOUND_IN]->(risk:Risk)
            RETURN p.id AS project_id,
                   p.name AS name,
                   p.repo_path AS repo_path,
                   p.permission_status AS permission_status,
                   p.analysis_status AS analysis_status,
                   p.last_scan_id AS last_scan_id,
                   toString(p.created_at) AS created_at,
                   toString(p.updated_at) AS updated_at,
                   count(DISTINCT f) AS files,
                   count(DISTINCT fn) AS functions,
                   count(DISTINCT rt) AS routes,
                   0 AS models,
                   count(DISTINCT ds) AS datastores,
                   count(DISTINCT it) AS integrations,
                   count(DISTINCT risk) AS risks
            ORDER BY updated_at DESC
            """
        )
    )


@st.cache_data(ttl=20)
def load_code_nodes(project_id: str | None = None) -> pd.DataFrame:
    project_filter = (
        f"AND n.project_id = {json.dumps(project_id)}"
        if project_id
        else ""
    )
    return df(
        run_read(
            f"""
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN [
                'Repository', 'File', 'Route', 'Service', 'Function',
                'DatabaseModel', 'DatabaseTable', 'DataStore', 'Entity', 'Workflow',
                'Integration', 'Artifact', 'Risk'
            ])
            {project_filter}
            RETURN labels(n)[0] AS type,
                   n.id AS id,
                   coalesce(n.display_name, n.name) AS display_name,
                   n.name AS name,
                   n.source_path AS source_path,
                   n.confidence AS confidence,
                   n.scan_id AS scan_id,
                   n.project_id AS project_id,
                   n.technical_description AS technical_description,
                   n.stakeholder_description AS stakeholder_description
            ORDER BY type, source_path, name
            LIMIT 500
            """
        )
    )


@st.cache_data(ttl=20)
def load_project_workflow_rows(project_id: str) -> pd.DataFrame:
    return df(
        run_read(
            f"""
            MATCH (p:Project {{id: {json.dumps(project_id)}}})-[:PROJECT_HAS_REPOSITORY]->(:Repository)-[:REPOSITORY_HAS_FILE]->(f:File)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_ROUTE]->(route:Route)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_FUNCTION]->(fn:Function)
            OPTIONAL MATCH (f)-[:FILE_DEFINES_SERVICE]->(svc:Service)
            OPTIONAL MATCH (f)-[:FILE_USES_DATASTORE]->(store:DataStore)
            OPTIONAL MATCH (f)-[:FILE_USES_INTEGRATION]->(integration:Integration)
            OPTIONAL MATCH (f)-[:RISK_FOUND_IN]->(risk:Risk)
            WITH f,
                 collect(DISTINCT route.name) AS routes,
                 collect(DISTINCT fn.name) AS functions,
                 collect(DISTINCT svc.name) AS services,
                 [(f)-->(model) WHERE 'DatabaseModel' IN labels(model) | model.name] AS models,
                 collect(DISTINCT store.name) AS datastores,
                 collect(DISTINCT integration.name) AS integrations,
                 collect(DISTINCT risk.name) AS risks
            WHERE size(routes) > 0 OR size(functions) > 0 OR size(services) > 0
               OR size(models) > 0 OR size(datastores) > 0 OR size(integrations) > 0
               OR size(risks) > 0
            RETURN f.name AS file,
                   routes,
                   functions,
                   services,
                   models,
                   datastores,
                   integrations,
                   risks,
                   CASE
                     WHEN size(routes) > 0 THEN 'Route-driven workflow'
                     WHEN size(datastores) > 0 THEN 'Data-access workflow'
                     WHEN size(functions) > 0 THEN 'Function workflow'
                     ELSE 'Architecture unit'
                   END AS workflow_type
            ORDER BY size(routes) DESC, size(datastores) DESC, file
            """
        )
    )


@st.cache_data(ttl=20)
def load_storage_summary(project_id: str) -> pd.DataFrame:
    return df(
        run_read(
            f"""
            MATCH (f:File)-[:FILE_USES_DATASTORE]->(ds:DataStore)
            WHERE f.project_id = {json.dumps(project_id)}
            RETURN ds.name AS storage,
                   ds.storage_type AS storage_type,
                   count(DISTINCT f) AS files,
                   collect(DISTINCT f.name)[0..8] AS example_files
            ORDER BY files DESC, storage
            """
        )
    )


@st.cache_data(ttl=20)
def load_project_relationship_counts(project_id: str | None = None) -> pd.DataFrame:
    project_filter = (
        f"WHERE coalesce(a.project_id, b.project_id) = {json.dumps(project_id)}"
        if project_id
        else ""
    )
    return df(
        run_read(
            f"""
            MATCH (a)-[r]->(b)
            {project_filter}
            RETURN type(r) AS relationship, count(*) AS count
            ORDER BY count DESC, relationship
            """
        )
    )


def selected_project() -> dict[str, Any] | None:
    projects = load_projects()
    if projects.empty:
        return None
    return projects.iloc[0].to_dict()


def run_codebase_analysis(repo_path: str, project_name: str | None = None, project_id: str | None = None) -> dict[str, Any]:
    analyzer = CodebaseAnalyzer(repo_path, project_name=project_name, project_id=project_id)
    system = analyzer.discover()
    run_id = GraphWriter().write(system)
    return {
        "run_id": run_id,
        "project_id": system.metadata["project_id"],
        "project_name": system.metadata["project_name"],
        "scan_id": system.metadata["scan_id"],
        "file_count": system.metadata["file_count"],
        "code_nodes": len(system.code_nodes),
        "relationships": len(system.code_relationships),
        "skills": len(system.skills),
    }


@st.cache_data(ttl=20)
def load_active_skills() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (s:Skill)
            RETURN s.id AS id,
                   s.name AS name,
                   s.description AS description,
                   s.performance_score AS performance_score,
                   s.language AS language,
                   s.avg_execution_ms AS avg_execution_ms
            ORDER BY coalesce(s.performance_score, 0) DESC
            """
        )
    )


@st.cache_data(ttl=20)
def load_skill_proposals(status: str | None = None) -> pd.DataFrame:
    if status:
        query = f"""
        MATCH (s)
        WHERE 'SkillProposal' IN labels(s)
          AND s.status = {json.dumps(status)}
        RETURN properties(s) AS props
        """
    else:
        query = """
        MATCH (s)
        WHERE 'SkillProposal' IN labels(s)
        RETURN properties(s) AS props
        """
    rows = []
    for row in run_read(query):
        props = row.get("props", {}) or {}
        rows.append(
            {
                "id": props.get("id"),
                "name": props.get("name"),
                "purpose": props.get("purpose"),
                "status": props.get("status"),
                "proposed_by": props.get("proposed_by"),
                "created_at": str(props.get("created_at", "")),
            }
        )
    rows.sort(key=lambda item: item.get("id") or "")
    return df(rows)


@st.cache_data(ttl=20)
def load_label_counts() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN label, count(*) AS count
            ORDER BY count DESC, label
            """
        )
    )


@st.cache_data(ttl=20)
def load_relationship_counts() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH ()-[r]->()
            RETURN type(r) AS relationship, count(*) AS count
            ORDER BY count DESC, relationship
            """
        )
    )


@st.cache_data(ttl=20)
def load_architecture_artifacts() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN [
                'Flow', 'Pipeline', 'SkillProposal', 'ExecutionTrace', 'Outcome',
                'AppProfile', 'WebSite', 'WebPage', 'WebEntity', 'Project',
                'Repository', 'File', 'Route', 'Service', 'Function',
                'DatabaseModel', 'DatabaseTable', 'DataStore', 'Entity', 'Workflow',
                'Integration', 'Artifact', 'Risk'
            ])
            RETURN labels(n)[0] AS type,
                   coalesce(n.id, n.app_id, n.domain, n.url, n.name, elementId(n)) AS id,
                   coalesce(n.name, n.title, n.entrypoint, n.status, '') AS name,
                   n.status AS status,
                   coalesce(n.project_id, n.app_id) AS app_id
            ORDER BY type, id
            LIMIT 300
            """
        )
    )


@st.cache_data(ttl=20)
def load_runtime_primitives() -> pd.DataFrame:
    return df(
        run_read(
            """
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN ['Connector', 'Server', 'Programme'])
            RETURN labels(n)[0] AS type,
                   coalesce(n.id, n.name, elementId(n)) AS id,
                   n.name AS name,
                   n.status AS status,
                   coalesce(n.type, n.region, '') AS detail
            ORDER BY type, id
            """
        )
    )


@st.cache_data(ttl=20)
def load_app_entity_counts(app_id: str) -> list[dict[str, Any]]:
    return run_read(
        f"""
        MATCH (ap:AppProfile {{app_id: {json.dumps(app_id)}}})-[:HAS_WEBSITE]->(w:WebSite)
              -[:SITE_HAS_ENTITY]->(e:WebEntity)
        RETURN e.entity_type AS type, count(e) AS count
        ORDER BY count DESC
        """
    )


@st.cache_data(ttl=20)
def load_website_analysis(domain: str) -> dict[str, Any]:
    domain_json = json.dumps(domain)
    counts = run_read(
        f"""
        MATCH (w:WebSite {{domain: {domain_json}}})-[:SITE_HAS_ENTITY]->(e:WebEntity)
        RETURN e.entity_type AS type, count(e) AS count
        ORDER BY type
        """
    )
    funding = run_read(
        f"""
        MATCH (w:WebSite {{domain: {domain_json}}})-[:SITE_HAS_ENTITY]->(c:WebEntity {{entity_type: 'Campaign'}})
        RETURN count(c) AS campaigns,
               round(sum(coalesce(c.value, 0)), 2) AS total_target
        """
    )
    donations = run_read(
        f"""
        MATCH (w:WebSite {{domain: {domain_json}}})-[:SITE_HAS_ENTITY]->(:WebEntity)<-[r:DONATED_TO]-(d:WebEntity)
        RETURN count(r) AS donation_edges,
               round(sum(coalesce(r.amount, 0)), 2) AS donated_amount,
               count(DISTINCT d) AS donors
        """
    )
    owner_gaps = run_read(
        f"""
        MATCH (w:WebSite {{domain: {domain_json}}})-[:SITE_HAS_ENTITY]->(c:WebEntity {{entity_type: 'Campaign'}})
        WHERE NOT (:WebEntity)-[:OWNS_CAMPAIGN]->(c)
        RETURN collect(c.name) AS campaigns_without_owner
        """
    )
    route_count = run_read(
        f"""
        MATCH (:WebSite {{domain: {domain_json}}})-[:EXPOSES_ROUTE]->(r:WebEntity)
        RETURN count(r) AS routes
        """
    )
    contract_count = run_read(
        f"""
        MATCH (:WebSite {{domain: {domain_json}}})-[:SITE_HAS_ENTITY]->(m:WebEntity {{entity_type: 'ContractMethod'}})
        RETURN count(m) AS contract_methods
        """
    )

    return {
        "counts": counts,
        "funding": funding[0] if funding else {},
        "donations": donations[0] if donations else {},
        "owner_gaps": owner_gaps[0].get("campaigns_without_owner", []) if owner_gaps else [],
        "routes": route_count[0].get("routes", 0) if route_count else 0,
        "contract_methods": contract_count[0].get("contract_methods", 0) if contract_count else 0,
    }


@st.cache_data(ttl=30)
def load_graphrag_context(goal: str, industry: str | None = None) -> dict[str, Any]:
    context = retrieve_graphrag_context(industry=industry, goal=goal)
    return {
        "goal": context.goal,
        "industry": context.industry,
        "baseline_score": context.baseline_score,
        "industry_stats": context.industry_stats,
        "failure_patterns": context.failure_patterns,
        "success_patterns": context.success_patterns,
        "active_flows": context.active_flows,
        "available_skills": context.available_skills,
        "available_connectors": context.available_connectors,
        "infra_status": context.infra_status,
        "learning_events": context.learning_events,
        "website_entities": context.website_entities,
        "software_nodes": context.software_nodes,
    }


@st.cache_data(ttl=20)
def load_graph_payload(limit: int = 180, scope: str = "Dual graph") -> dict[str, list[dict[str, Any]]]:
    scope_labels = {
        "Dual graph": [
            "Company", "Mentor", "Programme", "Flow", "Skill", "Connector", "Server"
        ],
        "Graph A: History": ["Company", "Mentor", "Programme"],
        "Graph B: Code and Infrastructure": ["Flow", "Skill", "Connector", "Server"],
        "Bridge: Execution traces": ["ExecutionTrace", "Outcome", "Flow"],
    }
    labels = scope_labels.get(scope, scope_labels["Dual graph"])
    label_filter = json.dumps(labels)
    rows = run_read(
        f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN {label_filter})
        WITH n LIMIT {limit}
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE any(label IN labels(m) WHERE label IN {label_filter})
        RETURN elementId(n) AS source_id,
               labels(n) AS source_labels,
               coalesce(n.name, n.id, n.path, elementId(n)) AS source_name,
               n.status AS source_status,
               n.avg_outcome_score AS source_score,
               n.industry AS source_industry,
               n.stage AS source_stage,
               n.pain_points AS source_pain,
               n.revenue AS source_revenue,
               n.expertise AS source_expertise,
               n.success_score AS source_success,
               n.available AS source_available,
               n.current_load AS source_load,
               n.region AS source_region,
               n.performance_score AS source_perf,
               n.error_rate AS source_error,
               n.project_id AS source_project_id,
               n.scan_id AS source_scan_id,
               n.source_path AS source_path,
               n.path AS source_file_path,
               n.confidence AS source_confidence,
               n.description AS source_description,
               n.technical_description AS source_technical_description,
               n.business_description AS source_business_description,
               n.method AS source_method,
               n.route AS source_route,
               n.storage_type AS source_storage_type,
               n.primitive_type AS source_primitive_type,
               n.risk_type AS source_risk_type,
               n.severity AS source_severity,
               type(r) AS rel_type,
               elementId(m) AS target_id,
               labels(m) AS target_labels,
               coalesce(m.name, m.id, m.path, elementId(m)) AS target_name,
               m.status AS target_status,
               m.avg_outcome_score AS target_score,
               m.industry AS target_industry,
               m.stage AS target_stage,
               m.pain_points AS target_pain,
               m.revenue AS target_revenue,
               m.expertise AS target_expertise,
               m.success_score AS target_success,
               m.available AS target_available,
               m.current_load AS target_load,
               m.region AS target_region,
               m.performance_score AS target_perf,
               m.error_rate AS target_error,
               m.project_id AS target_project_id,
               m.scan_id AS target_scan_id,
               m.source_path AS target_path,
               m.path AS target_file_path,
               m.confidence AS target_confidence,
               m.description AS target_description,
               m.technical_description AS target_technical_description,
               m.business_description AS target_business_description,
               m.method AS target_method,
               m.route AS target_route,
               m.storage_type AS target_storage_type,
               m.primitive_type AS target_primitive_type,
               m.risk_type AS target_risk_type,
               m.severity AS target_severity
        """
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node_from_row(row: dict[str, Any], prefix: str) -> dict[str, Any]:
        return {
            "id": row[f"{prefix}_id"],
            "label": str(row[f"{prefix}_name"]),
            "group": row[f"{prefix}_labels"][0] if row[f"{prefix}_labels"] else "Node",
            "status": row.get(f"{prefix}_status"),
            "score": row.get(f"{prefix}_score"),
            "industry": row.get(f"{prefix}_industry"),
            "stage": row.get(f"{prefix}_stage"),
            "pain": row.get(f"{prefix}_pain"),
            "revenue": row.get(f"{prefix}_revenue"),
            "expertise": row.get(f"{prefix}_expertise"),
            "success": row.get(f"{prefix}_success"),
            "available": row.get(f"{prefix}_available"),
            "load": row.get(f"{prefix}_load"),
            "region": row.get(f"{prefix}_region"),
            "perf": row.get(f"{prefix}_perf"),
            "error": row.get(f"{prefix}_error"),
            "project_id": row.get(f"{prefix}_project_id"),
            "scan_id": row.get(f"{prefix}_scan_id"),
            "source_path": row.get(f"{prefix}_path") or row.get(f"{prefix}_file_path"),
            "confidence": row.get(f"{prefix}_confidence"),
            "description": row.get(f"{prefix}_description"),
            "technical_description": row.get(f"{prefix}_technical_description"),
            "business_description": row.get(f"{prefix}_business_description"),
            "method": row.get(f"{prefix}_method"),
            "route": row.get(f"{prefix}_route"),
            "storage_type": row.get(f"{prefix}_storage_type"),
            "primitive_type": row.get(f"{prefix}_primitive_type"),
            "risk_type": row.get(f"{prefix}_risk_type"),
            "severity": row.get(f"{prefix}_severity"),
        }

    for row in rows:
        nodes[row["source_id"]] = node_from_row(row, "source")
        if row.get("target_id"):
            nodes[row["target_id"]] = node_from_row(row, "target")
            edges.append(
                {
                    "from": row["source_id"],
                    "to": row["target_id"],
                    "label": row.get("rel_type", ""),
                }
            )

    return {"nodes": list(nodes.values()), "edges": edges}


def clear_data_cache() -> None:
    st.cache_data.clear()


def realtime_status() -> dict[str, Any]:
    try:
        response = requests.get(f"{REALTIME_API_BASE}/health", timeout=0.6)
        response.raise_for_status()
        return {"connected": True, **response.json()}
    except Exception:
        return {"connected": False, "status": "disconnected", "clients": 0}


def run_agent(goal: str) -> tuple[int, str, str, str | None]:
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "main.py", "--goal", goal],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"thread:\s*([a-zA-Z0-9_-]+)", combined)
    return result.returncode, result.stdout, result.stderr, match.group(1) if match else None


def display_table(data: pd.DataFrame, height: int = 280) -> None:
    if data.empty:
        st.info("No records yet.")
        return
    st.dataframe(data, width="stretch", height=height, hide_index=True)


def proposal_payload(raw: Any) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, indent=2)
    except (TypeError, json.JSONDecodeError):
        return str(raw)


def compact_list(values: Any, limit: int = 4) -> str:
    if not values:
        return "None"
    if isinstance(values, str):
        return values
    clean = [str(value) for value in values if value not in (None, "")]
    if not clean:
        return "None"
    shown = clean[:limit]
    suffix = f" +{len(clean) - limit} more" if len(clean) > limit else ""
    return ", ".join(shown) + suffix


def workflow_sentence(row: pd.Series) -> str:
    steps = []
    if row.get("routes"):
        steps.append("Route: " + compact_list(row["routes"], 3))
    if row.get("functions"):
        steps.append("Function: " + compact_list(row["functions"], 3))
    if row.get("services"):
        steps.append("Service: " + compact_list(row["services"], 2))
    if row.get("models"):
        steps.append("Model: " + compact_list(row["models"], 2))
    if row.get("datastores"):
        steps.append("Storage: " + compact_list(row["datastores"], 2))
    if row.get("integrations"):
        steps.append("Integration: " + compact_list(row["integrations"], 2))
    if row.get("risks"):
        steps.append("Review: " + compact_list(row["risks"], 2))
    return " -> ".join(steps) if steps else "No relationships detected yet"


def cloud_run_job_url() -> str | None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    region = os.environ.get("SANDBOX_GCP_REGION") or os.environ.get("GOOGLE_CLOUD_LOCATION")
    job = os.environ.get("SANDBOX_JOB_NAME")
    if not project or not region or not job:
        return None
    return (
        "https://console.cloud.google.com/run/jobs/details/"
        f"{region}/{job}/executions?project={project}"
    )


def graph_legend_html() -> str:
    items = [
        ("Company", "#d7efe5", "#167447"),
        ("Mentor", "#e7e0ff", "#5f4bb6"),
        ("Programme", "#f3e5ab", "#8b6d12"),
        ("Flow", "#fff0c2", "#a55b19"),
        ("Skill", "#dcecff", "#3267a8"),
        ("Connector", "#ffd9cc", "#b54a2c"),
        ("Server", "#e7e3d8", "#6d6252"),
        ("Problem", "#fddede", "#a73737"),
        ("Proposed", "#fff9c2", "#d4a017"),
        ("Agent active", "#d0f4de", "#0f7b63"),
    ]
    dots = "".join(
        f'<div class="legend-item"><div class="legend-dot" '
        f'style="background:{bg};border-color:{border};"></div>{label}</div>'
        for label, bg, border in items
    )
    return f'<div class="legend-box">{dots}</div>'


def graph_html(
    payload: dict[str, list[dict[str, Any]]],
    agent_active_ids: list[str] | None = None,
) -> str:
    """Interactive graph with search, click details, and active-agent highlighting."""

    groups = {
        "Company": {"color": {"background": "#d7efe5", "border": "#167447"}},
        "Mentor": {"color": {"background": "#e7e0ff", "border": "#5f4bb6"}},
        "Flow": {"color": {"background": "#fff0c2", "border": "#a55b19"}},
        "Skill": {"color": {"background": "#dcecff", "border": "#3267a8"}},
        "Connector": {"color": {"background": "#ffd9cc", "border": "#b54a2c"}},
        "Server": {"color": {"background": "#e7e3d8", "border": "#6d6252"}},
        "ExecutionTrace": {"color": {"background": "#cdeff2", "border": "#217b84"}},
        "Outcome": {"color": {"background": "#f0d6d6", "border": "#a73737"}},
        "Programme": {"color": {"background": "#f3e5ab", "border": "#8b6d12"}},
        "WebSite": {"color": {"background": "#d7e8ff", "border": "#3267a8"}},
        "WebPage": {"color": {"background": "#e9f7cf", "border": "#6f9b20"}},
        "WebEntity": {"color": {"background": "#ffd9ed", "border": "#a63171"}},
        "AppProfile": {"color": {"background": "#cfe7df", "border": "#0f7b63"}},
        "Pipeline": {"color": {"background": "#f8dfb2", "border": "#a55b19"}},
        "SkillProposal": {"color": {"background": "#ead8ff", "border": "#6845a4"}},
        "Project": {"color": {"background": "#d8f3dc", "border": "#167447"}},
        "Repository": {"color": {"background": "#cdeff2", "border": "#217b84"}},
        "File": {"color": {"background": "#f1eadb", "border": "#6d6252"}},
        "Route": {"color": {"background": "#d7e8ff", "border": "#3267a8"}},
        "Service": {"color": {"background": "#fff0c2", "border": "#a55b19"}},
        "Function": {"color": {"background": "#dcecff", "border": "#3267a8"}},
        "DatabaseModel": {"color": {"background": "#ead8ff", "border": "#6845a4"}},
        "DatabaseTable": {"color": {"background": "#ead8ff", "border": "#6845a4"}},
        "DataStore": {"color": {"background": "#d7efe5", "border": "#167447"}},
        "Entity": {"color": {"background": "#ffd9ed", "border": "#a63171"}},
        "Workflow": {"color": {"background": "#f8dfb2", "border": "#a55b19"}},
        "Integration": {"color": {"background": "#ffd9cc", "border": "#b54a2c"}},
        "Artifact": {"color": {"background": "#e9f7cf", "border": "#6f9b20"}},
        "Risk": {"color": {"background": "#f0d6d6", "border": "#a73737"}},
    }

    size_map = {
        "Project": 26,
        "Company": 24,
        "Mentor": 24,
        "Repository": 22,
        "Programme": 20,
        "Flow": 20,
        "Workflow": 22,
        "Pipeline": 20,
        "Route": 18,
        "Service": 18,
        "Server": 18,
        "DataStore": 18,
        "DatabaseModel": 17,
        "DatabaseTable": 17,
        "Function": 15,
        "Skill": 15,
        "Connector": 15,
        "Integration": 15,
        "Entity": 15,
        "Artifact": 13,
        "File": 12,
        "ExecutionTrace": 12,
        "Outcome": 12,
        "Risk": 16,
        "SkillProposal": 16,
    }

    badge_colors = {
        "Project": "#167447",
        "Repository": "#217b84",
        "File": "#6d6252",
        "Route": "#3267a8",
        "Service": "#a55b19",
        "Workflow": "#a55b19",
        "Function": "#3267a8",
        "Skill": "#3267a8",
        "DataStore": "#167447",
        "DatabaseModel": "#6845a4",
        "DatabaseTable": "#6845a4",
        "Entity": "#a63171",
        "Integration": "#b54a2c",
        "Artifact": "#6f9b20",
        "Risk": "#a73737",
        "Company": "#167447",
        "Mentor": "#5f4bb6",
        "Flow": "#a55b19",
        "Connector": "#b54a2c",
        "Server": "#6d6252",
        "Programme": "#8b6d12",
        "ExecutionTrace": "#217b84",
        "Outcome": "#a73737",
        "AppProfile": "#0f7b63",
        "Pipeline": "#a55b19",
        "SkillProposal": "#6845a4",
        "WebSite": "#3267a8",
        "WebPage": "#6f9b20",
        "WebEntity": "#a63171",
    }

    active_ids = set(agent_active_ids or [])
    nodes = []
    node_details = {}

    for node in payload["nodes"]:
        status = node.get("status")
        label = node["label"]
        group = node["group"]
        is_active = node["id"] in active_ids

        node_data: dict[str, Any] = {
            "id": node["id"],
            "label": label[:24],
            "group": group,
            "title": f"<b>{group}</b>: {label}",
            "shape": "dot",
            "size": size_map.get(group, 14) * (1.35 if is_active else 1),
        }

        if is_active:
            node_data["color"] = {
                "background": "#d0f4de",
                "border": "#0f7b63",
                "highlight": {"background": "#b7eecb", "border": "#0a5c49"},
            }
            node_data["shadow"] = {
                "enabled": True,
                "color": "rgba(15,123,99,0.4)",
                "size": 16,
                "x": 0,
                "y": 0,
            }
        elif status in ("overloaded", "critical", "deprecated", "analysis_failed"):
            node_data["color"] = {
                "background": "#fddede",
                "border": "#a73737",
                "highlight": {"background": "#fddede", "border": "#7a1c1c"},
            }
        elif status == "proposed":
            node_data["color"] = {
                "background": "#fff9c2",
                "border": "#d4a017",
                "highlight": {"background": "#fff9c2", "border": "#b8860b"},
            }

        nodes.append(node_data)

        details: dict[str, Any] = {"Type": group, "Name": label}
        if status:
            details["Status"] = status
        if is_active:
            details["Agent Status"] = "ACTIVE - being processed"
        if node.get("project_id"):
            details["Project ID"] = node["project_id"]
        if node.get("scan_id"):
            details["Scan ID"] = node["scan_id"]
        if node.get("source_path"):
            details["Source Path"] = node["source_path"]
        if node.get("method"):
            details["Method"] = node["method"]
        if node.get("route"):
            details["Route"] = node["route"]
        if node.get("primitive_type"):
            details["Primitive"] = node["primitive_type"]
        if node.get("storage_type"):
            details["Storage Type"] = node["storage_type"]
        if node.get("risk_type"):
            details["Risk Type"] = node["risk_type"]
        if node.get("severity"):
            details["Severity"] = node["severity"]
        if node.get("confidence") is not None:
            details["Confidence"] = node["confidence"]
        if node.get("description"):
            details["Description"] = node["description"]
        if node.get("technical_description"):
            details["Technical"] = node["technical_description"]
        if node.get("business_description"):
            details["Stakeholder"] = node["business_description"]
        if node.get("industry"):
            details["Industry"] = node["industry"]
        if node.get("stage"):
            details["Stage"] = node["stage"]
        if node.get("pain"):
            details["Pain Points"] = node["pain"]
        if node.get("revenue") is not None:
            details["Revenue"] = f"RM {node['revenue']:,}"
        if node.get("expertise"):
            exp = node["expertise"]
            details["Expertise"] = ", ".join(exp) if isinstance(exp, list) else exp
        if node.get("success") is not None:
            details["Success Score"] = node["success"]
        if node.get("available") is not None:
            details["Available"] = "Yes" if node["available"] else "No"
        if node.get("score") is not None:
            details["Avg Score"] = node["score"]
        if node.get("load") is not None:
            details["CPU Load"] = f"{node['load']}%"
        if node.get("region"):
            details["Region"] = node["region"]
        if node.get("perf") is not None:
            details["Performance"] = node["perf"]
        if node.get("error") is not None:
            details["Error Rate"] = node["error"]

        node_details[node["id"]] = details

    html = f"""
    <div style="display:flex; gap:12px; height:720px;">
      <div style="flex:1; position:relative;">
        <div style="position:absolute; top:10px; left:10px; z-index:10; display:flex; gap:6px;">
          <input id="searchBox" placeholder="Search node..." onkeyup="searchNode()"
            style="padding:6px 12px; border-radius:8px; border:1px solid #d8d1c2;
                   background:#fffaf0; font-size:13px; width:190px; outline:none;
                   box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <button onclick="resetView()"
            style="padding:6px 12px; border-radius:8px; border:1px solid #d8d1c2;
                   background:#fffaf0; cursor:pointer; font-size:12px; color:#65706d;">
            Reset
          </button>
        </div>

        <div id="agentIndicator"
          style="position:absolute; top:10px; right:10px; z-index:10;
                 padding:5px 12px; border-radius:8px; font-size:0.75rem;
                 font-weight:600; display:none;
                 background:#d0f4de; border:1px solid #0f7b63; color:#0a5c49;">
          Agent running...
        </div>

        <div id="network"
          style="height:100%; border:1px solid #d8d1c2; border-radius:12px;
                 background:#fffaf0; box-shadow:0 4px 20px rgba(0,0,0,0.06);">
        </div>
      </div>

      <div id="detailPanel"
        style="width:285px; background:#fffaf0; border:1px solid #d8d1c2;
               border-radius:12px; padding:20px; overflow-y:auto;
               box-shadow:0 4px 20px rgba(0,0,0,0.06);
               display:flex; flex-direction:column; gap:4px; flex-shrink:0;">
        <div style="font-size:0.85rem; color:#65706d; text-align:center; margin-top:40px;">
          Click any node to see its details
        </div>
      </div>
    </div>

    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <script>
      const nodesData  = new vis.DataSet({json.dumps(nodes)});
      const edgesData  = new vis.DataSet({json.dumps(payload["edges"])});
      const groups     = {json.dumps(groups)};
      const details    = {json.dumps(node_details)};
      const badgeColor = {json.dumps(badge_colors)};
      const activeIds  = {json.dumps(list(active_ids))};
      const container  = document.getElementById("network");

      const options = {{
        groups,
        interaction: {{
          hover: true,
          navigationButtons: true,
          keyboard: true,
          tooltipDelay: 80
        }},
        physics: {{
          solver: "forceAtlas2Based",
          forceAtlas2Based: {{
            gravitationalConstant: -60,
            centralGravity: 0.008,
            springLength: 170,
            springConstant: 0.05,
            damping: 0.55
          }},
          stabilization: {{ iterations: 220, updateInterval: 20 }},
          adaptiveTimestep: true
        }},
        nodes: {{
          font: {{
            face: "Inter, sans-serif",
            size: 12,
            color: "#19211f",
            strokeWidth: 3,
            strokeColor: "#fffaf0"
          }},
          borderWidth: 2,
          borderWidthSelected: 3,
          shadow: {{ enabled: true, size: 8, x: 2, y: 3, color: "rgba(0,0,0,0.07)" }}
        }},
        edges: {{
          arrows: {{ to: {{ enabled: true, scaleFactor: 0.45 }} }},
          color: {{ color: "#b5a99a", highlight: "#0f7b63", hover: "#0f7b63" }},
          font: {{ size: 9, align: "middle", color: "#7a6f63", strokeWidth: 2, strokeColor: "#fffaf0" }},
          smooth: {{ type: "cubicBezier", forceDirection: "none", roundness: 0.45 }},
          width: 1.2,
          selectionWidth: 2.5
        }},
        layout: {{ improvedLayout: true }}
      }};

      const network = new vis.Network(container, {{ nodes: nodesData, edges: edgesData }}, options);

      network.once("stabilizationIterationsDone", function() {{
        network.fit({{ animation: {{ duration: 900, easingFunction: "easeInOutQuad" }} }});
        if (activeIds.length > 0) {{
          document.getElementById("agentIndicator").style.display = "block";
          pulseActiveNodes();
        }}
      }});

      let pulseUp = true;
      function pulseActiveNodes() {{
        if (activeIds.length === 0) return;
        setInterval(() => {{
          const updates = activeIds.map(id => {{
            const node = nodesData.get(id);
            if (!node) return null;
            return {{
              id,
              size: pulseUp ? (node.size || 20) * 1.15 : (node.size || 20)
            }};
          }}).filter(Boolean);
          nodesData.update(updates);
          pulseUp = !pulseUp;
        }}, 700);
      }}

      function renderDetails(nodeId) {{
        const panel = document.getElementById("detailPanel");
        const info = details[nodeId];
        if (!info) return;

        const type = info["Type"] || "Node";
        const name = info["Name"] || nodeId;
        const color = badgeColor[type] || "#65706d";
        const isActive = activeIds.includes(nodeId);

        let rows = "";
        for (const [k, v] of Object.entries(info)) {{
          if (k === "Type" || k === "Name") continue;
          const isAgentRow = k === "Agent Status";
          rows += `
            <div style="display:flex;justify-content:space-between;padding:7px 0;
                        border-bottom:1px solid #ede8df;font-size:0.82rem;gap:8px;
                        ${{isAgentRow ? 'background:#f0faf5;margin:0 -4px;padding:7px 4px;border-radius:4px;' : ''}}">
              <span style="color:#65706d;font-weight:500;flex-shrink:0;">${{k}}</span>
              <span style="color:${{isAgentRow ? '#0f7b63' : '#19211f'}};font-weight:600;text-align:right;word-break:break-word;">${{v}}</span>
            </div>`;
        }}

        const activeBanner = isActive ? `
          <div style="background:#d0f4de;border:1px solid #0f7b63;border-radius:6px;
                      padding:6px 10px;font-size:0.75rem;color:#0a5c49;font-weight:600;
                      margin-bottom:10px;">
            Agent is currently processing this node
          </div>` : "";

        panel.innerHTML = `
          ${{activeBanner}}
          <div style="font-size:1rem;font-weight:700;color:#19211f;margin-bottom:4px;word-break:break-word;">${{name}}</div>
          <div style="display:inline-block;padding:3px 10px;border-radius:999px;
                      font-size:0.72rem;font-weight:600;color:white;
                      background:${{color}};margin-bottom:14px;">${{type}}</div>
          ${{rows}}
          <div style="margin-top:10px;font-size:0.75rem;color:#9c927f;text-align:center;">
            Node ID: ...${{String(nodeId).slice(-8)}}
          </div>`;
      }}

      network.on("click", function(params) {{
        if (params.nodes.length === 0) {{
          document.getElementById("detailPanel").innerHTML =
            '<div style="font-size:0.85rem;color:#65706d;text-align:center;margin-top:40px;">Click any node to see its details</div>';
          return;
        }}
        renderDetails(params.nodes[0]);
      }});

      network.on("hoverNode", function() {{ container.style.cursor = "pointer"; }});
      network.on("blurNode", function() {{ container.style.cursor = "default"; }});

      function searchNode() {{
        const q = document.getElementById("searchBox").value.toLowerCase();
        if (!q) {{ resetView(); return; }}
        const allNodes = nodesData.get();
        const match = allNodes.find(n => n.label && n.label.toLowerCase().includes(q));
        if (match) {{
          network.selectNodes([match.id]);
          network.focus(match.id, {{ scale: 1.5, animation: {{ duration: 700 }} }});
          renderDetails(match.id);
        }}
      }}

      function resetView() {{
        document.getElementById("searchBox").value = "";
        network.unselectAll();
        network.fit({{ animation: {{ duration: 600 }} }});
        document.getElementById("detailPanel").innerHTML =
          '<div style="font-size:0.85rem;color:#65706d;text-align:center;margin-top:40px;">Click any node to see its details</div>';
      }}
    </script>
    """
    return html


def default_sandbox_flow() -> str:
    return """flow_id: ui_sandbox_candidate
description: "Manual sandbox verification flow from Streamlit"
runs_on: srv_002
steps:
  - id: semantic_match
    skill: skill_semantic_similarity
    input:
      query: "Healthtech mentor matching"
  - id: calculate_score
    skill: skill_score_calculator
    input:
      weights:
        semantic_similarity: 0.7
        availability: 0.3
"""


def run_sandbox_from_ui(flow_yaml: str, mode: str) -> dict[str, Any]:
    old_mock = os.environ.get("SANDBOX_MOCK")
    old_mode = os.environ.get("SANDBOX_MODE")
    os.environ["SANDBOX_MOCK"] = "false"
    os.environ["SANDBOX_MODE"] = mode
    try:
        result = simulate_flow.invoke(
            {
                "flow_yaml": flow_yaml,
                "dataset_snapshot_id": "ui_sandbox_snapshot",
            }
        )
    finally:
        if old_mock is None:
            os.environ.pop("SANDBOX_MOCK", None)
        else:
            os.environ["SANDBOX_MOCK"] = old_mock
        if old_mode is None:
            os.environ.pop("SANDBOX_MODE", None)
        else:
            os.environ["SANDBOX_MODE"] = old_mode
    return result


with st.sidebar:
    st.markdown("## EcoLink")
    page = st.radio(
        "View",
        [
            "Project Review",
            "Graph Display",
            "Real-Time Agents",
            "Flows",
            "Agentic Architecture",
        ],
        label_visibility="collapsed",
    )
    if st.button("Refresh Data", width="stretch"):
        clear_data_cache()
        st.rerun()


st.title("EcoLink NeuroCore")
sandbox_label = (
    "Mock Sandbox"
    if os.environ.get("SANDBOX_MOCK", "true").lower() == "true"
    else f"{os.environ.get('SANDBOX_MODE', 'local').title()} Sandbox"
)

# GraphRAG status: check how many Skill nodes have an embedding
try:
    _graphrag_rows = run_read(
        "MATCH (s:Skill) WHERE s.embedding IS NOT NULL RETURN count(s) AS n"
    )
    _embedded_count = int(_graphrag_rows[0].get("n", 0)) if _graphrag_rows else 0
    if _embedded_count > 0:
        _graphrag_pill = f"<span class='status-pill status-good'>GraphRAG ({_embedded_count} embedded)</span>"
    else:
        _graphrag_pill = "<span class='status-pill status-warn'>GraphRAG (no embeddings yet)</span>"
except Exception:
    _graphrag_pill = "<span class='status-pill'>GraphRAG</span>"

st.markdown(
    "<span class='status-pill status-good'>Neo4j</span>"
    f"<span class='status-pill status-warn'>{sandbox_label}</span>"
    "<span class='status-pill'>LangGraph Agent</span>"
    + _graphrag_pill,
    unsafe_allow_html=True,
)

neo4j_error = None
try:
    verify_neo4j_connection()
except RuntimeError as exc:
    neo4j_error = str(exc)
    st.warning(
        "Neo4j is currently unavailable. Database-backed pages and live GraphRAG "
        "retrieval will fail until the connection is restored."
    )

database_required_pages = {
    "Project Review",
    "Graph Display",
    "Flows",
    "Agentic Architecture",
}
if neo4j_error and page in database_required_pages:
    st.error(neo4j_error)
    st.stop()


project = None if neo4j_error else selected_project()
project_ready = bool(project and project.get("analysis_status") == "analysis_complete")
if page != "Project Review" and not project_ready:
    st.warning(
        "Connect a project and complete codebase analysis before using graph, flow, "
        "agent, and architecture pages."
    )
    if project:
        st.write(
            {
                "project": project.get("name"),
                "permission_status": project.get("permission_status"),
                "analysis_status": project.get("analysis_status"),
                "repo_path": project.get("repo_path"),
            }
        )
    else:
        st.info("Open Project Review to approve a local codebase analysis.")
    st.stop()


overview = {} if neo4j_error else load_overview()

if page == "Project Review":
    st.subheader("Project Review")
    st.caption("Permission-first connection for the software project this agentic layer analyzes.")

    default_source = str((ROOT.parent / "fundraising_app" / "Crowd-Funding-App").resolve())
    if project:
        status_class = "status-good" if project.get("analysis_status") == "analysis_complete" else "status-warn"
        st.markdown(
            f"<span class='status-pill {status_class}'>{project.get('analysis_status', 'unknown')}</span>"
            f"<span class='status-pill'>Permission: {project.get('permission_status', 'unknown')}</span>",
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Files", int(project.get("files", 0) or 0))
        c2.metric("Functions", int(project.get("functions", 0) or 0))
        c3.metric("Routes", int(project.get("routes", 0) or 0))
        c4.metric("Models", int(project.get("models", 0) or 0))
        c5.metric("Storage", int(project.get("datastores", 0) or 0))
        c6.metric("Risks", int(project.get("risks", 0) or 0))
        st.markdown(f"**Connected project:** `{project.get('name')}`")
        st.markdown(f"**Repository path:** `{project.get('repo_path')}`")
        st.markdown(f"**Last scan:** `{project.get('last_scan_id') or 'not scanned yet'}`")
    else:
        st.info("No project is connected yet. Approve a local repository before the graph and agents become available.")

    st.markdown("### Connect Project")
    st.markdown(
        "This analyzer reads source files, routes, services, models, workflows, "
        "integrations, and manifests. It excludes secret-looking files and common "
        "dependency/build/cache folders such as `.git`, `node_modules`, `.venv`, "
        "`dist`, and `build`."
    )
    project_name = st.text_input(
        "Project name",
        value=str(project.get("name") if project else "Crowd Funding App"),
        key="project_name",
    )
    repo_path = st.text_input(
        "Local codebase path",
        value=str(project.get("repo_path") if project else default_source),
        key="project_repo_path",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Approve Analysis", type="primary", key="approve_project_analysis"):
            approved = approve_project(repo_path, project_name)
            project_id = approved["project_id"]
            publish_event(
                source="ui",
                target="indexer",
                event_type="approved",
                title="Codebase analysis approved",
                detail=approved["repo_path"],
                payload=approved,
            )
            mark_project_status(project_id, "analysis_running")
            publish_event(
                source="indexer",
                event_type="started",
                title="Codebase analysis started after approval",
                detail=repo_path,
                payload={"project_id": project_id, "repo_path": repo_path},
            )
            try:
                with st.spinner("Permission approved. Analyzing codebase and writing software graph..."):
                    result = run_codebase_analysis(repo_path, project_name, project_id)
                mark_project_status(project_id, "analysis_complete", result["scan_id"])
                publish_event(
                    source="indexer",
                    event_type="result",
                    title="Codebase analysis completed",
                    detail=f"{result['code_nodes']} code nodes from {result['file_count']} files",
                    payload=result,
                )
                clear_data_cache()
                st.success("Project approved and analysis complete.")
                st.json(result)
                st.rerun()
            except Exception as exc:
                mark_project_status(project_id, "analysis_failed")
                publish_event(
                    source="indexer",
                    event_type="error",
                    title="Codebase analysis failed after approval",
                    detail=str(exc),
                    payload={"project_id": project_id, "repo_path": repo_path},
                )
                raise
    with c2:
        if st.button("Analyze Codebase", key="run_codebase_analysis"):
            approved = approve_project(repo_path, project_name)
            project_id = approved["project_id"]
            mark_project_status(project_id, "analysis_running")
            publish_event(
                source="indexer",
                event_type="started",
                title="Codebase analysis started",
                detail=repo_path,
                payload={"project_id": project_id, "repo_path": repo_path},
            )
            try:
                with st.spinner("Analyzing codebase and writing software graph..."):
                    result = run_codebase_analysis(repo_path, project_name, project_id)
                mark_project_status(project_id, "analysis_complete", result["scan_id"])
                publish_event(
                    source="indexer",
                    event_type="result",
                    title="Codebase analysis completed",
                    detail=f"{result['code_nodes']} code nodes from {result['file_count']} files",
                    payload=result,
                )
                clear_data_cache()
                st.success("Codebase analysis complete.")
                st.json(result)
                st.rerun()
            except Exception as exc:
                mark_project_status(project_id, "analysis_failed")
                publish_event(
                    source="indexer",
                    event_type="error",
                    title="Codebase analysis failed",
                    detail=str(exc),
                    payload={"project_id": project_id, "repo_path": repo_path},
                )
                raise

    if project_ready and project:
        tab_summary, tab_workflows, tab_inspector, tab_storage, tab_legacy = st.tabs(
            ["Software Summary", "Workflows", "Primitive Inspector", "Storage", "Legacy Data"]
        )
        with tab_summary:
            workflows = load_project_workflow_rows(project["project_id"])
            storage = load_storage_summary(project["project_id"])
            c1, c2 = st.columns([1.25, 1])
            with c1:
                st.markdown("### Workflow Map")
                if workflows.empty:
                    st.info("No route/function/storage relationships detected yet.")
                else:
                    preview = workflows.head(8).copy()
                    preview["pipeline"] = preview.apply(workflow_sentence, axis=1)
                    display_table(preview[["file", "workflow_type", "pipeline"]], height=380)
            with c2:
                st.markdown("### Architecture Signals")
                display_table(load_project_relationship_counts(project["project_id"]), height=220)
                st.markdown("### Storage Signals")
                display_table(storage, height=180)
        with tab_workflows:
            workflows = load_project_workflow_rows(project["project_id"])
            if workflows.empty:
                st.info("No workflows detected yet.")
            else:
                selected_file = st.selectbox("Workflow source", workflows["file"].tolist(), key="workflow_source")
                selected_row = workflows[workflows["file"] == selected_file].iloc[0]
                st.markdown("### Relationship Pipeline")
                st.code(workflow_sentence(selected_row), language="text")
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**Routes**\n\n{compact_list(selected_row['routes'], 10)}")
                c2.markdown(f"**Functions / Services**\n\n{compact_list(selected_row['functions'], 8)}\n\n{compact_list(selected_row['services'], 5)}")
                c3.markdown(f"**Storage / Risk**\n\n{compact_list(selected_row['datastores'], 8)}\n\n{compact_list(selected_row['risks'], 5)}")
                with st.expander("All detected workflow rows"):
                    rows = workflows.copy()
                    rows["pipeline"] = rows.apply(workflow_sentence, axis=1)
                    display_table(rows[["file", "workflow_type", "pipeline"]], height=520)
        with tab_inspector:
            nodes = load_code_nodes(project["project_id"])
            if nodes.empty:
                st.info("No primitives available.")
            else:
                nodes = nodes.copy()
                nodes["label"] = nodes["display_name"].fillna(nodes["type"] + ": " + nodes["name"])
                selected_label = st.selectbox("Primitive", nodes["label"].tolist(), key="primitive_detail")
                primitive = nodes[nodes["label"] == selected_label].iloc[0]
                st.markdown(f"### {primitive['display_name']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Type", primitive["type"])
                c2.metric("Confidence", round(float(primitive.get("confidence") or 0), 2))
                c3.metric("Source", Path(str(primitive["source_path"])).name)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Technical Description", key="tech_primitive_desc"):
                        st.session_state["primitive_desc_mode"] = "technical"
                with c2:
                    if st.button("Stakeholder Description", key="stakeholder_primitive_desc"):
                        st.session_state["primitive_desc_mode"] = "stakeholder"
                mode = st.session_state.get("primitive_desc_mode", "stakeholder")
                if mode == "technical":
                    st.info(primitive.get("technical_description") or "Technical description is not available yet.")
                else:
                    st.success(primitive.get("stakeholder_description") or "Stakeholder description is not available yet.")
                st.markdown(f"**Source path:** `{primitive['source_path']}`")
                st.markdown(f"**Graph ID:** `{primitive['id']}`")
        with tab_storage:
            storage = load_storage_summary(project["project_id"])
            st.markdown("### Detected Data Storage")
            if storage.empty:
                st.info("No storage mechanism detected from code yet. Add a database DSN in the Flows page to inspect a live database read-only.")
            else:
                display_table(storage, height=300)
            st.markdown("### Sandbox Connector Units")
            connector_rows = [
                {
                    "connector_id": connector_id,
                    "name": connector_cls.name,
                    "description": connector_cls.description,
                    "side_effects": "read-only",
                }
                for connector_id, connector_cls in CONNECTOR_REGISTRY.items()
            ]
            display_table(pd.DataFrame(connector_rows), height=160)
            st.code(
                "Codebase -> DataStore detection -> CSV_Connector / SQL_Connector -> sandbox snapshot -> agent recommendation",
                language="text",
            )
        with tab_legacy:
            st.caption("Existing seeded/demo graph data remains visible here for migration context only.")
            cols = st.columns(6)
            cols[0].metric("Companies", overview.get("companies", 0))
            cols[1].metric("Mentors", overview.get("mentors", 0))
            cols[2].metric("Flows", overview.get("flows", 0))
            cols[3].metric("Servers", overview.get("servers", 0))
            cols[4].metric("Avg Score", overview.get("avg_match_score", 0))
            cols[5].metric("Pending", overview.get("proposed", 0))
            display_table(load_label_counts(), height=300)

elif page == "Database Review":
    st.subheader("Current Database Review")
    st.caption("Live Neo4j inventory, connected applications, isolation health, and recent historical evidence.")

    cols = st.columns(6)
    cols[0].metric("Companies", overview.get("companies", 0))
    cols[1].metric("Mentors", overview.get("mentors", 0))
    cols[2].metric("Flows", overview.get("flows", 0))
    cols[3].metric("Servers", overview.get("servers", 0))
    cols[4].metric("Avg Score", overview.get("avg_match_score", 0))
    cols[5].metric("Pending", overview.get("proposed", 0))

    tab_overview, tab_apps, tab_isolation, tab_history = st.tabs(
        ["Overview", "Connected Apps", "Isolation", "History"]
    )
    with tab_overview:
        left, right = st.columns([1.15, 1])
        with left:
            st.markdown("### Node Types")
            display_table(load_label_counts(), height=320)
        with right:
            st.markdown("### Relationship Types")
            display_table(load_relationship_counts(), height=320)
        st.markdown("### Flow Portfolio")
        flows = load_flows()
        if flows.empty:
            st.info("No flows yet.")
        else:
            display_table(flows[["id", "status", "avg_score", "connector", "server", "skills"]], height=300)

    with tab_apps:
        profiles = load_app_profiles()
        if profiles.empty:
            st.info("No connected apps yet. Use the Web & Database Flows page to ingest a website/codebase.")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Connected Apps", len(profiles))
            m2.metric("Total Pages Indexed", int(profiles["pages"].sum()))
            m3.metric("Total Entities Extracted", int(profiles["entities"].sum()))
            display_table(
                profiles[["app_id", "source_type", "base_url", "pages", "entities", "last_indexed_at"]],
                height=260,
            )
            selected_app_id = st.selectbox("Inspect app", profiles["app_id"].tolist(), key="db_app")
            if selected_app_id:
                row = profiles[profiles["app_id"] == selected_app_id].iloc[0]
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**Source type:** {row.get('source_type', '—')}")
                    st.markdown(f"**Base URL:** {row.get('base_url', '—')}")
                    st.markdown(f"**Source path:** {row.get('source_path', '—') or '—'}")
                    st.markdown(f"**Last indexed:** {row.get('last_indexed_at', '—')}")
                with c2:
                    st.markdown("**Entity breakdown**")
                    display_table(df(load_app_entity_counts(selected_app_id)), height=180)

    with tab_isolation:
        iso = load_isolation_status()
        status_label = "ISOLATED" if iso["fully_isolated"] else (
            "PARTIAL" if (iso["page_scoped"] > 0 or iso["entity_scoped"] > 0) else "NOT SCOPED"
        )
        status_class = (
            "status-good" if iso["fully_isolated"]
            else "status-warn" if status_label == "PARTIAL"
            else "status-bad"
        )
        st.markdown(
            f"<h3>Isolation policy: <span class='status-pill {status_class}'>{status_label}</span></h3>",
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pages", iso["page_total"])
        c2.metric("Stamped Pages", iso["page_scoped"])
        c3.metric("Entities", iso["entity_total"])
        c4.metric("Stamped Entities", iso["entity_scoped"])
        st.markdown("### Per-app isolation breakdown")
        display_table(load_per_app_isolation(), height=280)
        st.markdown("### Snapshot Policy")
        st.markdown(
            "- Secret-looking fields are stripped before sandbox execution.\n"
            "- App-scoped snapshots are selected by `app_id` when available.\n"
            "- Local sandbox execution runs out-of-process."
        )

    with tab_history:
        h1, h2 = st.tabs(["Lowest Matches", "Execution Traces"])
        with h1:
            display_table(load_matches(), height=500)
        with h2:
            display_table(load_traces(), height=500)

elif page == "Graph Display":
    st.subheader("Original Website Graph")
    graph_scope = st.radio(
        "Graph scope",
        [
            "Dual graph",
            "Graph A: History",
            "Graph B: Code and Infrastructure",
            "Bridge: Execution traces",
        ],
        horizontal=True,
    )
    limit = st.slider("Node limit", min_value=40, max_value=240, value=180, step=20)
    payload = load_graph_payload(limit, graph_scope)

    c1, c2, c3 = st.columns(3)
    c1.metric("Nodes", len(payload["nodes"]))
    c2.metric("Relationships", len(payload["edges"]))
    c3.metric("Pending Proposals", overview.get("proposed", 0))
    st.markdown(graph_legend_html(), unsafe_allow_html=True)
    st.markdown(
        "<div class='graph-tip'>Click any node to inspect details. "
        "Green glow indicates an agent-active node when a run marks one in session state.</div>",
        unsafe_allow_html=True,
    )
    active_node_ids = st.session_state.get("agent_active_nodes", [])
    components.html(graph_html(payload, agent_active_ids=active_node_ids), height=730)

elif page == "Real-Time Agents":
    st.subheader("Real-Time Agent Structure & Communication")
    st.caption("Static LangGraph topology beside the live event stream used by the dashboard, CLI, sandbox, approval, and indexer.")

    tab_topology, tab_live = st.tabs(["Agent Structure", "Live Communication"])
    with tab_topology:
        components.html(agent_map_html(), height=760, scrolling=False)

    with tab_live:
        status = realtime_status()
        if status["connected"]:
            st.success(f"Realtime server connected. Active WebSocket clients: {status.get('clients', 0)}")
        else:
            st.warning("Realtime server disconnected. Start `uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload` to enable live updates.")

        recent_events = read_events(limit=200)
        c1, c2, c3 = st.columns(3)
        c1.metric("Stored Events", len(recent_events))
        c2.metric("Realtime", "Connected" if status["connected"] else "Offline")
        c3.metric("Event Server", "8765")
        components.html(
            live_comms_html(
                initial_events=recent_events,
                api_base=REALTIME_API_BASE,
                ws_url=REALTIME_WS_URL,
            ),
            height=800,
            scrolling=False,
        )

elif page == "Live Agent Comms":
    st.subheader("Live Agent Communications")
    status = realtime_status()
    if status["connected"]:
        st.success(f"Realtime server connected. Active WebSocket clients: {status.get('clients', 0)}")
    else:
        st.warning("Realtime server disconnected. Start `uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload` to enable live updates.")

    recent_events = read_events(limit=200)
    c1, c2, c3 = st.columns(3)
    c1.metric("Stored Events", len(recent_events))
    c2.metric("Realtime", "Connected" if status["connected"] else "Offline")
    c3.metric("Event Server", "8765")
    components.html(
        live_comms_html(
            initial_events=recent_events,
            api_base=REALTIME_API_BASE,
            ws_url=REALTIME_WS_URL,
        ),
        height=800,
        scrolling=False,
    )

elif page == "Flows":
    st.subheader("Flows")
    st.caption("Detected backend workflows, database/API pipelines, sandbox checks, optional web evidence, and approvals.")

    tab_code, tab_pipelines, tab_db_flows, tab_sandbox, tab_approvals, tab_web = st.tabs(
        ["Codebase Flows", "Software Pipelines", "Database Flows", "Sandbox", "Approvals", "Optional Web Evidence"]
    )

    with tab_code:
        if project:
            st.markdown("### Codebase Workflows")
            workflows = load_project_workflow_rows(project["project_id"])
            if workflows.empty:
                st.info("No workflows found. Re-run analysis from Project Review.")
            else:
                workflow_types = sorted(workflows["workflow_type"].dropna().unique().tolist())
                selected_types = st.multiselect(
                    "Workflow type",
                    workflow_types,
                    default=workflow_types,
                    key="workflow_type_filter",
                )
                filtered = workflows[workflows["workflow_type"].isin(selected_types)] if selected_types else workflows
                filtered = filtered.copy()
                filtered["pipeline"] = filtered.apply(workflow_sentence, axis=1)
                display_table(filtered[["file", "workflow_type", "pipeline"]], height=480)

    with tab_web:
        default_source = str((ROOT.parent / "fundraising_app" / "Crowd-Funding-App").resolve())
        st.caption("Optional supporting evidence only. The primary source of truth is the approved codebase analysis.")
        url = st.text_input("Website URL", value="http://127.0.0.1:5173", key="flow_ingest_url")
        source_path = st.text_input("Local source folder", value=default_source, key="flow_ingest_source")
        c1, c2, c3 = st.columns(3)
        depth = c1.number_input("Crawl depth", min_value=0, max_value=3, value=1, key="flow_ingest_depth")
        max_pages = c2.number_input("Max pages", min_value=1, max_value=100, value=30, key="flow_ingest_pages")
        clear_existing = c3.checkbox("Clear existing domain first", value=True, key="flow_ingest_clear")

        st.markdown("### Indexed Websites")
        display_table(load_websites(), height=180)

        if st.button("Ingest Website & Source", type="primary"):
            publish_event(
                source="indexer",
                event_type="started",
                title="Website ingestion started",
                detail=url,
                payload={"url": url, "source_path": source_path},
            )
            with st.spinner("Crawling website, extracting identities, and materializing pipelines..."):
                result = crawl_website(
                    start_url=url,
                    max_depth=int(depth),
                    max_pages=int(max_pages),
                    clear_existing=clear_existing,
                    source_path=source_path or None,
                )
            publish_event(
                source="indexer",
                event_type="result",
                title="Website ingestion completed",
                detail=f"Indexed {result['domain']}: {result.get('entities_written', 0)} entities",
                payload=result,
            )
            clear_data_cache()
            st.success(f"Indexed {result['domain']}")
            st.json(result)

        websites = load_websites()
        if not websites.empty:
            selected_domain = st.selectbox("Analyze domain", websites["domain"].tolist(), key="flow_domain")
            analysis = load_website_analysis(selected_domain)
            funding = analysis["funding"]
            donations = analysis["donations"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Campaigns", funding.get("campaigns", 0))
            m2.metric("Donors", donations.get("donors", 0))
            m3.metric("Routes", analysis.get("routes", 0))
            m4.metric("Contract Methods", analysis.get("contract_methods", 0))
            display_table(df(analysis["counts"]), height=180)

    with tab_pipelines:
        all_pipelines = load_pipelines()
        if all_pipelines.empty:
            st.info("No pipelines discovered yet. Run an ingest with a source path containing routes and contract/API code.")
        else:
            total = len(all_pipelines)
            with_contract = int((all_pipelines["has_contract"] == True).sum())  # noqa: E712
            apps_covered = all_pipelines["app_id"].nunique()
            m1, m2, m3 = st.columns(3)
            m1.metric("Pipelines Discovered", total)
            m2.metric("With Contract/API Risk", with_contract)
            m3.metric("Apps Covered", apps_covered)

            app_ids = ["All"] + sorted(all_pipelines["app_id"].dropna().unique().tolist())
            selected_app = st.selectbox("Filter by app", app_ids, key="pipeline_app_filter")
            filtered = (
                all_pipelines
                if selected_app == "All"
                else all_pipelines[all_pipelines["app_id"] == selected_app]
            )
            display_df = filtered[["name", "app_id", "entrypoint", "steps", "entity_types", "has_contract"]].copy()
            display_df["risk"] = display_df["has_contract"].map(lambda x: "HIGH" if x else "low")
            display_table(display_df.drop(columns=["has_contract"]), height=280)

            pipeline_options = filtered["id"].tolist()
            if pipeline_options:
                labels = dict(zip(filtered["id"], filtered["name"]))
                selected_pid = st.selectbox(
                    "Pipeline detail",
                    pipeline_options,
                    format_func=lambda x: labels.get(x, x),
                    key="pipeline_detail",
                )
                p_row = filtered[filtered["id"] == selected_pid].iloc[0]
                risk_color = "status-bad" if p_row.get("has_contract") else "status-good"
                risk_label = "HIGH — external/contract execution" if p_row.get("has_contract") else "low"
                st.markdown(
                    f"**Entrypoint:** `{p_row['entrypoint']}` "
                    f"<span class='status-pill {risk_color}'>Risk: {risk_label}</span>",
                    unsafe_allow_html=True,
                )
                display_table(pd.DataFrame(load_pipeline_steps(selected_pid)), height=240)

    with tab_db_flows:
        st.markdown("### Read-only Database Access")
        st.caption("Optional. If a DSN is provided, the agentic layer can inspect database schema and SELECT samples without mutating data.")
        dsn = st.text_input("SQLAlchemy database DSN", value=os.environ.get("INDEX_DB_DSN", ""), type="password", key="db_connector_dsn")
        query = st.text_area("Optional SELECT preview query", value="", height=90, key="db_connector_query")
        if st.button("Inspect Database Read-only", key="inspect_db_readonly"):
            if not dsn:
                st.warning("Provide a database DSN first.")
            else:
                connector = get_connector("SQL_Connector")
                with st.spinner("Inspecting schema through SQL_Connector in read-only mode..."):
                    output = connector.inspect(
                        ConnectorInput(
                            source=dsn,
                            query=query or None,
                            limit=20,
                        )
                    )
                st.success("Database inspected without writes.")
                st.json(output.model_dump())

        st.markdown("### Existing Database Flows")
        flows = load_flows()
        if flows.empty:
            st.info("No database flows found.")
        else:
            statuses = sorted([s for s in flows["status"].dropna().unique()])
            selected_status = st.multiselect("Status", statuses, default=statuses, key="db_flow_status")
            filtered_flows = flows[flows["status"].isin(selected_status)] if selected_status else flows
            display_table(filtered_flows, height=460)

    with tab_sandbox:
        gcp_url = cloud_run_job_url()
        mode = st.segmented_control(
            "Sandbox target",
            options=["local", "cloudrun"],
            default=os.environ.get("SANDBOX_MODE", "local")
            if os.environ.get("SANDBOX_MODE", "local") in {"local", "cloudrun"}
            else "local",
            key="flow_sandbox_mode",
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Configured Mode", os.environ.get("SANDBOX_MODE", "local"))
        c2.metric("GCP Region", os.environ.get("SANDBOX_GCP_REGION", "not set"))
        c3.metric("Cloud Run Job", os.environ.get("SANDBOX_JOB_NAME", "not set"))
        if gcp_url:
            st.link_button("Open GCP Cloud Run Job", gcp_url)
        flow_yaml = st.text_area("Sandbox flow YAML", value=default_sandbox_flow(), height=280, key="flow_sandbox_yaml")
        flow_id = "ui_sandbox_candidate"
        try:
            parsed_flow = yaml.safe_load(flow_yaml) or {}
            if isinstance(parsed_flow, dict):
                flow_id = parsed_flow.get("flow_id", flow_id)
        except yaml.YAMLError:
            pass
        if st.button("Create Sandbox Run", type="primary", key="flow_sandbox_run"):
            publish_event(
                source="sandbox",
                event_type="started",
                title="Sandbox run requested",
                detail=f"Mode: {mode}; Flow: {flow_id}",
                payload={"mode": mode, "flow_id": flow_id},
            )
            result = run_sandbox_from_ui(flow_yaml, mode)
            publish_event(
                source="sandbox",
                target="evaluator",
                event_type="result" if result.get("status") == "success" else "error",
                title="Sandbox run completed",
                detail=result.get("error_log") or f"Metrics: {result.get('metrics', {})}",
                payload={"flow_id": flow_id, "result": result},
            )
            st.session_state["last_sandbox_result"] = result
            if result.get("status") == "success":
                metrics = result.get("metrics", {})
                log_execution_trace(
                    flow_id="flow_smart_match_v1",
                    result_score=metrics.get("match_score", 0.0),
                    status="success",
                )
                clear_data_cache()
                st.success("Sandbox run created successfully.")
            else:
                infra_err = result.get("infra_error")
                if infra_err:
                    err_type = infra_err.get("error_type", "CLOUD_ERROR")
                    service  = infra_err.get("service", "")
                    action   = infra_err.get("human_action", "")
                    fix_url  = infra_err.get("activation_url", "")

                    st.error(f"**Infrastructure error — {err_type}**")
                    st.markdown(
                        f"The sandbox could not run because a GCP infrastructure requirement is not met.\n\n"
                        f"**Affected service:** `{service}`\n\n"
                        f"**Required action:** {action}"
                    )
                    if fix_url:
                        st.link_button("Enable API in GCP Console", fix_url, type="primary")
                    st.info(
                        "**Quick fix:** Switch to local sandbox mode — no GCP required.\n\n"
                        "Set `SANDBOX_MODE=local` in your `.env` file and re-run, "
                        "or use the segmented control above to select **local**."
                    )
                    with st.expander("Raw error detail"):
                        st.code(infra_err.get("raw", ""), language="text")
                else:
                    st.error(result.get("error_log", "Sandbox run failed."))
        if "last_sandbox_result" in st.session_state:
            st.json(st.session_state["last_sandbox_result"])

    with tab_approvals:
        flows = load_flows()
        proposals = flows[flows["status"].fillna("") == "proposed"] if not flows.empty else flows
        if proposals.empty:
            st.info("No pending optimization proposals.")
        for _, row in proposals.iterrows():
            st.markdown(f"### {row['id']}")
            c1, c2, c3 = st.columns([1, 1, 4])
            with c1:
                if st.button("Approve", key=f"flow_approve_{row['id']}", type="primary"):
                    activate_proposal(row["id"])
                    publish_event(
                        source="human_approval",
                        event_type="approved",
                        title="Proposal approved in Streamlit",
                        detail=row["id"],
                        payload={"proposal_id": row["id"]},
                    )
                    clear_data_cache()
                    st.rerun()
            with c2:
                if st.button("Reject", key=f"flow_reject_{row['id']}"):
                    reject_proposal(row["id"], "Rejected in Streamlit dashboard")
                    publish_event(
                        source="human_approval",
                        event_type="rejected",
                        title="Proposal rejected in Streamlit",
                        detail=row["id"],
                        payload={"proposal_id": row["id"]},
                    )
                    clear_data_cache()
                    st.rerun()
            with c3:
                st.write({"name": row.get("name"), "avg_score": row.get("avg_score"), "skills": row.get("skills")})
            payload = proposal_payload(row.get("yaml_config"))
            if payload:
                st.code(payload, language="json")

elif page == "Connected App":
    st.subheader("Connected Application Profiles")
    st.caption(
        "Each indexed website or codebase creates an AppProfile node in Neo4j. "
        "The agent planner uses this context to know which system it is optimizing."
    )

    profiles = load_app_profiles()

    if profiles.empty:
        st.info(
            "No connected apps yet. Use **Website Ingest** to index your first application."
        )
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Connected Apps", len(profiles))
        m2.metric("Total Pages Indexed", int(profiles["pages"].sum()))
        m3.metric("Total Entities Extracted", int(profiles["entities"].sum()))

        st.markdown("### App Profiles")
        display_table(
            profiles[["app_id", "source_type", "base_url", "pages", "entities", "last_indexed_at"]],
            height=220,
        )

        selected_app_id = st.selectbox(
            "Inspect app",
            profiles["app_id"].tolist(),
            format_func=lambda x: x,
        )

        if selected_app_id:
            row = profiles[profiles["app_id"] == selected_app_id].iloc[0]
            st.markdown(f"### {selected_app_id}")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Source type:** {row.get('source_type', '—')}")
                st.markdown(f"**Base URL:** {row.get('base_url', '—')}")
                st.markdown(f"**Source path:** {row.get('source_path', '—') or '—'}")
                st.markdown(f"**Last indexed:** {row.get('last_indexed_at', '—')}")
            with c2:
                entity_counts = load_app_entity_counts(selected_app_id)
                if entity_counts:
                    st.markdown("**Entity breakdown**")
                    display_table(df(entity_counts), height=200)
                else:
                    st.info("No entities extracted for this app yet.")

            st.markdown("### Re-index")
            st.caption("Re-run the crawler for this app with updated settings.")
            ri_url = st.text_input(
                "URL",
                value=str(row.get("base_url", "") or ""),
                key=f"ri_url_{selected_app_id}",
            )
            ri_path = st.text_input(
                "Source path (optional)",
                value=str(row.get("source_path", "") or ""),
                key=f"ri_path_{selected_app_id}",
            )
            ri_c1, ri_c2, ri_c3 = st.columns(3)
            ri_depth = ri_c1.number_input("Depth", min_value=0, max_value=3, value=1, key="ri_depth")
            ri_pages = ri_c2.number_input("Max pages", min_value=1, max_value=100, value=30, key="ri_pages")
            ri_clear = ri_c3.checkbox("Clear existing", value=True, key="ri_clear")

            if st.button("Re-index App", type="primary"):
                publish_event(
                    source="indexer",
                    event_type="started",
                    title="Re-index started from Connected App",
                    detail=ri_url,
                    payload={"app_id": selected_app_id, "url": ri_url},
                )
                with st.spinner(f"Re-indexing {selected_app_id}..."):
                    try:
                        result = crawl_website(
                            start_url=ri_url,
                            max_depth=int(ri_depth),
                            max_pages=int(ri_pages),
                            clear_existing=ri_clear,
                            source_path=ri_path or None,
                        )
                    except Exception as exc:
                        publish_event(
                            source="indexer",
                            event_type="error",
                            title="Re-index failed",
                            detail=str(exc),
                            payload={"app_id": selected_app_id},
                        )
                        st.error(f"Re-index failed: {exc}")
                        st.stop()
                publish_event(
                    source="indexer",
                    event_type="result",
                    title="Re-index completed",
                    detail=f"{result.get('entities_written', 0)} entities",
                    payload=result,
                )
                clear_data_cache()
                st.success(f"Re-indexed {selected_app_id}")
                st.json(result)

elif page == "Pipeline Explorer":
    st.subheader("Pipeline Explorer")
    st.caption(
        "Pipelines are discovered automatically after each website ingest. "
        "Each pipeline is a chain: Route → Feature → ContractMethod."
    )

    all_pipelines = load_pipelines()

    if all_pipelines.empty:
        st.info(
            "No pipelines discovered yet. Run **Website Ingest** with a source path "
            "containing App.tsx (routes) and .clar files (contract methods) to auto-discover pipelines."
        )
    else:
        # Summary metrics
        total = len(all_pipelines)
        with_contract = int((all_pipelines["has_contract"] == True).sum())  # noqa: E712
        apps_covered = all_pipelines["app_id"].nunique()

        m1, m2, m3 = st.columns(3)
        m1.metric("Pipelines Discovered", total)
        m2.metric("With Smart Contract", with_contract)
        m3.metric("Apps Covered", apps_covered)

        # App filter
        app_ids = ["All"] + sorted(all_pipelines["app_id"].dropna().unique().tolist())
        selected_app = st.selectbox("Filter by app", app_ids)
        filtered = (
            all_pipelines
            if selected_app == "All"
            else all_pipelines[all_pipelines["app_id"] == selected_app]
        )

        # Pipeline table — risk badge inline
        def _risk(row: Any) -> str:
            return "HIGH" if row["has_contract"] else "low"

        display_df = filtered[["name", "app_id", "entrypoint", "steps", "entity_types", "has_contract"]].copy()
        display_df["risk"] = display_df["has_contract"].map(lambda x: "HIGH" if x else "low")
        display_table(display_df.drop(columns=["has_contract"]), height=280)

        # Pipeline detail
        st.markdown("### Pipeline Detail")
        pipeline_options = filtered["id"].tolist()
        pipeline_labels = dict(zip(filtered["id"], filtered["name"]))

        if pipeline_options:
            selected_pid = st.selectbox(
                "Select pipeline",
                pipeline_options,
                format_func=lambda x: pipeline_labels.get(x, x),
            )
            p_row = filtered[filtered["id"] == selected_pid].iloc[0]
            steps = load_pipeline_steps(selected_pid)

            risk_color = "status-bad" if p_row.get("has_contract") else "status-good"
            risk_label = "HIGH — involves smart contract" if p_row.get("has_contract") else "low"
            st.markdown(
                f"**Entrypoint:** `{p_row['entrypoint']}`  "
                f"&nbsp;&nbsp;<span class='status-pill {risk_color}'>Risk: {risk_label}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"**Entity types:** {', '.join(p_row['entity_types'] or [])}")
            st.markdown(f"**App:** {p_row['app_id']}")

            if steps:
                st.markdown("**Steps**")
                steps_df = pd.DataFrame(steps)
                display_table(steps_df, height=220)
            else:
                st.info("No step detail available for this pipeline.")

elif page == "Data Isolation":
    st.subheader("Data Isolation")
    st.caption(
        "Isolation status of indexed data. Every node written since Phase 4 carries "
        "an app_id property, ensuring that data from different connected apps cannot "
        "be mixed in sandbox snapshots or agent queries."
    )

    iso = load_isolation_status()
    status_label = "ISOLATED" if iso["fully_isolated"] else (
        "PARTIAL" if (iso["page_scoped"] > 0 or iso["entity_scoped"] > 0) else "NOT SCOPED"
    )
    status_class = (
        "status-good" if iso["fully_isolated"]
        else "status-warn" if status_label == "PARTIAL"
        else "status-bad"
    )

    st.markdown(
        f"<h3>Isolation policy: "
        f"<span class='status-pill {status_class}'>{status_label}</span></h3>",
        unsafe_allow_html=True,
    )

    if not iso["fully_isolated"] and iso["page_total"] > 0:
        unscoped_pages = iso["page_total"] - iso["page_scoped"]
        unscoped_entities = iso["entity_total"] - iso["entity_scoped"]
        if unscoped_pages or unscoped_entities:
            st.warning(
                f"{unscoped_pages} WebPage node(s) and {unscoped_entities} WebEntity node(s) "
                "are missing app_id. Re-index those apps to stamp them."
            )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pages (total)",          iso["page_total"])
    c2.metric("Pages (app_id stamped)", iso["page_scoped"])
    c3.metric("Entities (total)",          iso["entity_total"])
    c4.metric("Entities (app_id stamped)", iso["entity_scoped"])

    st.markdown("### Sandbox snapshot policy")
    st.markdown(
        "- **Secret fields excluded**: always — keys matching `password`, `secret`, "
        "`token`, `credential`, `private`, `api_key` and variants are stripped before "
        "any data is sent to the sandbox.\n"
        "- **Scope**: when an `app_id` is supplied, only matching Company nodes are "
        "included; falls back to the full EcoLink graph if no scoped nodes exist.\n"
        "- **Sandbox isolation**: local mode runs `sandbox_task.py` as a subprocess "
        "with no shared memory; Cloud Run mode uses isolated container execution."
    )

    st.markdown("### Per-app isolation breakdown")
    per_app = load_per_app_isolation()
    if per_app.empty:
        st.info("No AppProfile nodes found. Run Website Ingest to create a profile.")
    else:
        display_table(per_app, height=260)

elif page == "Website Ingest":
    st.subheader("Website Entity Ingestion")
    default_source = str((ROOT.parent / "fundraising_app" / "Crowd-Funding-App").resolve())
    url = st.text_input("Website URL", value="http://127.0.0.1:5173")
    source_path = st.text_input("Optional local source folder", value=default_source)
    c1, c2, c3 = st.columns(3)
    depth = c1.number_input("Crawl depth", min_value=0, max_value=3, value=1)
    max_pages = c2.number_input("Max pages", min_value=1, max_value=100, value=30)
    clear_existing = c3.checkbox("Clear existing domain first", value=True)

    st.markdown("**Existing indexed websites**")
    display_table(load_websites(), height=180)

    if st.button("Ingest Website", type="primary"):
        publish_event(
            source="indexer",
            event_type="started",
            title="Website ingestion started",
            detail=url,
            payload={"url": url, "source_path": source_path},
        )
        with st.spinner("Crawling website and extracting identities..."):
            try:
                result = crawl_website(
                    start_url=url,
                    max_depth=int(depth),
                    max_pages=int(max_pages),
                    clear_existing=clear_existing,
                    source_path=source_path or None,
                )
            except Exception as exc:
                publish_event(
                    source="indexer",
                    event_type="error",
                    title="Website ingestion failed",
                    detail=str(exc),
                    payload={"url": url},
                )
                raise
            publish_event(
                source="indexer",
                event_type="result",
                title="Website ingestion completed",
                detail=f"Indexed {result['domain']}: {result.get('entities_written', 0)} entities",
                payload=result,
            )
        clear_data_cache()
        st.success(f"Indexed {result['domain']}")
        st.json(result)

    websites = load_websites()
    if not websites.empty:
        selected_domain = st.selectbox("Analyze domain", websites["domain"].tolist())
        analysis = load_website_analysis(selected_domain)
        funding = analysis["funding"]
        donations = analysis["donations"]

        st.markdown("### Agentic website analysis")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Campaigns", funding.get("campaigns", 0))
        m2.metric("Donors", donations.get("donors", 0))
        m3.metric("Donation Edges", donations.get("donation_edges", 0))
        m4.metric("Routes", analysis.get("routes", 0))

        recommendations = []
        if funding.get("campaigns", 0):
            recommendations.append(
                f"Track funding progress across {funding.get('campaigns', 0)} campaigns "
                f"with total target {funding.get('total_target', 0)}."
            )
        if analysis.get("contract_methods", 0):
            recommendations.append(
                f"Connect {analysis.get('contract_methods', 0)} detected contract methods to UI actions."
            )
        if analysis["owner_gaps"]:
            recommendations.append(
                "Resolve missing campaign owner links: " + ", ".join(analysis["owner_gaps"])
            )
        else:
            recommendations.append("All detected campaigns have owner relationships.")
        if donations.get("donation_edges", 0):
            recommendations.append(
                f"Use {donations.get('donation_edges', 0)} donor-to-campaign edges for supporter graph analysis."
            )

        for item in recommendations:
            st.write(f"- {item}")

        st.markdown("**Entity counts by type**")
        display_table(df(analysis["counts"]), height=170)
        st.markdown("### Extracted identities/entities")
        display_table(load_web_entities(selected_domain), height=420)

elif page == "Agent Run":
    st.subheader("Run Optimization")
    default_goal = "Improve match quality for Healthtech startups"
    goal = st.text_input("Goal", value=default_goal)
    if st.button("Run Agent", type="primary"):
        publish_event(
            source="ui",
            target="planner",
            event_type="started",
            title="Agent run requested from Streamlit",
            detail=goal,
            payload={"goal": goal},
        )
        with st.spinner("Planner, generator, critic, simulator, evaluator..."):
            try:
                code, stdout, stderr, thread_id = run_agent(goal)
            except subprocess.TimeoutExpired:
                publish_event(
                    source="ui",
                    event_type="error",
                    title="Agent run timed out",
                    detail="Timeout after 240 seconds.",
                    payload={"goal": goal},
                )
                st.error("Agent run timed out after 240 seconds.")
            else:
                clear_data_cache()
                if thread_id:
                    st.session_state["last_thread_id"] = thread_id
                if code == 0:
                    st.success("Agent run completed.")
                else:
                    st.warning("Agent run stopped before a clean exit.")
                publish_event(
                    thread_id=thread_id or "system",
                    source="ui",
                    event_type="result" if code == 0 else "error",
                    title="Streamlit agent run finished",
                    detail=f"Exit code: {code}",
                    payload={"goal": goal, "thread_id": thread_id, "stdout_tail": stdout[-1200:]},
                )
                if thread_id:
                    st.caption(f"Thread ID: {thread_id}")
                st.markdown("**Output**")
                st.code(stdout or "(no stdout)", language="text")
                if stderr:
                    st.markdown("**Diagnostics**")
                    st.code(stderr, language="text")

    st.subheader("Created Proposals")
    proposals = load_flows()
    proposals = proposals[proposals["status"].fillna("") == "proposed"]
    display_table(proposals[["id", "name", "avg_score", "server", "connector"]], height=220)

elif page == "Sandbox":
    st.subheader("Sandbox Control")
    gcp_url = cloud_run_job_url()
    mode = st.segmented_control(
        "Sandbox target",
        options=["local", "cloudrun"],
        default=os.environ.get("SANDBOX_MODE", "local")
        if os.environ.get("SANDBOX_MODE", "local") in {"local", "cloudrun"}
        else "local",
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Configured Mode", os.environ.get("SANDBOX_MODE", "local"))
    c2.metric("GCP Region", os.environ.get("SANDBOX_GCP_REGION", "not set"))
    c3.metric("Cloud Run Job", os.environ.get("SANDBOX_JOB_NAME", "not set"))

    if gcp_url:
        st.link_button("Open GCP Cloud Run Job", gcp_url)
        st.caption(gcp_url)
    else:
        st.warning("Set GOOGLE_CLOUD_PROJECT, SANDBOX_GCP_REGION, and SANDBOX_JOB_NAME to show the GCP container/job link.")

    flow_yaml = st.text_area("Sandbox flow YAML", value=default_sandbox_flow(), height=300)
    flow_id = "ui_sandbox_candidate"
    try:
        parsed_flow = yaml.safe_load(flow_yaml) or {}
        if isinstance(parsed_flow, dict):
            flow_id = parsed_flow.get("flow_id", flow_id)
    except yaml.YAMLError:
        pass

    if st.button("Create Sandbox Run", type="primary"):
        publish_event(
            source="sandbox",
            event_type="started",
            title="Sandbox run requested",
            detail=f"Mode: {mode}; Flow: {flow_id}",
            payload={"mode": mode, "flow_id": flow_id},
        )
        with st.spinner(f"Creating {mode} sandbox run..."):
            result = run_sandbox_from_ui(flow_yaml, mode)
        publish_event(
            source="sandbox",
            target="evaluator",
            event_type="result" if result.get("status") == "success" else "error",
            title="Sandbox run completed",
            detail=result.get("error_log") or f"Metrics: {result.get('metrics', {})}",
            payload={"flow_id": flow_id, "result": result},
        )
        clear_data_cache()
        st.session_state["last_sandbox_result"] = result
        st.session_state["last_sandbox_flow_id"] = flow_id
        if result.get("status") == "success":
            st.success("Sandbox run created successfully.")
            metrics = result.get("metrics", {})
            score = metrics.get("match_score", 0.0)
            log_execution_trace(flow_id="flow_smart_match_v1", result_score=score, status="success")
            st.info("Execution trace logged against flow_smart_match_v1 for dashboard visibility.")
        else:
            infra_err = result.get("infra_error")
            if infra_err:
                err_type = infra_err.get("error_type", "CLOUD_ERROR")
                service  = infra_err.get("service", "")
                action   = infra_err.get("human_action", "")
                fix_url  = infra_err.get("activation_url", "")

                st.error(f"**Infrastructure error — {err_type}**")
                st.markdown(
                    f"The sandbox could not run because a GCP infrastructure requirement is not met.\n\n"
                    f"**Affected service:** `{service}`\n\n"
                    f"**Required action:** {action}"
                )
                if fix_url:
                    st.link_button("Enable API in GCP Console", fix_url, type="primary")
                st.info(
                    "**Quick fix:** Switch to local sandbox mode — no GCP required.\n\n"
                    "Set `SANDBOX_MODE=local` in your `.env` file and re-run, "
                    "or use the segmented control above to select **local**."
                )
                with st.expander("Raw error detail"):
                    st.code(infra_err.get("raw", ""), language="text")
            else:
                st.error(result.get("error_log", "Sandbox run failed."))

    if "last_sandbox_result" in st.session_state:
        result = st.session_state["last_sandbox_result"]
        st.markdown("### Last Sandbox Result")
        st.json(result)
        if gcp_url and mode == "cloudrun":
            st.link_button("Open Created GCP Sandbox Job", gcp_url)

elif page == "Flows":
    st.subheader("Flows")
    flows = load_flows()
    status = st.multiselect(
        "Status",
        sorted([s for s in flows["status"].dropna().unique()]) if not flows.empty else [],
        default=sorted([s for s in flows["status"].dropna().unique()]) if not flows.empty else [],
    )
    if status and not flows.empty:
        flows = flows[flows["status"].isin(status)]
    display_table(flows, height=300)

    st.divider()
    st.subheader("Optimize a Flow")
    st.caption("Select one of the original flows below. The agent will analyse it and propose an improved version without leaving this page.")

    all_flows = load_flows()

    # ── Only show real original flows, not agent-generated proposals ──
    original_flows = all_flows[
        ~all_flows["name"].fillna("").str.startswith("newflow") &
        ~all_flows["status"].fillna("").isin(["proposed", "rejected"])
    ] if not all_flows.empty else all_flows

    if original_flows.empty:
        st.info("No original flows found to optimize.")
    else:
        # ── Flow selection as clean cards ──
        selected_idx = st.session_state.get("selected_flow_idx", 0)
        selected_idx = min(selected_idx, len(original_flows) - 1)

        cols = st.columns(len(original_flows))
        for i, (_, row) in enumerate(original_flows.iterrows()):
            score = row.get("avg_score")
            score_val = float(score) if score and str(score) != "nan" else None
            if score_val is None:
                score_display = "N/A"
                score_color = "#65706d"
            elif score_val < 5:
                score_display = f"{score_val:.1f} — low"
                score_color = "#a73737"
            elif score_val < 7:
                score_display = f"{score_val:.1f} — ok"
                score_color = "#a55b19"
            else:
                score_display = f"{score_val:.1f} — good"
                score_color = "#167447"

            is_sel = selected_idx == i
            border = "2px solid #19211f" if is_sel else "1px solid #d8d1c2"
            bg = "#19211f" if is_sel else "#fffaf0"
            txt = "#f7f1e4" if is_sel else "#19211f"
            sub = "#a8a49e" if is_sel else "#65706d"

            with cols[i]:
                st.markdown(f"""
                <div style="background:{bg};border:{border};border-radius:10px;
                            padding:12px 14px;cursor:pointer;transition:all .2s;
                            margin-bottom:8px;">
                    <div style="font-size:.82rem;font-weight:600;color:{txt};margin-bottom:4px;
                                white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                        {row['name']}
                    </div>
                    <div style="font-size:.72rem;color:{score_color if not is_sel else '#a8a49e'};">
                        Score: {score_display}
                    </div>
                    <div style="font-size:.7rem;color:{sub};margin-top:2px;">
                        {row.get('status','') or '—'}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("Select", key=f"sel_{i}", use_container_width=True):
                    st.session_state["selected_flow_idx"] = i
                    st.session_state["opt_phase"] = "idle"
                    st.rerun()

        # ── Selected flow details — compact row ──
        sel_row     = original_flows.iloc[selected_idx]
        sel_name    = sel_row["name"]
        sel_score   = sel_row.get("avg_score")
        sel_score_f = float(sel_score) if sel_score and str(sel_score) != "nan" else None
        sel_conn    = sel_row.get("connector")
        sel_conn    = sel_conn if sel_conn and str(sel_conn) != "nan" else "—"
        sel_skills  = sel_row.get("skills") or []
        sel_status  = sel_row.get("status") or "—"

        st.markdown(f"""
        <div style="background:#fffaf0;border:1px solid #d8d1c2;border-radius:10px;
                    padding:12px 16px;margin:8px 0 12px;display:flex;gap:24px;
                    flex-wrap:wrap;align-items:center;">
            <div>
                <div style="font-size:.7rem;color:#65706d;font-weight:500;">Selected flow</div>
                <div style="font-size:.88rem;font-weight:600;color:#19211f;">{sel_name}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#65706d;font-weight:500;">Current score</div>
                <div style="font-size:.88rem;font-weight:600;color:#19211f;">{f"{sel_score_f:.1f}" if sel_score_f is not None else "N/A"}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#65706d;font-weight:500;">Connector</div>
                <div style="font-size:.88rem;font-weight:600;color:#19211f;">{sel_conn}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#65706d;font-weight:500;">Status</div>
                <div style="font-size:.88rem;font-weight:600;color:#19211f;">{sel_status}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#65706d;font-weight:500;">Skills</div>
                <div style="font-size:.88rem;font-weight:600;color:#19211f;">{len(sel_skills) if isinstance(sel_skills, list) else 0} skills</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Agent animation ──
        opt_phase = st.session_state.get("opt_phase", "idle")
        opt_slot  = st.empty()

        def opt_anim(phase="idle", flow_name=""):
            phases_map = {
                "idle":       (-1, f"Ready — click Optimize to improve '{flow_name}'"),
                "reading":    (0,  f"Planner reading '{flow_name}' skills and history from Neo4j..."),
                "thinking":   (1,  f"Generator asking Gemini AI how to improve '{flow_name}'..."),
                "proposing":  (2,  f"Critic validating the proposed replacement flow..."),
                "validating": (3,  f"Simulator testing the new flow in sandbox..."),
                "done":       (4,  f"Complete — improved version of '{flow_name}' saved as proposal"),
                "error":      (-2, "No new proposals were created — try again in a moment"),
            }
            active, msg = phases_map.get(phase, phases_map["idle"])
            agents = [
                ("Planner",   "Reads flow + history", "#3267a8", "#dcecff"),
                ("Generator", "Calls Gemini AI",       "#167447", "#d7efe5"),
                ("Critic",    "Validates proposal",    "#a55b19", "#fff0c2"),
                ("Simulator", "Tests in sandbox",      "#5f4bb6", "#e7e0ff"),
            ]
            cards = ""
            for i, (name, role, color, bg) in enumerate(agents):
                is_a = active == i
                is_d = active > i and active >= 0
                op   = "1" if (is_a or is_d) else "0.55"
                bd   = f"2px solid {color}" if is_a else "1px solid #d8d1c2"
                cbg  = bg if is_a else "#fffaf0"
                pulse = "animation:pulse-card 1.4s ease-in-out infinite;" if is_a else ""
                shimmer = '<div style="position:absolute;top:0;left:-100%;width:60%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.55),transparent);animation:shimmer 1.3s infinite;pointer-events:none;"></div>' if is_a else ""
                if is_a:
                    dot = f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{color};animation:blink .9s infinite;margin-right:5px;flex-shrink:0;"></span>'
                elif is_d:
                    dot = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#167447;margin-right:5px;flex-shrink:0;"></span>'
                else:
                    dot = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#d8d1c2;margin-right:5px;flex-shrink:0;"></span>'
                cards += f'<div style="background:{cbg};border:{bd};border-radius:10px;padding:12px 10px;opacity:{op};transition:all .45s;{pulse}position:relative;overflow:hidden;">{shimmer}<div style="display:flex;align-items:center;margin-bottom:5px;">{dot}<span style="font-size:.8rem;font-weight:600;color:{color};">{name}</span></div><div style="font-size:.68rem;color:#65706d;line-height:1.3;">{role}</div></div>'
                if i < 3:
                    ac = color if (is_a or is_d) else "#d8d1c2"
                    cards += f'<div style="display:flex;align-items:center;justify-content:center;color:{ac};font-size:16px;">&rarr;</div>'
            pct = max(0, int(active / 4 * 100)) if active >= 0 else 0
            if phase == "done":   sb,sbd,sc = "#f0faf5","#167447","#167447"
            elif phase == "error":sb,sbd,sc = "#fdf0f0","#a73737","#a73737"
            elif phase == "idle": sb,sbd,sc = "#f5f2eb","#d8d1c2","#65706d"
            else:                 sb,sbd,sc = "#edf5ff","#3267a8","#3267a8"
            return f"""<style>
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
@keyframes pulse-card{{0%,100%{{box-shadow:0 0 0 0 rgba(50,103,168,.18)}}50%{{box-shadow:0 0 0 5px rgba(50,103,168,.06)}}}}
@keyframes shimmer{{to{{left:140%}}}}
</style>
<div style="background:#fffaf0;border:1px solid #d8d1c2;border-radius:12px;padding:16px 16px 14px;margin-bottom:10px;">
<div style="display:grid;grid-template-columns:1fr 24px 1fr 24px 1fr 24px 1fr;align-items:center;gap:3px;margin-bottom:12px;">{cards}</div>
<div style="background:#ede8df;border-radius:999px;height:2px;margin-bottom:9px;overflow:hidden;">
<div style="background:#0f7b63;height:2px;width:{pct}%;border-radius:999px;transition:width .7s ease;"></div></div>
<div style="background:{sb};border:1px solid {sbd};border-radius:7px;padding:8px 12px;font-size:.76rem;color:{sc};font-weight:500;">{msg}</div>
</div>"""

        opt_slot.markdown(opt_anim(opt_phase, sel_name), unsafe_allow_html=True)

        # Result panel after done
        if opt_phase == "done":
            st.markdown(f"""
            <div style="background:#f0faf5;border:1px solid #167447;border-radius:10px;
                        padding:14px 16px;margin-bottom:10px;">
                <div style="font-size:.8rem;font-weight:600;color:#167447;margin-bottom:8px;">What the agent improved</div>
                <div style="display:flex;gap:32px;flex-wrap:wrap;">
                    <div>
                        <div style="font-size:.68rem;color:#65706d;">Before</div>
                        <div style="font-size:.82rem;font-weight:600;color:#19211f;">{sel_name}</div>
                        <div style="font-size:.72rem;color:#a73737;">Score: {f"{sel_score_f:.1f}" if sel_score_f else "N/A"}</div>
                    </div>
                    <div style="font-size:18px;color:#d8d1c2;align-self:center;">&rarr;</div>
                    <div>
                        <div style="font-size:.68rem;color:#65706d;">After</div>
                        <div style="font-size:.82rem;font-weight:600;color:#19211f;">New proposed flow</div>
                        <div style="font-size:.72rem;color:#167447;">Score: estimated higher</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            st.info("Go to Proposals page to Approve or Reject this proposal.")

        if st.button("Optimize this flow", type="primary", use_container_width=True):
            goal = f"Optimize the flow named '{sel_name}'. Current score is {sel_score_f}. Analyse its skills and historical match failures. Propose a better version."
            st.session_state["opt_phase"] = "reading"
            opt_slot.markdown(opt_anim("reading", sel_name), unsafe_allow_html=True)

            env = os.environ.copy()
            proc = subprocess.Popen(
                [sys.executable, "main.py", "--goal", goal],
                cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for raw_line in proc.stdout:
                ll = raw_line.lower()
                if any(x in ll for x in ["query_graph","querying","reading","graph","neo4j"]):
                    if st.session_state.get("opt_phase") != "reading":
                        st.session_state["opt_phase"] = "reading"
                        opt_slot.markdown(opt_anim("reading", sel_name), unsafe_allow_html=True)
                elif any(x in ll for x in ["gemini","llm","generat","propose","200 ok"]):
                    if st.session_state.get("opt_phase") != "thinking":
                        st.session_state["opt_phase"] = "thinking"
                        opt_slot.markdown(opt_anim("thinking", sel_name), unsafe_allow_html=True)
                elif any(x in ll for x in ["critic","validat","check"]):
                    if st.session_state.get("opt_phase") != "proposing":
                        st.session_state["opt_phase"] = "proposing"
                        opt_slot.markdown(opt_anim("proposing", sel_name), unsafe_allow_html=True)
                elif any(x in ll for x in ["simulat","sandbox"]):
                    if st.session_state.get("opt_phase") != "validating":
                        st.session_state["opt_phase"] = "validating"
                        opt_slot.markdown(opt_anim("validating", sel_name), unsafe_allow_html=True)

            proc.wait()
            clear_data_cache()

            updated_flows = load_flows()
            has_proposals = not updated_flows[updated_flows["status"].fillna("") == "proposed"].empty

            if has_proposals:
                st.session_state["opt_phase"] = "done"
                opt_slot.markdown(opt_anim("done", sel_name), unsafe_allow_html=True)
                st.success("Optimization complete — go to Proposals to approve the new flow!")
            else:
                st.session_state["opt_phase"] = "error"
                opt_slot.markdown(opt_anim("error", sel_name), unsafe_allow_html=True)
                st.warning("No new proposals were created. Try again in a moment.")


elif page == "Proposals":
    st.subheader("Pending Optimizations")
    flows = load_flows()
    proposals = flows[flows["status"].fillna("") == "proposed"] if not flows.empty else flows
    if proposals.empty:
        st.info("No pending proposals.")
    for _, row in proposals.iterrows():
        st.markdown(f"### {row['id']}")
        c1, c2, c3 = st.columns([1, 1, 4])
        with c1:
            if st.button("Approve", key=f"approve_{row['id']}", type="primary"):
                activate_proposal(row["id"])
                publish_event(
                    source="human_approval",
                    event_type="approved",
                    title="Proposal approved in Streamlit",
                    detail=row["id"],
                    payload={"proposal_id": row["id"]},
                )
                clear_data_cache()
                st.success(f"Approved {row['id']}")
                st.rerun()
        with c2:
            if st.button("Reject", key=f"reject_{row['id']}"):
                reject_proposal(row["id"], "Rejected in Streamlit dashboard")
                publish_event(
                    source="human_approval",
                    event_type="rejected",
                    title="Proposal rejected in Streamlit",
                    detail=row["id"],
                    payload={"proposal_id": row["id"]},
                )
                clear_data_cache()
                st.warning(f"Rejected {row['id']}")
                st.rerun()
        with c3:
            st.write(
                {
                    "name": row.get("name"),
                    "avg_score": row.get("avg_score"),
                    "server": row.get("server"),
                    "connector": row.get("connector"),
                    "skills": row.get("skills"),
                }
            )
        payload = proposal_payload(row.get("yaml_config"))
        if payload:
            st.code(payload, language="json")

elif page == "Agentic Architecture":
    st.subheader("Full Agentic Architecture")
    st.caption("Inventory of skills, generated artifacts, graph primitives, runtime primitives, and GraphRAG evidence used by the agentic layer.")

    tab_skills, tab_artifacts, tab_primitives, tab_sandbox_arch, tab_graphrag, tab_run = st.tabs(
        ["Skills", "Artifacts", "Primitives", "Sandbox Architecture", "GraphRAG", "Run Agent"]
    )

    with tab_skills:
        skills = load_active_skills()
        proposals_df = load_skill_proposals()
        c1, c2, c3 = st.columns(3)
        c1.metric("Active Skills", len(skills))
        c2.metric("Skill Proposals", len(proposals_df))
        embedded = run_read("MATCH (s:Skill) WHERE s.embedding IS NOT NULL RETURN count(s) AS n")
        c3.metric("Embedded Skills", embedded[0]["n"] if embedded else 0)

        st.markdown("### Active Skills")
        if skills.empty:
            st.info("No Skill nodes in Graph B yet.")
        else:
            display_table(
                skills[["id", "name", "description", "performance_score", "language", "avg_execution_ms"]],
                height=360,
            )

        st.markdown("### Skill Proposals")
        if proposals_df.empty:
            st.info("No SkillProposal nodes yet. Run an agent cycle to surface proposed skills.")
        else:
            for _, row in proposals_df.iterrows():
                with st.expander(f"{row['id']} — {row['status'].upper()}"):
                    st.markdown(f"**Name:** {row['name']}")
                    st.markdown(f"**Purpose:** {row['purpose']}")
                    st.markdown(f"**Proposed by:** {row['proposed_by']} | **Created:** {row['created_at']}")
                    if row["status"] == "proposed":
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Approve", key=f"arch_sp_approve_{row['id']}", type="primary"):
                                approve_skill_proposal(row["id"])
                                clear_data_cache()
                                st.rerun()
                        with c2:
                            if st.button("Reject", key=f"arch_sp_reject_{row['id']}"):
                                reject_skill_proposal(row["id"], "Rejected in Streamlit dashboard")
                                clear_data_cache()
                                st.rerun()

    with tab_artifacts:
        st.markdown("### Agent-Created And Indexed Artifacts")
        st.caption("Flows, proposals, pipelines, execution traces, outcomes, app profiles, web pages, and extracted entities.")
        display_table(load_architecture_artifacts(), height=520)

    with tab_primitives:
        left, right = st.columns(2)
        with left:
            st.markdown("### Node Primitives")
            display_table(load_label_counts(), height=360)
        with right:
            st.markdown("### Relationship Primitives")
            display_table(load_relationship_counts(), height=360)
        st.markdown("### Runtime Primitives")
        display_table(load_runtime_primitives(), height=260)

    with tab_sandbox_arch:
        st.markdown("### Proposed Sandbox Architecture")
        st.caption("This is the new architecture being created in isolation. It does not alter production data.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Connector Units", len(CONNECTOR_REGISTRY))
        c2.metric("Mode", "Read-only")
        c3.metric("Mutation", "Blocked")
        c4.metric("Output", "Recommendation JSON")
        st.code(
            """Existing project code
  -> Project software graph
  -> DataStore detection
  -> CSV_Connector / SQL_Connector (read-only)
  -> Sandbox snapshot
  -> Planner / Critic / Simulator
  -> Proposed restructure actions
  -> Admin review""",
            language="text",
        )
        connector_rows = [
            {
                "connector_id": connector_id,
                "name": connector_cls.name,
                "description": connector_cls.description,
                "input_output": "immutable Pydantic models",
                "side_effects": "none",
            }
            for connector_id, connector_cls in CONNECTOR_REGISTRY.items()
        ]
        display_table(pd.DataFrame(connector_rows), height=180)
        st.markdown("### Recommendation Actions")
        st.json(
            [
                {"action_type": "create_connector", "target": "DataStore", "sandbox_only": True},
                {"action_type": "modify_workflow", "target": "Workflow", "sandbox_only": True},
                {"action_type": "add_validation", "target": "Route or Function", "sandbox_only": True},
                {"action_type": "flag_risk", "target": "Risk", "sandbox_only": True},
            ]
        )

    with tab_graphrag:
        st.markdown("### GraphRAG Evidence")
        goal = st.text_input("Goal", value="Improve match quality for Healthtech startups", key="arch_graphrag_goal")
        industry = st.selectbox(
            "Industry override",
            ["Auto", "Fintech", "Healthtech", "E-commerce", "Logistics", "SaaS", "Edtech"],
            key="arch_graphrag_industry",
        )
        if st.button("Retrieve GraphRAG Context", type="primary", key="arch_graphrag_btn"):
            with st.spinner("Retrieving live graph context from Neo4j..."):
                context = load_graphrag_context(goal, None if industry == "Auto" else industry)
            publish_event(
                source="planner",
                target="generator",
                event_type="message",
                title="GraphRAG context retrieved",
                detail=(
                    f"{context['industry']} context with "
                    f"{len(context['failure_patterns'])} failures and "
                    f"{len(context['success_patterns'])} successes."
                ),
                payload={
                    "industry": context["industry"],
                    "baseline_score": context["baseline_score"],
                    "failure_patterns": len(context["failure_patterns"]),
                    "success_patterns": len(context["success_patterns"]),
                },
            )
            st.session_state["architecture_graphrag_context"] = context

        context = st.session_state.get("architecture_graphrag_context")
        if context:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Industry", context["industry"])
            c2.metric("Baseline", context["baseline_score"])
            c3.metric("Failures", len(context["failure_patterns"]))
            c4.metric("Successes", len(context["success_patterns"]))
            g1, g2, g3, g4, g5, g6 = st.tabs(["Failures", "Successes", "Flows", "Skills", "Software Facts", "Website Entities"])
            with g1:
                display_table(df(context["failure_patterns"]), height=300)
            with g2:
                display_table(df(context["success_patterns"]), height=300)
            with g3:
                display_table(df(context["active_flows"]), height=300)
            with g4:
                display_table(df(context["available_skills"]), height=300)
            with g5:
                display_table(df(context["software_nodes"]), height=300)
            with g6:
                display_table(df(context["website_entities"]), height=300)

    with tab_run:
        st.markdown("### Run Optimization Agent")
        default_goal = "Improve match quality for Healthtech startups"
        goal = st.text_input("Goal", value=default_goal, key="arch_agent_goal")
        if st.button("Run Agent", type="primary", key="arch_run_agent"):
            publish_event(
                source="ui",
                target="planner",
                event_type="started",
                title="Agent run requested from Streamlit",
                detail=goal,
                payload={"goal": goal},
            )
            with st.spinner("Planner, generator, critic, simulator, evaluator..."):
                try:
                    code, stdout, stderr, thread_id = run_agent(goal)
                except subprocess.TimeoutExpired:
                    publish_event(
                        source="ui",
                        event_type="error",
                        title="Agent run timed out",
                        detail="Timeout after 240 seconds.",
                        payload={"goal": goal},
                    )
                    st.error("Agent run timed out after 240 seconds.")
                else:
                    clear_data_cache()
                    if code == 0:
                        st.success("Agent run completed.")
                    else:
                        st.warning("Agent run stopped before a clean exit.")
                    if thread_id:
                        st.caption(f"Thread ID: {thread_id}")
                    st.code(stdout or "(no stdout)", language="text")
                    if stderr:
                        st.code(stderr, language="text")

elif page == "Infrastructure":
    st.subheader("Server Load")
    servers = load_servers()
    display_table(servers, height=280)
    if not servers.empty:
        chart_data = servers[["name", "load_percent"]].set_index("name")
        st.bar_chart(chart_data)

elif page == "History":
    tab1, tab2 = st.tabs(["Matches", "Execution Traces"])
    with tab1:
        display_table(load_matches(), height=520)
    with tab2:
        display_table(load_traces(), height=520)
