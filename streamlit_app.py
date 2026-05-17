from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv

from src.agents.tools import (
    _build_snapshot,
    _run_read_cypher,
    activate_proposal,
    approve_architecture_proposal,
    approve_skill_proposal,
    create_architecture_proposal,
    list_architecture_proposals,
    log_execution_trace,
    query_graph,
    reject_proposal,
    reject_architecture_proposal,
    reject_skill_proposal,
    set_flow_container_url,
    simulate_flow,
    verify_neo4j_connection,
)
from src.agents.cloud_run_urls import (
    cloud_run_execution_url as build_cloud_run_execution_url,
    cloud_run_job_url as build_cloud_run_job_url,
    cloud_run_logs_url as build_cloud_run_logs_url,
)
from src.agents.architecture_sandbox import (
    build_architecture_proposal,
    build_database_only_architecture_proposal,
    discover_database_sources,
    probe_database_source,
    resolve_project_source_path,
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
        --ink: #20181d;
        --muted: #6f626a;
        --paper: #fcfafb;
        --panel: #ffffff;
        --panel-soft: #fbf3f7;
        --line: #eadde4;
        --line-strong: #d9c4cf;
        --accent: #9d174d;
        --accent-soft: #f9dce8;
        --accent-comp: #3f6f5b;
        --accent-comp-soft: #eaf4ef;
        --warn: #9a5b13;
        --bad: #b4234a;
        --good: #1d7555;
        --shadow: 0 16px 36px rgba(71, 31, 51, .07);
    }
    .stApp {
        background: var(--paper);
        color: var(--ink);
        font-family: "Avenir Next", "Helvetica Neue", Helvetica, sans-serif;
    }
    .stApp header[data-testid="stHeader"] {
        background:
            linear-gradient(90deg, rgba(234,244,239,.96), rgba(255,247,250,.96));
        border-bottom: 1px solid var(--line);
        box-shadow: 0 8px 28px rgba(71, 31, 51, .04);
    }
    .stApp [data-testid="stToolbar"] {
        color: var(--muted);
    }
    .stApp [data-testid="stStatusWidget"],
    .stApp [data-testid="stSpinner"] {
        background: rgba(255,255,255,.94) !important;
        color: var(--accent) !important;
        border: 1px solid var(--line) !important;
        border-radius: 8px !important;
        box-shadow: 0 12px 28px rgba(71,31,51,.06) !important;
    }
    .stApp [data-testid="stStatusWidget"] *,
    .stApp [data-testid="stSpinner"] * {
        color: var(--accent) !important;
    }
    .stApp [data-testid="stAppViewContainer"],
    .stApp [data-testid="stMain"],
    .stApp [data-testid="stVerticalBlock"],
    .stApp [data-testid="stMarkdownContainer"],
    .stApp [data-testid="stMarkdownContainer"] p,
    .stApp [data-testid="stMarkdownContainer"] li,
    .stApp [data-testid="stMarkdownContainer"] span,
    .stApp label,
    .stApp p,
    .stApp li {
        color: var(--ink);
    }
    .stApp small,
    .stApp caption,
    .stApp [data-testid="stCaptionContainer"],
    .stApp [data-testid="stMarkdownContainer"] code {
        color: var(--muted);
    }
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
        color: var(--ink);
        letter-spacing: 0;
        font-family: "Avenir Next", "Helvetica Neue", Helvetica, sans-serif;
        font-weight: 720;
    }
    h1 {
        font-size: 2.05rem;
        line-height: 1.08;
        margin-bottom: .35rem;
    }
    section[data-testid="stSidebar"] {
        background:
            linear-gradient(180deg, #ffffff 0%, #fff7fa 54%, #f7fbf8 100%);
        border-right: 1px solid var(--line);
        box-shadow: 10px 0 30px rgba(71, 31, 51, .035);
    }
    section[data-testid="stSidebar"] * {
        color: var(--ink);
    }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span {
        color: var(--ink);
    }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
        color: var(--ink);
        font-size: 1rem;
        font-weight: 760;
        margin-bottom: .7rem;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] {
        gap: .12rem;
    }
    section[data-testid="stSidebar"] [role="radio"] {
        border-radius: 8px;
        padding: 5px 8px;
        transition: background .16s ease, color .16s ease;
    }
    section[data-testid="stSidebar"] [role="radio"]:has(input:checked) {
        background: rgba(157, 23, 77, .08);
        color: var(--accent);
    }
    section[data-testid="stSidebar"] [role="radio"]:hover {
        background: rgba(63, 111, 91, .08);
    }
    section[data-testid="stSidebar"] button {
        border-radius: 8px !important;
        border-color: var(--line-strong) !important;
        background: rgba(255,255,255,.9) !important;
        box-shadow: 0 10px 24px rgba(71,31,51,.045);
    }
    .stApp div[data-testid="stAlert"] *,
    .stApp div[data-testid="stExpander"] *,
    .stApp div[data-baseweb="tab-list"] *,
    .stApp div[data-testid="stRadio"] *,
    .stApp div[data-testid="stSelectbox"] *,
    .stApp div[data-testid="stTextInput"] *,
    .stApp div[data-testid="stTextArea"] * {
        color: var(--ink);
    }
    .stApp div[data-baseweb="select"] > div,
    .stApp div[data-baseweb="select"] input,
    .stApp div[data-baseweb="select"] [role="combobox"] {
        background: var(--panel) !important;
        color: var(--ink) !important;
        border-color: var(--line) !important;
    }
    .stApp div[data-baseweb="select"] svg {
        fill: var(--muted) !important;
    }
    .stApp div[data-testid="stAlert"] {
        background: rgba(255,255,255,.96);
        border-color: var(--line);
    }
    .stApp div[data-testid="stExpander"] {
        background: rgba(255,255,255,.88);
        border-color: var(--line);
    }
    .stApp div[data-testid="stMetric"] label,
    .stApp div[data-testid="stMetric"] [data-testid="stMetricLabel"],
    .stApp div[data-testid="stMetric"] [data-testid="stMetricValue"],
    .stApp div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
        color: var(--ink);
    }
    .stApp div[data-testid="stDataFrame"] {
        color: var(--ink);
    }
    .stApp input,
    .stApp textarea {
        color: var(--ink) !important;
        background: var(--panel) !important;
        border-color: var(--line) !important;
    }
    .stApp input::placeholder,
    .stApp textarea::placeholder {
        color: #a3929c !important;
    }
    .stApp button[kind="secondary"],
    .stApp button[data-testid="baseButton-secondary"] {
        color: var(--ink);
        background: var(--panel);
        border-color: var(--line);
    }
    .stApp button[kind="secondary"]:hover,
    .stApp button[data-testid="baseButton-secondary"]:hover {
        color: var(--accent);
        border-color: var(--accent);
        background: var(--panel-soft);
    }
    .stApp button[kind="primary"],
    .stApp button[data-testid="baseButton-primary"] {
        color: #ffffff;
        background: var(--accent);
        border-color: var(--accent);
    }
    .stApp pre,
    .stApp code {
        color: #3d2732;
        background: #fff6fa;
    }
    .stApp [data-testid="stCodeBlock"],
    .stApp [data-testid="stJson"],
    .stApp [data-testid="stJson"] pre,
    .stApp [data-testid="stCodeBlock"] pre {
        background: #fff7fa !important;
        color: var(--ink) !important;
        border: 1px solid var(--line) !important;
        border-radius: 8px !important;
    }
    .stApp [data-testid="stCodeBlock"] *,
    .stApp [data-testid="stJson"] * {
        background-color: transparent !important;
        color: var(--ink) !important;
    }
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,.96);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px 14px 10px;
        box-shadow: var(--shadow);
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 8px;
        overflow: hidden;
        background: var(--panel-soft);
        box-shadow: 0 12px 30px rgba(71, 31, 51, .045);
    }
    div[data-testid="stDataFrame"] [role="grid"],
    div[data-testid="stDataFrame"] [data-testid="stDataFrameResizable"] {
        background: #fff9fc !important;
    }
    div[data-testid="stDataFrame"] [role="columnheader"] {
        background: #f5eef2 !important;
        color: var(--ink) !important;
        border-color: var(--line) !important;
    }
    div[data-testid="stDataFrame"] [role="gridcell"] {
        background: #fff9fc !important;
        color: var(--ink) !important;
        border-color: #f0e5eb !important;
    }
    div[data-testid="stDataFrame"] [role="row"]:nth-child(even) [role="gridcell"] {
        background: #fcf5f8 !important;
    }
    div[data-testid="stDataFrame"] canvas,
    div[data-testid="stDataFrame"] svg {
        background: #fff9fc !important;
    }
    .status-pill {
        display: inline-block;
        padding: 3px 9px;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: var(--panel);
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
        background: rgba(255,255,255,0.92);
        border: 1px solid var(--line);
        border-radius: 8px;
        margin-bottom: 10px;
        box-shadow: 0 10px 28px rgba(71,31,51,.04);
    }
    .legend-item {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.78rem;
        color: var(--ink);
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
        background: rgba(157,23,77,0.07);
        border: 1px solid rgba(157,23,77,0.2);
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 0.82rem;
        color: var(--accent);
        margin-bottom: 10px;
    }
    .agent-log {
        background: #fff7fa;
        color: #3d2732;
        border-radius: 8px;
        padding: 14px 18px;
        font-family: "SFMono-Regular", Consolas, monospace;
        font-size: 0.8rem;
        line-height: 1.7;
        max-height: 200px;
        overflow-y: auto;
        border: 1px solid var(--line);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def run_read(cypher: str) -> list[dict[str, Any]]:
    try:
        return query_graph.invoke({"cypher_query": cypher})
    except Exception as exc:
        st.session_state["neo4j_last_read_error"] = str(exc)
        return []


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
                   f.project_id AS project_id,
                   f.business_flow_id AS business_flow_id,
                   f.justification AS justification,
                   f.yaml_config AS yaml_config,
                   f.container_url AS container_url
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
            RETURN et.id                          AS trace_id,
                   f.id                           AS flow_id,
                   f.name                         AS flow_name,
                   et.status                      AS status,
                   o.score                        AS score,
                   et.baseline_score              AS baseline_score,
                   et.skills_applied              AS skills_applied,
                   toString(et.timestamp)         AS timestamp
            ORDER BY timestamp DESC
            LIMIT 100
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
            OPTIONAL MATCH (p)-[:HAS_BUSINESS_FLOW]->(bf:BusinessFlow)
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
                   count(DISTINCT risk) AS risks,
                   count(DISTINCT bf) AS business_flows
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
                'BusinessFlow', 'FlowStep', 'Integration', 'Artifact', 'Risk'
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
def load_primitive_relationship_rows(project_id: str, node_id: str) -> pd.DataFrame:
    return df(
        run_read(
            f"""
            MATCH (n {{id: {json.dumps(node_id)}}})
            OPTIONAL MATCH (src)-[in_rel]->(n)
            WHERE src.project_id = {json.dumps(project_id)} OR src.id = {json.dumps(project_id)}
            WITH n, collect({{
                direction: 'incoming',
                relationship: type(in_rel),
                neighbor_type: labels(src)[0],
                neighbor: coalesce(src.display_name, src.name, src.id)
            }}) AS incoming
            OPTIONAL MATCH (n)-[out_rel]->(dst)
            WHERE dst.project_id = {json.dumps(project_id)} OR dst.id = {json.dumps(project_id)}
            WITH incoming + collect({{
                direction: 'outgoing',
                relationship: type(out_rel),
                neighbor_type: labels(dst)[0],
                neighbor: coalesce(dst.display_name, dst.name, dst.id)
            }}) AS rows
            UNWIND rows AS row
            WITH row
            WHERE row.relationship IS NOT NULL
            RETURN row.direction AS direction,
                   row.relationship AS relationship,
                   row.neighbor_type AS neighbor_type,
                   row.neighbor AS neighbor
            ORDER BY direction, relationship, neighbor
            LIMIT 80
            """
        )
    )


@st.cache_data(ttl=20)
def load_business_flow_rows(project_id: str) -> pd.DataFrame:
    return df(
        run_read(
            f"""
            MATCH (:Project {{id: {json.dumps(project_id)}}})-[:HAS_BUSINESS_FLOW]->(bf:BusinessFlow)
            OPTIONAL MATCH (bf)-[hs:HAS_STEP]->(step:FlowStep)
            OPTIONAL MATCH (step)-[:USES_PRIMITIVE]->(primitive)
            WITH bf, hs, step, primitive
            ORDER BY coalesce(hs.order, step.order), step.name
            WITH bf,
                 collect({{
                    order: coalesce(hs.order, step.order),
                    step: step.name,
                    step_type: step.step_type,
                    primitive: primitive.name,
                    primitive_type: labels(primitive)[0],
                    primitive_id: primitive.id,
                    evidence: step.evidence
                 }}) AS steps
            RETURN bf.id AS id,
                   bf.name AS business_flow,
                   bf.entrypoint AS entrypoint,
                   bf.flow_type AS flow_type,
                   bf.confidence AS confidence,
                   bf.evidence_summary AS evidence_summary,
                   bf.source_paths AS source_paths,
                   steps,
                   [s IN steps WHERE s.primitive_type IN ['DataStore', 'DatabaseModel', 'DatabaseTable'] | s.primitive] AS datastores,
                   [s IN steps WHERE s.primitive_type = 'Integration' | s.primitive] AS integrations,
                   [s IN steps WHERE s.primitive_type = 'Risk' | s.primitive] AS risks
            ORDER BY confidence DESC, business_flow
            """
        )
    )


def add_business_flow_display_columns(flows: pd.DataFrame) -> pd.DataFrame:
    if flows.empty:
        return flows
    rows = flows.copy()
    rows["ordered_chain"] = rows.apply(business_flow_sentence, axis=1)
    rows["transaction_journey"] = rows.apply(lambda row: transaction_journey_kind(row)[0], axis=1)
    rows["source_hint"] = rows["source_paths"].apply(
        lambda paths: Path(str(paths[0])).name
        if isinstance(paths, list) and paths
        else "unknown source"
    )
    rows["flow_display"] = rows.apply(
        lambda row: (
            f"{row.get('transaction_journey') or 'primary journey'} · {row.get('business_flow') or row.get('id')} "
            f"· {row.get('entrypoint') or row.get('source_hint') or 'entry'} "
            f"· {str(row.get('id') or '')[-10:]}"
        ),
        axis=1,
    )
    return rows


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
def load_exact_storage_sources(project_id: str) -> pd.DataFrame:
    return df(
        run_read(
            f"""
            MATCH (ds:DataStore)
            WHERE ds.project_id = {json.dumps(project_id)}
            OPTIONAL MATCH (f:File)-[:FILE_USES_DATASTORE]->(ds)
            RETURN ds.id AS datastore_id,
                   ds.name AS database_or_storage,
                   ds.storage_type AS storage_type,
                   ds.source_path AS evidence_file,
                   collect(DISTINCT f.name)[0..6] AS linked_files,
                   ds.confidence AS confidence
            ORDER BY database_or_storage, evidence_file
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

    active_project_id = st.session_state.get("active_project_id")
    if active_project_id and "project_id" in projects:
        active = projects[projects["project_id"].astype(str) == str(active_project_id)]
        if not active.empty:
            return active.iloc[0].to_dict()

    typed_repo_path = st.session_state.get("project_repo_path")
    if typed_repo_path and "repo_path" in projects:
        try:
            typed_root = str(Path(str(typed_repo_path)).expanduser().resolve())
            by_path = projects[projects["repo_path"].fillna("").apply(
                lambda value: str(Path(str(value)).expanduser().resolve()) == typed_root
                if value else False
            )]
            if not by_path.empty:
                return by_path.iloc[0].to_dict()
        except Exception:
            pass

    ranked = projects.copy()
    ranked["_repo_exists"] = ranked["repo_path"].fillna("").apply(lambda p: Path(str(p)).expanduser().exists())
    ranked["_flows"] = pd.to_numeric(ranked.get("business_flows", 0), errors="coerce").fillna(0)
    ranked["_files"] = pd.to_numeric(ranked.get("files", 0), errors="coerce").fillna(0)
    ranked["_complete"] = ranked["analysis_status"].fillna("").eq("analysis_complete")
    ranked = ranked.sort_values(
        by=["_repo_exists", "updated_at", "_complete", "_flows", "_files"],
        ascending=[False, False, False, False, False],
    )
    return ranked.iloc[0].drop(labels=["_repo_exists", "_flows", "_files", "_complete"]).to_dict()


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
def load_architecture_proposals(project_id: str | None = None) -> pd.DataFrame:
    return df(list_architecture_proposals(project_id=project_id))


def render_architecture_proposal(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    validation = payload.get("validation", {})
    code_arch = payload.get("code_architecture", {})
    sandbox = payload.get("sandbox", {})
    database_error = payload.get("database_error")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Code Nodes", summary.get("code_nodes", 0))
    c2.metric("Connectors", summary.get("connectors", 0))
    c3.metric("Rules", summary.get("rules", 0))
    c4.metric("Test", validation.get("status", "unknown"))

    st.markdown("**Sandbox copy**")
    database_copy = sandbox.get("database_copy") or {"copied": False, "message": "No database copy was created."}
    st.json(
        {
            "status": (
                "Project was copied into an isolated sandbox."
                if sandbox.get("project_copy")
                else "Database-only sandbox; project source was not copied."
            ),
            "project_copy": sandbox.get("project_copy") or "Not copied",
            "database": database_copy,
            "excluded": sandbox.get("excluded", []),
            "credential_refs": payload.get("credential_refs", []),
        },
        expanded=False,
    )
    if database_error:
        st.error(f"Database connection/copy failed: {database_error}")
        raw_error = database_copy.get("raw_error") if isinstance(database_copy, dict) else None
        if raw_error and raw_error != database_error:
            with st.expander("Technical database error"):
                st.code(raw_error, language="text")
    if payload.get("limitations"):
        for limitation in payload["limitations"]:
            st.warning(limitation)

    left, right = st.columns(2)
    with left:
        st.markdown("**Detected architecture**")
        st.json(code_arch.get("counts", {}), expanded=False)
        if payload.get("database_connectors"):
            st.markdown("**Database connectors**")
            display_table(pd.DataFrame(payload["database_connectors"]), height=180)
    with right:
        st.markdown("**Rules for communication**")
        display_table(pd.DataFrame(payload.get("communication_rules", [])), height=260)

    st.markdown("**Validation command**")
    st.code(validation.get("command") or "No command detected", language="text")
    if validation.get("stdout"):
        with st.expander("Validation stdout"):
            st.code(validation["stdout"], language="text")
    if validation.get("stderr"):
        with st.expander("Validation stderr"):
            st.code(validation["stderr"], language="text")


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
    primitive_labels = [
        "Project", "Repository", "File", "Route", "Function", "Service",
        "DataStore", "DatabaseModel", "DatabaseTable", "Integration",
        "Risk", "BusinessFlow", "FlowStep",
    ]
    if status:
        query = f"""
        MATCH (s)
        WHERE 'SkillProposal' IN labels(s)
          AND s.status = {json.dumps(status)}
          AND none(label IN labels(s) WHERE label IN {json.dumps(primitive_labels)})
          AND NOT s.id STARTS WITH 'project_'
          AND NOT s.id STARTS WITH 'skill_project_'
        RETURN properties(s) AS props
        """
    else:
        query = f"""
        MATCH (s)
        WHERE 'SkillProposal' IN labels(s)
          AND none(label IN labels(s) WHERE label IN {json.dumps(primitive_labels)})
          AND NOT s.id STARTS WITH 'project_'
          AND NOT s.id STARTS WITH 'skill_project_'
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
                'SchemaChangeProposal',
                'AppProfile', 'WebSite', 'WebPage', 'WebEntity', 'Project',
                'Repository', 'File', 'Route', 'Service', 'Function',
                'DatabaseModel', 'DatabaseTable', 'DataStore', 'Entity', 'Workflow',
                'Integration', 'Artifact', 'Risk'
            ])
            RETURN labels(n)[0] AS type,
                   coalesce(n.id, n.app_id, n.domain, n.url, n.name, elementId(n)) AS id,
                   coalesce(n.name, n.title, n.label, n.entrypoint, n.status, '') AS name,
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


def _graph_rows_to_payload(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
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
            "stakeholder_description": row.get(f"{prefix}_stakeholder_description"),
            "method": row.get(f"{prefix}_method"),
            "route": row.get(f"{prefix}_route"),
            "storage_type": row.get(f"{prefix}_storage_type"),
            "primitive_type": row.get(f"{prefix}_primitive_type"),
            "risk_type": row.get(f"{prefix}_risk_type"),
            "severity": row.get(f"{prefix}_severity"),
            "entrypoint": row.get(f"{prefix}_entrypoint"),
            "flow_type": row.get(f"{prefix}_flow_type"),
            "evidence_summary": row.get(f"{prefix}_evidence_summary"),
            "source_paths": row.get(f"{prefix}_source_paths"),
            "order": row.get(f"{prefix}_order"),
            "step_type": row.get(f"{prefix}_step_type"),
            "primitive_id": row.get(f"{prefix}_primitive_id"),
            "primitive_label": row.get(f"{prefix}_primitive_label"),
            "evidence": row.get(f"{prefix}_evidence"),
            "business_flow_id": row.get(f"{prefix}_business_flow_id"),
            "sandbox_only": row.get(f"{prefix}_sandbox_only"),
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


def _append_optimized_flow_overlay(
    payload: dict[str, list[dict[str, Any]]],
    project_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Add approved sandbox-only optimization proposals to project graph payload."""
    rows = run_read(
        f"""
        MATCH (f:Flow)
        WHERE f.project_id = {json.dumps(project_id)}
          AND f.business_flow_id IS NOT NULL
          AND f.status IN ['active', 'approved']
        RETURN elementId(f) AS element_id,
               f.id AS id,
               f.name AS name,
               f.status AS status,
               f.project_id AS project_id,
               f.business_flow_id AS business_flow_id,
               f.avg_outcome_score AS score,
               f.justification AS justification
        ORDER BY f.id DESC
        LIMIT 30
        """
    )
    if not rows:
        return payload

    existing_node_ids = {node["id"] for node in payload["nodes"]}
    existing_edges = {
        (edge.get("from"), edge.get("to"), edge.get("label"))
        for edge in payload["edges"]
    }

    for row in rows:
        node_id = row["element_id"]
        overlay_node = {
            "id": node_id,
            "label": row.get("name") or row.get("id"),
            "group": "Flow",
            "status": "sandbox_approved",
            "score": row.get("score"),
            "project_id": row.get("project_id"),
            "description": row.get("justification"),
            "business_flow_id": row.get("business_flow_id"),
            "sandbox_only": True,
        }
        if node_id in existing_node_ids:
            for node in payload["nodes"]:
                if node["id"] == node_id:
                    node.update({k: v for k, v in overlay_node.items() if v is not None})
                    break
        else:
            payload["nodes"].append(overlay_node)
            existing_node_ids.add(node_id)

        target_business_flow_id = row.get("business_flow_id")
        target = next(
            (
                node for node in payload["nodes"]
                if node.get("group") == "BusinessFlow"
                and (
                    node.get("primitive_id") == target_business_flow_id
                    or node.get("label") == target_business_flow_id
                    or node.get("id") == target_business_flow_id
                )
            ),
            None,
        )
        if not target and target_business_flow_id:
            matched = run_read(
                f"""
                MATCH (bf:BusinessFlow {{id: {json.dumps(target_business_flow_id)}}})
                RETURN elementId(bf) AS element_id, bf.name AS name, bf.id AS id,
                       bf.project_id AS project_id, bf.confidence AS confidence
                LIMIT 1
                """
            )
            if matched:
                bf = matched[0]
                target = {
                    "id": bf["element_id"],
                    "label": bf.get("name") or bf.get("id"),
                    "group": "BusinessFlow",
                    "status": None,
                    "project_id": bf.get("project_id"),
                    "confidence": bf.get("confidence"),
                }
                if target["id"] not in existing_node_ids:
                    payload["nodes"].append(target)
                    existing_node_ids.add(target["id"])

        if target:
            edge_key = (target["id"], node_id, "APPROVED_SANDBOX_OPTIMIZATION")
            if edge_key not in existing_edges:
                payload["edges"].append(
                    {
                        "from": target["id"],
                        "to": node_id,
                        "label": "APPROVED_SANDBOX_OPTIMIZATION",
                        "color": {"color": "#9d174d", "highlight": "#7f123f", "hover": "#7f123f"},
                        "width": 3,
                        "dashes": True,
                    }
                )
                existing_edges.add(edge_key)
    return payload


def _graph_return_clause() -> str:
    return """
           elementId(n) AS source_id,
           labels(n) AS source_labels,
           coalesce(properties(n).display_name, properties(n).name, properties(n).id, properties(n).path, elementId(n)) AS source_name,
           properties(n).status AS source_status,
           properties(n).avg_outcome_score AS source_score,
           properties(n).industry AS source_industry,
           properties(n).stage AS source_stage,
           properties(n).pain_points AS source_pain,
           properties(n).revenue AS source_revenue,
           properties(n).expertise AS source_expertise,
           properties(n).success_score AS source_success,
           properties(n).available AS source_available,
           properties(n).current_load AS source_load,
           properties(n).region AS source_region,
           properties(n).performance_score AS source_perf,
           properties(n).error_rate AS source_error,
           properties(n).project_id AS source_project_id,
           properties(n).scan_id AS source_scan_id,
           properties(n).source_path AS source_path,
           properties(n).path AS source_file_path,
           properties(n).confidence AS source_confidence,
           properties(n).description AS source_description,
           properties(n).technical_description AS source_technical_description,
           coalesce(properties(n).stakeholder_description, properties(n).business_description) AS source_stakeholder_description,
           properties(n).method AS source_method,
           coalesce(properties(n).route_path, properties(n).route) AS source_route,
           properties(n).storage_type AS source_storage_type,
           properties(n).primitive_type AS source_primitive_type,
           properties(n).risk_type AS source_risk_type,
           properties(n).severity AS source_severity,
           properties(n).entrypoint AS source_entrypoint,
           properties(n).flow_type AS source_flow_type,
           properties(n).evidence_summary AS source_evidence_summary,
           properties(n).source_paths AS source_source_paths,
           properties(n).order AS source_order,
           properties(n).step_type AS source_step_type,
           properties(n).primitive_id AS source_primitive_id,
           properties(n).primitive_label AS source_primitive_label,
           properties(n).evidence AS source_evidence,
           properties(n).business_flow_id AS source_business_flow_id,
           properties(n).sandbox_only AS source_sandbox_only,
           type(r) AS rel_type,
           elementId(m) AS target_id,
           labels(m) AS target_labels,
           coalesce(properties(m).display_name, properties(m).name, properties(m).id, properties(m).path, elementId(m)) AS target_name,
           properties(m).status AS target_status,
           properties(m).avg_outcome_score AS target_score,
           properties(m).industry AS target_industry,
           properties(m).stage AS target_stage,
           properties(m).pain_points AS target_pain,
           properties(m).revenue AS target_revenue,
           properties(m).expertise AS target_expertise,
           properties(m).success_score AS target_success,
           properties(m).available AS target_available,
           properties(m).current_load AS target_load,
           properties(m).region AS target_region,
           properties(m).performance_score AS target_perf,
           properties(m).error_rate AS target_error,
           properties(m).project_id AS target_project_id,
           properties(m).scan_id AS target_scan_id,
           properties(m).source_path AS target_path,
           properties(m).path AS target_file_path,
           properties(m).confidence AS target_confidence,
           properties(m).description AS target_description,
           properties(m).technical_description AS target_technical_description,
           coalesce(properties(m).stakeholder_description, properties(m).business_description) AS target_stakeholder_description,
           properties(m).method AS target_method,
           coalesce(properties(m).route_path, properties(m).route) AS target_route,
           properties(m).storage_type AS target_storage_type,
           properties(m).primitive_type AS target_primitive_type,
           properties(m).risk_type AS target_risk_type,
           properties(m).severity AS target_severity,
           properties(m).entrypoint AS target_entrypoint,
           properties(m).flow_type AS target_flow_type,
           properties(m).evidence_summary AS target_evidence_summary,
           properties(m).source_paths AS target_source_paths,
           properties(m).order AS target_order,
           properties(m).step_type AS target_step_type,
           properties(m).primitive_id AS target_primitive_id,
           properties(m).primitive_label AS target_primitive_label,
           properties(m).evidence AS target_evidence,
           properties(m).business_flow_id AS target_business_flow_id,
           properties(m).sandbox_only AS target_sandbox_only
    """



@st.cache_data(ttl=20)
def load_legacy_graph_payload(limit: int = 180, scope: str = "Dual graph") -> dict[str, list[dict[str, Any]]]:
    scope_labels = {
        "Dual graph": [
            "Company", "Mentor", "Programme", "Flow", "Skill", "Connector", "Server"
        ],
        "Graph A: History": ["Company", "Mentor", "Programme"],
        "Legacy Graph B: Flow Runtime": ["Flow", "Skill", "Connector", "Server"],
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
        RETURN {_graph_return_clause()}
        """
    )
    return _graph_rows_to_payload(rows)


@st.cache_data(ttl=20)
def load_agentic_architecture_graph_payload(project_id: str) -> dict[str, list[dict[str, Any]]]:
    """Build the dedicated agentic architecture graph shown by the last graph scope."""
    project_rows = run_read(
        f"""
        MATCH (p:Project {{project_id: {json.dumps(project_id)}}})
        RETURN p.name AS name, p.repo_path AS repo_path, p.last_scan_id AS last_scan_id
        LIMIT 1
        """
    )
    project_row = project_rows[0] if project_rows else {}

    dynamic_rows = run_read(
        f"""
        OPTIONAL MATCH (f:Flow)
        WHERE f.project_id = {json.dumps(project_id)}
          AND f.status IN ['proposed', 'approved', 'active']
        WITH collect({{
            id: f.id,
            name: coalesce(f.name, f.id),
            status: f.status,
            score: f.avg_outcome_score
        }})[..8] AS flows
        OPTIONAL MATCH (s:Skill)
        WHERE s.project_id = {json.dumps(project_id)}
           OR EXISTS {{
                MATCH (s)-[:SKILL_DERIVED_FROM_FUNCTION]->(:Function {{project_id: {json.dumps(project_id)}}})
           }}
        WITH flows, collect({{
            id: coalesce(s.id, s.name),
            name: coalesce(s.name, s.id),
            performance: s.performance_score
        }})[..8] AS skills
        OPTIONAL MATCH (ap:ArchitectureProposal {{project_id: {json.dumps(project_id)}}})
        WITH flows, skills, collect({{
            id: ap.id,
            name: coalesce(ap.summary, ap.id),
            status: ap.status,
            tested: ap.tested
        }})[..5] AS architecture_proposals
        RETURN flows, skills, architecture_proposals
        """
    )
    dynamic = dynamic_rows[0] if dynamic_rows else {}
    flows = [row for row in dynamic.get("flows", []) if row.get("id")]
    skills = [row for row in dynamic.get("skills", []) if row.get("id")]
    architecture_proposals = [
        row for row in dynamic.get("architecture_proposals", []) if row.get("id")
    ]

    nodes: list[dict[str, Any]] = [
        {
            "id": "project",
            "label": project_row.get("name") or "Connected Project",
            "group": "Project",
            "project_id": project_id,
            "source_path": project_row.get("repo_path"),
            "scan_id": project_row.get("last_scan_id"),
            "description": "Application codebase connected to the agentic system.",
        },
        {"id": "graph_store", "label": "Neo4j Graph Store", "group": "GraphStore", "description": "Typed project graph, flow registry, execution traces, approvals, and architecture metadata."},
        {"id": "graphrag", "label": "GraphRAG Retriever", "group": "AgentTool", "description": "Retrieves graph evidence for planning, critique, and architecture review."},
        {"id": "event_bus", "label": "Realtime Event Bus", "group": "Runtime", "description": "Publishes agent phases, sandbox logs, approvals, and UI updates."},
        {"id": "planner", "label": "Planner", "group": "Agent", "description": "Finds evidence and forms an improvement hypothesis."},
        {"id": "generator", "label": "Generator", "group": "Agent", "description": "Drafts flow YAML, code patches, or architecture proposals."},
        {"id": "critic", "label": "Critic", "group": "Agent", "description": "Validates skills, connectors, policies, and infrastructure assumptions."},
        {"id": "simulator", "label": "Simulator", "group": "Agent", "description": "Runs sandbox tests and records metrics."},
        {"id": "evaluator", "label": "Evaluator", "group": "Agent", "description": "Compares sandbox score against baseline and retry thresholds."},
        {"id": "human_approval", "label": "Human Approval", "group": "Approval", "description": "Admin gate for flow, skill, and architecture changes."},
        {"id": "flow_registry", "label": "Flow Registry", "group": "Registry", "description": "Approved and active flows used by the runtime."},
        {"id": "skill_registry", "label": "Skill Registry", "group": "Registry", "description": "Reusable skills derived from functions or human-approved proposals."},
        {"id": "sandbox", "label": "Sandbox Runner", "group": "SandboxRuntime", "description": "Isolated execution boundary for simulations and architecture tests."},
        {"id": "cloud_run", "label": "Cloud Run Job", "group": "SandboxRuntime", "description": "Remote container job that executes sandbox-system/sandbox_task.py."},
        {"id": "architecture_sandbox", "label": "Architecture Sandbox", "group": "SandboxRuntime", "description": "Copies project/database shape and validates proposed architecture in isolation."},
        {"id": "architecture_approval", "label": "Architecture Approval Queue", "group": "Approval", "description": "Tested architecture proposals awaiting approval or rejection."},
    ]

    edges: list[dict[str, Any]] = [
        {"from": "project", "to": "graph_store", "label": "INDEXED_IN"},
        {"from": "graph_store", "to": "graphrag", "label": "RETRIEVED_BY"},
        {"from": "graphrag", "to": "planner", "label": "EVIDENCE"},
        {"from": "planner", "to": "generator", "label": "HYPOTHESIS"},
        {"from": "generator", "to": "critic", "label": "PROPOSAL"},
        {"from": "critic", "to": "simulator", "label": "VALIDATED"},
        {"from": "simulator", "to": "sandbox", "label": "RUNS"},
        {"from": "sandbox", "to": "cloud_run", "label": "EXECUTES_ON"},
        {"from": "simulator", "to": "evaluator", "label": "METRICS"},
        {"from": "evaluator", "to": "generator", "label": "RETRY"},
        {"from": "evaluator", "to": "human_approval", "label": "APPROVAL_REQUIRED"},
        {"from": "human_approval", "to": "flow_registry", "label": "ACTIVATES"},
        {"from": "flow_registry", "to": "graph_store", "label": "WRITES"},
        {"from": "skill_registry", "to": "generator", "label": "AVAILABLE_SKILLS"},
        {"from": "generator", "to": "architecture_sandbox", "label": "ARCHITECTURE_PROPOSAL"},
        {"from": "architecture_sandbox", "to": "architecture_approval", "label": "TESTED"},
        {"from": "architecture_approval", "to": "graph_store", "label": "APPROVED_METADATA"},
        {"from": "event_bus", "to": "planner", "label": "OBSERVES"},
        {"from": "event_bus", "to": "simulator", "label": "LOGS"},
        {"from": "event_bus", "to": "human_approval", "label": "NOTIFIES"},
    ]

    for row in flows:
        node_id = f"flow:{row['id']}"
        nodes.append({
            "id": node_id,
            "label": row.get("name") or row["id"],
            "group": "Flow",
            "status": row.get("status"),
            "score": row.get("score"),
            "description": "Project flow managed by the agentic registry.",
        })
        edges.append({"from": "flow_registry", "to": node_id, "label": "CONTAINS"})
        edges.append({"from": node_id, "to": "sandbox", "label": "TESTED_IN"})

    for row in skills:
        node_id = f"skill:{row['id']}"
        nodes.append({
            "id": node_id,
            "label": row.get("name") or row["id"],
            "group": "Skill",
            "perf": row.get("performance"),
            "description": "Skill available to generator/simulator.",
        })
        edges.append({"from": "skill_registry", "to": node_id, "label": "CONTAINS"})

    for row in architecture_proposals:
        node_id = f"arch:{row['id']}"
        nodes.append({
            "id": node_id,
            "label": row.get("name") or row["id"],
            "group": "ArchitectureProposal",
            "status": row.get("status"),
            "description": f"Tested: {row.get('tested')}",
        })
        edges.append({"from": "architecture_approval", "to": node_id, "label": "QUEUES"})

    return {"nodes": nodes, "edges": edges}


@st.cache_data(ttl=20)
def load_project_graph_payload(
    project_id: str,
    limit: int = 180,
    scope: str = "Full Project Graph",
) -> dict[str, list[dict[str, Any]]]:
    if scope == "Agentic Layer Links":
        return load_agentic_architecture_graph_payload(project_id)

    scope_labels = {
        "Full Project Graph": [
            "Project", "Repository", "File", "Route", "Service", "Function",
            "DatabaseModel", "DatabaseTable", "DataStore", "Entity", "Workflow",
            "BusinessFlow", "FlowStep", "Integration", "Artifact", "Risk", "Skill", "Flow",
        ],
        "Software Architecture": [
            "Project", "Repository", "File", "Route", "Service", "Function",
            "DatabaseModel", "DatabaseTable", "Entity", "Integration", "Artifact",
        ],
        "Workflow Pipeline": [
            "Project", "BusinessFlow", "FlowStep", "File", "Route", "Service",
            "Function", "DatabaseModel", "DatabaseTable", "DataStore", "Integration", "Risk", "Flow",
        ],
        "Storage & Risk": [
            "Project", "Repository", "File", "DataStore", "DatabaseModel",
            "DatabaseTable", "Risk", "Integration",
        ],
    }
    labels = scope_labels.get(scope, scope_labels["Full Project Graph"])
    label_filter = json.dumps(labels)
    project_json = json.dumps(project_id)
    rows = run_read(
        f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN {label_filter})
          AND (
            n.project_id = {project_json}
            OR n.id = {project_json}
            OR (
              'Skill' IN labels(n)
              AND EXISTS {{
                MATCH (n)-[:SKILL_DERIVED_FROM_FUNCTION]->(:Function {{project_id: {project_json}}})
              }}
            )
          )
        WITH n LIMIT {limit}
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE any(label IN labels(m) WHERE label IN {label_filter})
          AND (
            m.project_id = {project_json}
            OR m.id = {project_json}
            OR (
              'Skill' IN labels(m)
              AND EXISTS {{
                MATCH (m)-[:SKILL_DERIVED_FROM_FUNCTION]->(:Function {{project_id: {project_json}}})
              }}
            )
          )
        RETURN {_graph_return_clause()}
        """
    )

    payload = _graph_rows_to_payload(rows)
    return _append_optimized_flow_overlay(payload, project_id)


def clear_data_cache() -> None:
    st.cache_data.clear()


def realtime_status() -> dict[str, Any]:
    try:
        response = requests.get(f"{REALTIME_API_BASE}/health", timeout=0.6)
        response.raise_for_status()
        return {"connected": True, **response.json()}
    except Exception:
        return {"connected": False, "status": "disconnected", "clients": 0}


def ensure_realtime_server() -> dict[str, Any]:
    status = realtime_status()
    if status["connected"]:
        return status

    proc = st.session_state.get("realtime_server_proc")
    if proc is not None and getattr(proc, "poll", lambda: None)() is None:
        time.sleep(0.4)
        return realtime_status()

    try:
        st.session_state["realtime_server_proc"] = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.realtime.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8765",
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(8):
            time.sleep(0.25)
            status = realtime_status()
            if status["connected"]:
                return status
    except Exception:
        pass
    return realtime_status()


def run_agent(
    goal: str,
    project_id: str | None = None,
    business_flow_id: str | None = None,
    source_path: str | None = None,
    proposal_only: bool = False,
) -> tuple[int, str, str, str | None]:
    env = os.environ.copy()
    cmd = [sys.executable, "main.py", "--goal", goal]
    if project_id:
        cmd.extend(["--project-id", project_id])
    if business_flow_id:
        cmd.extend(["--business-flow-id", business_flow_id])
    if source_path:
        cmd.extend(["--source-path", source_path])
    if proposal_only:
        cmd.append("--proposal-only")
    result = subprocess.run(
        cmd,
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
    styled = (
        data.style
        .set_table_styles(
            [
                {
                    "selector": "thead th",
                    "props": [
                        ("background-color", "#f5eef2"),
                        ("color", "#20181d"),
                        ("border-bottom", "1px solid #eadde4"),
                        ("font-weight", "700"),
                    ],
                },
                {
                    "selector": "tbody td",
                    "props": [
                        ("background-color", "#fff9fc"),
                        ("color", "#20181d"),
                        ("border-color", "#f0e5eb"),
                    ],
                },
                {
                    "selector": "tbody tr:nth-child(even) td",
                    "props": [("background-color", "#fcf5f8")],
                },
            ]
        )
    )
    st.dataframe(styled, width="stretch", height=height, hide_index=True)


def ui_value(value: Any, fallback: str = "Not linked") -> str:
    if value is None:
        return fallback
    if isinstance(value, float) and pd.isna(value):
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return fallback
    return text


def render_merge_success_panel(merge: dict[str, Any]) -> None:
    flow_name = ui_value(merge.get("flow_name") or merge.get("name"), "Flow")
    flow_id = ui_value(merge.get("flow_id") or merge.get("id"), "unknown")
    event_id = ui_value(merge.get("merge_event_id"), "not recorded")
    merged_at = ui_value(merge.get("last_registry_merge_at"), "just now")
    merge_count = ui_value(merge.get("registry_merge_count"), "1")
    st.success(f"Flow **{flow_name}** is now active in the registry.")
    with st.container(border=True):
        st.markdown("**Registry merge recorded**")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Flow", flow_name)
        m2.metric("Status", "active")
        m3.metric("Merge count", merge_count)
        m4.metric("Event", event_id)
        st.caption(f"Flow ID `{flow_id}` was merged at `{merged_at}` and stored in Neo4j.")


def schema_columns_table(schema: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table in schema or []:
        table_name = table.get("table") or table.get("name") or "unknown_table"
        columns = table.get("columns") or []
        if not isinstance(columns, list):
            rows.append({"table": table_name, "column": str(columns), "type": ""})
            continue
        for column in columns:
            if isinstance(column, dict):
                rows.append(
                    {
                        "table": table_name,
                        "column": column.get("name") or "",
                        "type": column.get("type") or "",
                    }
                )
            else:
                rows.append({"table": table_name, "column": str(column), "type": ""})
    return pd.DataFrame(rows)


def proposal_payload(raw: Any) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, indent=2)
    except (TypeError, json.JSONDecodeError):
        return str(raw)


def parse_proposal_payload(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


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


def flow_needs_optimization(row: pd.Series, threshold: float = 8.5) -> tuple[bool, str]:
    score = float(row.get("score") or 0)
    risks = row.get("risks") or []
    if isinstance(risks, str):
        risks = [risks] if risks else []
    if score >= threshold and not risks:
        return (
            True,
            f"This flow has strong static evidence: score {score:.1f} is above the {threshold:.1f} review threshold and no explicit risks were detected. You can still run an agent review to verify it and look for improvement opportunities.",
        )
    if risks:
        return True, "Optimization is available because this flow has graph-detected risks to review."
    return True, f"Optimization is available because this flow score is below the {threshold:.1f} threshold."


def _chain_items(chain: Any, fallback: Any = None) -> list[str]:
    if isinstance(chain, list):
        values = [
            str(item.get("step") or item.get("name") or item.get("primitive") or item)
            for item in chain
        ]
    elif isinstance(chain, str):
        values = [part.strip() for part in re.split(r"\s*(?:->|→)\s*", chain) if part.strip()]
    else:
        values = []
    if not values and fallback:
        return _chain_items(fallback)
    return values


def render_flow_chips(items: list[str], accent: str = "#9d174d") -> None:
    if not items:
        st.caption("No ordered graph chain captured.")
        return
    chips = ""
    for i, item in enumerate(items[:8]):
        chips += (
            f"<span style='display:inline-block;border:1px solid rgba(15,123,99,.35);"
            f"background:#f5fbf8;color:#20181d;border-radius:999px;padding:5px 10px;"
            f"font-size:.75rem;margin:3px 4px 3px 0;'>{item}</span>"
        )
        if i < min(len(items), 8) - 1:
            chips += f"<span style='color:{accent};font-weight:700;margin-right:4px;'>→</span>"
    if len(items) > 8:
        chips += f"<span style='color:#6f626a;font-size:.75rem;'>+{len(items) - 8} more</span>"
    st.markdown(chips, unsafe_allow_html=True)


def _dot_escape(value: Any) -> str:
    text = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return text[:80]


def _dot_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def _step_label(step: Any, fallback: str = "Step") -> str:
    if isinstance(step, dict):
        return str(
            step.get("step")
            or step.get("name")
            or step.get("id")
            or step.get("primitive")
            or step.get("action_type")
            or fallback
        )
    return str(step or fallback)


def proposed_flow_steps(proposed_summary: dict[str, Any]) -> list[dict[str, Any]]:
    flow_yaml = proposed_summary.get("flow_yaml") or ""
    if flow_yaml:
        try:
            parsed = yaml.safe_load(flow_yaml) or {}
            if isinstance(parsed, dict):
                raw_steps = parsed.get("steps") or []
                if isinstance(raw_steps, list) and raw_steps:
                    return [
                        {
                            "label": _step_label(step, f"Step {i + 1}"),
                            "kind": "Workflow step",
                            "detail": (
                                step.get("skill")
                                or step.get("skill_id")
                                or step.get("description")
                                or ""
                            ) if isinstance(step, dict) else "",
                        }
                        for i, step in enumerate(raw_steps)
                    ]
        except yaml.YAMLError:
            pass

    actions = proposed_summary.get("recommended_actions") or []
    return [
        {
            "label": str(action.get("action_type") or f"Action {i + 1}"),
            "kind": "Proposal action",
            "detail": str(action.get("description") or ""),
        }
        for i, action in enumerate(actions)
        if isinstance(action, dict)
    ]


def before_flow_steps(before_summary: dict[str, Any]) -> list[dict[str, Any]]:
    chain = _chain_items(
        before_summary.get("ordered_chain"),
        before_summary.get("graph_evidence", {}).get("steps")
        if isinstance(before_summary.get("graph_evidence"), dict)
        else None,
    )
    return [{"label": item, "kind": "Current step", "detail": ""} for item in chain]


def flow_graph_dot(
    *,
    title: str,
    steps: list[dict[str, Any]],
    accent: str = "#9d174d",
) -> str:
    if not steps:
        steps = [{"label": "No steps captured", "kind": "Empty", "detail": ""}]
    lines = [
        "digraph G {",
        "  graph [rankdir=LR, bgcolor=\"transparent\", pad=\"0.2\", nodesep=\"0.45\", ranksep=\"0.55\"];",
        "  node [shape=box, style=\"rounded,filled\", fontname=\"Helvetica\", fontsize=10, margin=\"0.12,0.08\", color=\"#d9c4cf\", fillcolor=\"#ffffff\", fontcolor=\"#20181d\"];",
        "  edge [color=\"#cbb9c3\", penwidth=1.7, arrowsize=0.7];",
        f"  label=\"{_dot_escape(title)}\";",
        "  labelloc=\"t\";",
        "  fontsize=12;",
        "  fontname=\"Helvetica-Bold\";",
    ]
    for i, step in enumerate(steps[:12]):
        node_id = _dot_id("s", i)
        label = _dot_escape(step.get("label"))
        detail = _dot_escape(step.get("detail"))
        kind = _dot_escape(step.get("kind"))
        fill = "#eaf4ef" if i else "#eef3fb"
        color = accent if i else "#4f6f8f"
        dot_label = f"{i + 1}. {label}"
        if detail:
            dot_label += f"\\n{detail[:70]}"
        elif kind:
            dot_label += f"\\n{kind}"
        lines.append(
            f"  {node_id} [label=\"{dot_label}\", fillcolor=\"{fill}\", color=\"{color}\"];"
        )
    for i in range(max(0, min(len(steps), 12) - 1)):
        lines.append(f"  {_dot_id('s', i)} -> {_dot_id('s', i + 1)};")
    if len(steps) > 12:
        lines.append("  more [label=\"More steps hidden\", fillcolor=\"#fcfafb\", color=\"#d9c4cf\"];")
        lines.append(f"  {_dot_id('s', 11)} -> more;")
    lines.append("}")
    return "\n".join(lines)


def render_optimized_flow_graph(
    before_summary: dict[str, Any],
    proposed_summary: dict[str, Any],
) -> None:
    proposed_steps = proposed_flow_steps(proposed_summary)
    before_steps = before_flow_steps(before_summary)
    st.markdown("**Current vs Optimized Flow**")
    left, right = st.columns(2)
    with left:
        st.caption("Current detected flow")
        st.graphviz_chart(
            flow_graph_dot(
                title="Current flow",
                steps=before_steps,
                accent="#4f6f8f",
            ),
            use_container_width=True,
        )
    with right:
        st.caption("Optimized sandbox proposal")
        st.graphviz_chart(
            flow_graph_dot(
                title="Optimized flow",
                steps=proposed_steps,
                accent="#9d174d",
            ),
            use_container_width=True,
        )
    with st.expander("Open larger graph preview", expanded=False):
        graph_tabs = st.tabs(["Optimized Flow", "Current Flow"])
        with graph_tabs[0]:
            st.graphviz_chart(
                flow_graph_dot(
                    title="Optimized sandbox flow - approved state is still not real code",
                    steps=proposed_steps,
                    accent="#9d174d",
                ),
                use_container_width=True,
            )
        with graph_tabs[1]:
            st.graphviz_chart(
                flow_graph_dot(
                    title="Current detected flow",
                    steps=before_steps,
                    accent="#4f6f8f",
                ),
                use_container_width=True,
            )


def _graphrag_dot(context: dict[str, Any], flow_name: str = "") -> str:
    """Build a graphviz DOT string from a GraphRAG context dict."""
    industry = _dot_escape(context.get("industry") or "Unknown")
    baseline = context.get("baseline_score") or 0
    failures = context.get("failure_patterns") or []
    successes = context.get("success_patterns") or []
    skills = context.get("available_skills") or []
    active_flows = context.get("active_flows") or []

    lines = [
        "digraph GraphRAG {",
        '  graph [rankdir=TB, bgcolor="transparent", pad="0.3", nodesep="0.5", ranksep="0.7"];',
        '  node [fontname="Helvetica", fontsize=10, margin="0.14,0.09"];',
        '  edge [fontname="Helvetica", fontsize=9, penwidth=1.4, arrowsize=0.65];',
    ]

    # Industry node
    title_label = _dot_escape(flow_name) + "\\n" if flow_name else ""
    lines.append(
        f'  industry [label="{title_label}{industry}\\nbaseline {baseline:.1f}", '
        'shape=rectangle, style="rounded,filled", fillcolor="#eef3fb", '
        'color="#4f6f8f", fontcolor="#20181d", fontsize=12, penwidth=2];'
    )

    # Failure cluster
    if failures:
        lines.append('  subgraph cluster_failures {')
        lines.append('    label="Failure Patterns"; style="dashed"; color="#b4234a"; fontcolor="#b4234a"; fontsize=10;')
        for i, fp in enumerate(failures[:4]):
            co = _dot_escape(fp.get("company") or f"Company {i+1}")
            me = _dot_escape(fp.get("mentor") or "")
            sc = fp.get("score") or 0
            lbl = f"{co}"
            if me:
                lbl += f"\\n→ {me}"
            lbl += f"\\nscore {sc:.1f}"
            lines.append(
                f'    f{i} [label="{lbl}", shape=box, style="filled", '
                f'fillcolor="#fde8e8", color="#b4234a", fontcolor="#b4234a"];'
            )
        lines.append("  }")
        for i in range(min(len(failures), 4)):
            lines.append(f'  industry -> f{i} [style=dashed, color="#b4234a", label="avoid"];')

    # Success cluster
    if successes:
        lines.append('  subgraph cluster_successes {')
        lines.append('    label="Success Patterns"; style="filled"; fillcolor="#f0faf5"; color="#3f6f5b"; fontcolor="#3f6f5b"; fontsize=10;')
        for i, sp in enumerate(successes[:4]):
            co = _dot_escape(sp.get("company") or f"Company {i+1}")
            me = _dot_escape(sp.get("mentor") or "")
            sc = sp.get("score") or 0
            lbl = f"{co}"
            if me:
                lbl += f"\\n→ {me}"
            lbl += f"\\nscore {sc:.1f}"
            lines.append(
                f'    s{i} [label="{lbl}", shape=box, style="filled", '
                f'fillcolor="#eaf4ef", color="#3f6f5b", fontcolor="#3f6f5b"];'
            )
        lines.append("  }")
        for i in range(min(len(successes), 4)):
            lines.append(f'  industry -> s{i} [color="#3f6f5b", label="learn"];')

    # Skills cluster
    if skills:
        lines.append('  subgraph cluster_skills {')
        lines.append('    label="Available Skills"; color="#9d174d"; fontcolor="#9d174d"; fontsize=10;')
        for i, sk in enumerate(skills[:6]):
            name = _dot_escape(sk.get("name") or sk.get("id") or f"skill_{i}")
            lang = _dot_escape(sk.get("language") or "")
            perf = sk.get("performance_score") or 0
            lbl = name
            if lang:
                lbl += f"\\n{lang}"
            if perf:
                lbl += f"  {perf:.1f}"
            lines.append(
                f'    sk{i} [label="{lbl}", shape=ellipse, style="filled", '
                f'fillcolor="#eaf4ef", color="#9d174d", fontcolor="#9d174d"];'
            )
        lines.append("  }")
        if active_flows:
            for j, fl in enumerate(active_flows[:3]):
                for sk_name in (fl.get("skill_names") or [])[:2]:
                    for i, sk in enumerate(skills[:6]):
                        if (sk.get("name") or "") == sk_name:
                            lines.append(f'  fl{j} -> sk{i} [color="#9d174d", style=dashed];')

    # Active flows
    if active_flows:
        lines.append('  subgraph cluster_flows {')
        lines.append('    label="Active Flows"; color="#7a4f93"; fontcolor="#7a4f93"; fontsize=10;')
        for j, fl in enumerate(active_flows[:3]):
            name = _dot_escape(fl.get("name") or fl.get("flow_id") or f"flow_{j}")
            sc = fl.get("avg_score") or 0
            lbl = name
            if sc:
                lbl += f"\\nscore {sc:.1f}"
            lines.append(
                f'    fl{j} [label="{lbl}", shape=diamond, style="filled", '
                f'fillcolor="#f3effb", color="#7a4f93", fontcolor="#7a4f93"];'
            )
        lines.append("  }")
        for j in range(min(len(active_flows), 3)):
            lines.append(f'  industry -> fl{j} [color="#7a4f93", label="flow"];')

    lines.append("}")
    return "\n".join(lines)


def render_graphrag_context_viz(context: dict[str, Any], flow_name: str = "") -> None:
    """Render GraphRAG evidence graph and summary metrics used during optimization."""
    failures = context.get("failure_patterns") or []
    successes = context.get("success_patterns") or []
    skills = context.get("available_skills") or []
    flows = context.get("active_flows") or []
    sw_nodes = context.get("software_nodes") or []

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Industry", context.get("industry") or "—")
    c2.metric("Baseline", f"{context.get('baseline_score') or 0:.1f}")
    c3.metric("Failure patterns", len(failures))
    c4.metric("Success patterns", len(successes))
    c5.metric("Skills available", len(skills))

    col_graph, col_detail = st.columns([3, 2])
    with col_graph:
        st.caption("GraphRAG evidence graph — entities the agent reasoned over")
        try:
            st.graphviz_chart(_graphrag_dot(context, flow_name), use_container_width=True)
        except Exception as exc:
            st.warning(f"Graph render failed: {exc}")

    with col_detail:
        detail_tabs = st.tabs(["Failures", "Successes", "Skills", "Software"])
        with detail_tabs[0]:
            if failures:
                display_table(
                    df([{k: v for k, v in fp.items() if k in ("company", "mentor", "score", "feedback")} for fp in failures]),
                    height=220,
                )
            else:
                st.caption("No failure patterns found for this industry.")
        with detail_tabs[1]:
            if successes:
                display_table(
                    df([{k: v for k, v in sp.items() if k in ("company", "mentor", "score", "feedback")} for sp in successes]),
                    height=220,
                )
            else:
                st.caption("No success patterns found.")
        with detail_tabs[2]:
            if skills:
                display_table(
                    df([{k: v for k, v in sk.items() if k in ("name", "language", "performance_score", "avg_execution_ms")} for sk in skills]),
                    height=220,
                )
            else:
                st.caption("No skills in graph yet.")
        with detail_tabs[3]:
            if sw_nodes:
                display_table(
                    df([{k: v for k, v in n.items() if k in ("_label", "name", "source_path", "description")} for n in sw_nodes[:30]]),
                    height=220,
                )
            else:
                st.caption("No software nodes indexed for this project.")


def render_admin_sandbox_preview(parsed_payload: dict[str, Any]) -> None:
    sandbox = parsed_payload.get("sandbox_result") or {}
    proposed = parsed_payload.get("proposed_summary") or {}
    actions = proposed.get("recommended_actions") or parsed_payload.get("recommended_actions") or []
    metrics = sandbox.get("metrics") if isinstance(sandbox.get("metrics"), dict) else {}
    traces = sandbox.get("traces") if isinstance(sandbox.get("traces"), list) else []

    with st.expander("Open Sandbox Preview", expanded=False):
        st.caption("Review-only preview. It does not mutate the real project code.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Validation", str(sandbox.get("status") or "not run").title())
        c2.metric("Mode", metrics.get("validation_mode", proposed.get("proposal_mode", "proposal")))
        c3.metric("Actions", len(actions))
        c4.metric("Evidence", metrics.get("evidence_count", "n/a"))

        if sandbox.get("error_log"):
            st.error(sandbox["error_log"])
        elif metrics.get("validation_mode") == "graph_review":
            st.success("Graph evidence review passed. The optimized flow is ready for human approval.")

        if actions:
            action_rows = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                evidence = action.get("evidence_node_ids") or []
                action_rows.append(
                    {
                        "action": action.get("action_type"),
                        "target": compact_list([action.get("target_node_id")], 1),
                        "evidence_nodes": len(evidence),
                        "admin_summary": action.get("description"),
                    }
                )
            if action_rows:
                st.markdown("**Recommended changes**")
                display_table(pd.DataFrame(action_rows), height=220)

        if metrics:
            metric_rows = [
                {"metric": key, "value": value}
                for key, value in metrics.items()
                if key not in {"validation_mode"}
            ]
            if metric_rows:
                st.markdown("**Validation metrics**")
                display_table(pd.DataFrame(metric_rows), height=180)

        if traces:
            st.markdown("**Evidence trace**")
            display_table(pd.DataFrame(traces[:12]), height=240)


def render_human_proposal_card(
    *,
    title: str,
    before_summary: dict[str, Any],
    proposed_summary: dict[str, Any],
    justification: str | None = None,
    parsed_payload: dict[str, Any] | None = None,
) -> None:
    before_chain = _chain_items(
        before_summary.get("ordered_chain"),
        before_summary.get("graph_evidence", {}).get("steps") if isinstance(before_summary.get("graph_evidence"), dict) else None,
    )
    actions = proposed_summary.get("recommended_actions") or []
    action_text = [
        str(action.get("description") or action.get("action_type") or "")
        for action in actions
        if isinstance(action, dict) and (action.get("description") or action.get("action_type"))
    ]
    if not action_text and proposed_summary.get("hypothesis"):
        action_text = [str(proposed_summary.get("hypothesis"))]

    st.markdown(f"#### {title}")
    c_before, c_after = st.columns(2)
    with c_before:
        st.markdown("**Before**")
        st.caption(before_summary.get("business_flow") or "Selected business flow")
        render_flow_chips(before_chain)
        if before_summary.get("baseline_score") is not None:
            st.metric("Baseline", round(float(before_summary.get("baseline_score") or 0), 2))
    with c_after:
        st.markdown("**Proposed Now**")
        st.caption(proposed_summary.get("title") or "Human-review proposal")
        if action_text:
            for action in action_text[:4]:
                st.markdown(f"- {action}")
        else:
            st.caption("No action summary captured.")
        st.success(f"Code changed: {proposed_summary.get('code_mutation', 'none')}")
    if justification:
        st.info(justification)
    render_optimized_flow_graph(before_summary, proposed_summary)
    if parsed_payload:
        render_admin_sandbox_preview(parsed_payload)


def render_sandbox_review(result: dict[str, Any]) -> None:
    """Show sandbox output as an operator-readable review instead of raw JSON."""
    status = str(result.get("status") or "unknown")
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    traces = result.get("traces") if isinstance(result.get("traces"), list) else []
    run_meta = result.get("run") if isinstance(result.get("run"), dict) else {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", status.title())
    c2.metric("Score", metrics.get("match_score", "n/a"))
    c3.metric("Sample Size", metrics.get("sample_size", len(traces) or "n/a"))
    c4.metric("Latency", f"{metrics.get('latency_ms')} ms" if metrics.get("latency_ms") is not None else "n/a")

    if run_meta:
        with st.expander("Sandbox run details", expanded=True):
            render_cloud_run_console_links(run_meta)
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Mode", run_meta.get("execution_mode", result.get("execution_mode", "unknown")))
            r2.metric("Stage", run_meta.get("stage", "complete"))
            r3.metric("Run ID", run_meta.get("run_id", "n/a"))
            r4.metric("Execution", run_meta.get("execution_id", "pending"))
            details = {
                "project_id": run_meta.get("project_id"),
                "gcp_project": run_meta.get("gcp_project"),
                "region": run_meta.get("region"),
                "job": run_meta.get("job"),
                "source_bundle_gcs_uri": run_meta.get("source_bundle_gcs_uri"),
            }
            detail_rows = [{ "field": k, "value": v } for k, v in details.items() if v]
            if detail_rows:
                display_table(pd.DataFrame(detail_rows), height=190)

    if result.get("error_log"):
        st.error(result.get("error_log"))

    if metrics:
        before = metrics.get("sandbox_baseline_score")
        after = metrics.get("match_score")
        if before is not None and after is not None:
            _before_f = round(float(before), 2)
            _after_f  = round(float(after), 2)
            _delta    = round(_after_f - _before_f, 2)
            _verdict  = "Same or better ✓" if _delta >= 0 else "Degraded ✗"
            st.markdown("**Sandbox comparison**")
            _sc1, _sc2, _sc3 = st.columns(3)
            _sc1.metric("Baseline (random)", _before_f)
            _sc2.metric("Optimized score",   _after_f, delta=_delta)
            _sc3.metric("Verdict", _verdict)

    if traces:
        st.markdown("**Simulation trace sample**")
        trace_df = pd.DataFrame(traces[:20])
        display_table(trace_df, height=260)
    else:
        st.caption("No trace rows were returned for this sandbox run.")


def sandbox_health_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {
            "label": "Not tested",
            "tone": "idle",
            "summary": "No sandbox run has been executed for this flow in this session.",
        }
    status = str(result.get("status") or "unknown")
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    traces = result.get("traces") if isinstance(result.get("traces"), list) else []
    if status == "success":
        score = metrics.get("match_score")
        baseline = metrics.get("sandbox_baseline_score")
        delta = None
        if score is not None and baseline is not None:
            delta = round(float(score) - float(baseline), 2)
        return {
            "label": "Working",
            "tone": "good" if delta is None or delta >= 0 else "warn",
            "summary": (
                f"Sandbox completed with score {score}, baseline {baseline}, "
                f"{len(traces)} trace row(s)."
            ),
            "delta": delta,
        }
    return {
        "label": "Needs attention",
        "tone": "bad",
        "summary": result.get("error_log") or "Sandbox failed before producing metrics.",
    }


def render_sandbox_run_monitor(
    *,
    flow_id: str,
    flow_name: str,
    result: dict[str, Any] | None,
    running: bool = False,
) -> None:
    """Compact operational monitor for the approved-flow sandbox path."""
    summary = sandbox_health_summary(result)
    tone = summary.get("tone")
    run_meta = result.get("run") if isinstance(result, dict) and isinstance(result.get("run"), dict) else {}

    with st.container(border=True):
        top_cols = st.columns([1.2, 1, 1, 1])
        top_cols[0].metric("Sandbox", "Running" if running else summary["label"])
        top_cols[1].metric("Mode", run_meta.get("execution_mode", "Cloud Run"))
        top_cols[2].metric("Stage", run_meta.get("stage", "waiting" if not result else "complete"))
        top_cols[3].metric("Execution", run_meta.get("execution_id", "pending"))
        render_cloud_run_console_links(run_meta)

    if running:
        st.info(f"Running Cloud Run sandbox for **{flow_name}**. This can take up to a few minutes.")
    elif tone == "good":
        st.success(summary["summary"])
    elif tone == "warn":
        st.warning(summary["summary"])
    elif tone == "bad":
        st.error(summary["summary"])
    else:
        st.caption(summary["summary"])

    steps = [
        ("Prepare", "Flow YAML loaded and capability token will be minted."),
        ("Bundle", "Project source bundle uploaded to GCS when configured."),
        ("Cloud Run", "Cloud Run Job executes sandbox-system/sandbox_task.py."),
        ("Logs", "Cloud Logging is scanned for DATA_STREAM markers."),
        ("Score", "Trace rows are converted into score, baseline, and verdict."),
    ]
    completed = 0
    if result:
        completed = 5 if result.get("status") == "success" else 3
        stage = (result.get("run") or {}).get("stage")
        if stage == "source_bundle_upload":
            completed = 1
        elif stage == "cloud_run_execution":
            completed = 2
        elif stage == "cloud_logging_parse":
            completed = 3
    if running:
        completed = max(completed, 2)

    html = ["<div style='display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:10px 0 14px;'>"]
    for index, (label, desc) in enumerate(steps, start=1):
        active = running and index == min(completed + 1, 5)
        done = index <= completed and not active
        color = "#3f6f5b" if done else "#9a5b13" if active else "#9c927f"
        bg = "rgba(22,116,71,.10)" if done else "rgba(165,91,25,.12)" if active else "rgba(156,146,127,.10)"
        border = "rgba(22,116,71,.35)" if done else "rgba(165,91,25,.45)" if active else "rgba(156,146,127,.25)"
        state = "done" if done else "now" if active else "waiting"
        html.append(
            f"<div style='border:1px solid {border};background:{bg};border-radius:8px;padding:9px 10px;min-height:82px;'>"
            f"<div style='font-size:11px;text-transform:uppercase;color:{color};font-weight:750;'>{escape(state)}</div>"
            f"<div style='font-size:13px;color:#20181d;font-weight:700;margin-top:3px;'>{escape(label)}</div>"
            f"<div style='font-size:11px;color:#6f626a;line-height:1.3;margin-top:4px;'>{escape(desc)}</div>"
            "</div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)

    if result:
        render_sandbox_review(result)


def render_cloud_run_console_links(run_meta: dict[str, Any] | None) -> None:
    execution_url = cloud_run_execution_url(run_meta)
    logs_url = cloud_run_logs_url(run_meta)
    job_url = cloud_run_job_url(run_meta)
    links = [
        ("Open Cloud Run execution", execution_url),
        ("Open Cloud Run logs", logs_url),
        ("Open Cloud Run job", job_url),
    ]
    available = [(label, url) for label, url in links if url]
    if not available:
        st.warning(
            "No Cloud Run URL is available yet. Set GOOGLE_CLOUD_PROJECT, "
            "SANDBOX_GCP_REGION, and SANDBOX_JOB_NAME to enable console links."
        )
        return
    cols = st.columns(len(available))
    for col, (label, url) in zip(cols, available):
        col.link_button(label, url, use_container_width=True)


def cloud_run_execution_url(run_meta: dict[str, Any] | None) -> str | None:
    """Return a direct Cloud Run execution URL when result metadata includes one."""
    return build_cloud_run_execution_url(run_meta)


def cloud_run_logs_url(run_meta: dict[str, Any] | None) -> str | None:
    """Return a Logs Explorer URL scoped to a Cloud Run execution."""
    return build_cloud_run_logs_url(run_meta)


def summarize_agent_failure(output: str, exit_code: int | None = None) -> str:
    text = output or ""
    if "RESOURCE_EXHAUSTED" in text or "monthly spending cap" in text:
        return (
            "The agent stopped in the planner because the Gemini API returned "
            "`429 RESOURCE_EXHAUSTED`: the Google AI Studio project has exceeded "
            "its monthly spending cap. No proposal was created and no code was changed."
        )
    if "Neo4j connectivity check failed" in text or "Unable to retrieve routing information" in text:
        return (
            "The agent could not reach Neo4j, so it could not load the selected "
            "BusinessFlow graph evidence. No proposal was created."
        )
    if "Agent run timed out" in text:
        return "The agent run timed out before reaching a proposal. No code was changed."
    if exit_code and exit_code != 0:
        return f"The agent process exited with code {exit_code} before creating a proposal."
    return "The critic/evaluator did not find a grounded improvement for this flow."


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


def business_flow_sentence(row: pd.Series, limit: int = 8) -> str:
    steps = row.get("steps") or []
    if not isinstance(steps, list):
        return "No ordered steps detected yet"
    labels = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("step_type") or step.get("primitive_type") or "Step"
        primitive = step.get("primitive") or step.get("step") or "unknown"
        labels.append(f"{step_type}: {primitive}")
    if not labels:
        return "No ordered steps detected yet"
    shown = labels[:limit]
    suffix = f" -> +{len(labels) - limit} more" if len(labels) > limit else ""
    return " -> ".join(shown) + suffix


def transaction_journey_kind(row: pd.Series) -> tuple[str, str]:
    """Infer the user/business transaction this static flow most likely represents."""
    text_parts = [
        row.get("business_flow"),
        row.get("entrypoint"),
        row.get("flow_type"),
        row.get("evidence_summary"),
    ]
    for step in row.get("steps") or []:
        if isinstance(step, dict):
            text_parts.extend([step.get("step"), step.get("primitive"), step.get("evidence")])
    text = " ".join(str(part or "") for part in text_parts).lower()
    patterns = [
        (("course", "lesson", "learn", "quiz", "assignment", "enroll", "student", "education"), "learning journey", "education"),
        (("order", "cart", "checkout", "payment", "invoice", "refund", "ship"), "commerce transaction", "commerce"),
        (("campaign", "donate", "donation", "fund", "pledge", "donor"), "funding transaction", "funding"),
        (("login", "signin", "signup", "register", "auth", "profile", "account"), "account journey", "identity"),
        (("match", "recommend", "score", "rank", "search", "filter"), "matching journey", "recommendation"),
        (("message", "chat", "reply", "notification", "send"), "communication journey", "communication"),
        (("upload", "download", "file", "document", "media"), "content workflow", "content"),
    ]
    for keywords, label, domain in patterns:
        if any(keyword in text for keyword in keywords):
            return label, domain
    return "primary transaction journey", "general"


def business_flow_description(row: pd.Series) -> str:
    """Return a short operator-facing explanation of what a static flow means."""
    steps = row.get("steps") or []
    step_count = len(steps) if isinstance(steps, list) else 0
    entrypoint = row.get("entrypoint") or "the detected entry point"
    stores = row.get("datastores") or []
    integrations = row.get("integrations") or []
    risks = row.get("risks") or []
    journey_label, domain = transaction_journey_kind(row)

    clauses = [
        f"This looks like a {journey_label}, not necessarily an order flow. "
        f"It starts from `{entrypoint}` and contains {step_count} static step(s)."
    ]
    if domain != "general":
        clauses.append(f"The label comes from domain hints in routes, functions, files, and step evidence.")
    if stores:
        clauses.append(f"It touches storage: {compact_list(stores, 4)}.")
    if integrations:
        clauses.append(f"It calls external integration(s): {compact_list(integrations, 3)}.")
    if risks:
        clauses.append(f"Static analysis flagged review items: {compact_list(risks, 3)}.")
    if not stores and not integrations and not risks:
        clauses.append("No storage, integration, or explicit risk node is attached yet.")
    return " ".join(clauses)


def render_business_flow_chain(row: pd.Series, limit: int = 12) -> None:
    """Render ordered BusinessFlow steps as a readable timeline."""
    steps = row.get("steps") or []
    if not isinstance(steps, list) or not steps:
        st.caption("No ordered steps detected yet.")
        return

    cards = []
    for index, step in enumerate(steps[:limit], 1):
        if not isinstance(step, dict):
            continue
        step_type = escape(str(step.get("step_type") or step.get("primitive_type") or "Step"))
        primitive = escape(str(step.get("primitive") or step.get("step") or "unknown"))
        evidence = escape(str(step.get("evidence") or ""))
        evidence_html = (
            f"<div style='font-size:11px;color:#6f626a;line-height:1.3;margin-top:4px;'>{evidence[:130]}</div>"
            if evidence else ""
        )
        cards.append(
            f"""
            <div style="display:flex;gap:10px;align-items:flex-start;margin:0 0 10px;">
              <div style="width:26px;height:26px;border-radius:50%;background:#fff3f8;color:#9d174d;border:1px solid #eadde4;
                          display:flex;align-items:center;justify-content:center;font-size:12px;
                          font-weight:700;flex:0 0 26px;">{index}</div>
              <div style="border:1px solid #eadde4;background:#ffffff;border-radius:8px;
                          padding:8px 10px;flex:1;min-width:0;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:.04em;
                            color:#9d174d;font-weight:700;">{step_type}</div>
                <div style="font-size:13px;color:#20181d;font-weight:650;word-break:break-word;">{primitive}</div>
                {evidence_html}
              </div>
            </div>
            """
        )

    more = ""
    if len(steps) > limit:
        more = f"<div style='color:#6f626a;font-size:12px;margin-left:36px;'>+{len(steps) - limit} more step(s)</div>"
    st.markdown("".join(cards) + more, unsafe_allow_html=True)


def cloud_run_job_url(run_meta: dict[str, Any] | None = None) -> str | None:
    return build_cloud_run_job_url(run_meta)


def graph_legend_html() -> str:
    items = [
        ("Project", "#eaf4ef", "#3f6f5b"),
        ("Repository", "#edf5f7", "#4d7882"),
        ("File", "#f6f1eb", "#756b70"),
        ("Route", "#eef3fb", "#4f6f8f"),
        ("Business Flow", "#fff1df", "#9a5b13"),
        ("Flow Step", "#fff6d9", "#9a5b13"),
        ("Workflow", "#fff1df", "#9a5b13"),
        ("Function / Skill", "#eef3fb", "#4f6f8f"),
        ("Storage", "#f3effb", "#7660a8"),
        ("Risk", "#fde8ee", "#b4234a"),
        ("Company", "#eaf4ef", "#3f6f5b"),
        ("Mentor", "#f3effb", "#7a4f93"),
        ("Programme", "#fff2cb", "#8a6f2b"),
        ("Flow", "#fff6d9", "#9a5b13"),
        ("Approved sandbox optimization", "#f9dce8", "#9d174d"),
        ("Connector", "#fff0e8", "#9f5b39"),
        ("Server", "#f4f0f2", "#756b70"),
        ("Proposed", "#fff9c2", "#bf8b16"),
        ("Agent active", "#f9dce8", "#9d174d"),
        ("Agent", "#f9dce8", "#9d174d"),
        ("Agent Tool", "#eef3fb", "#4f6f8f"),
        ("Graph Store", "#f3effb", "#7660a8"),
        ("Sandbox Runtime", "#eaf4ef", "#3f6f5b"),
        ("Approval", "#fff3f8", "#9d174d"),
        ("Registry", "#fff6d9", "#9a5b13"),
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
    scope: str = "Full Project Graph",
) -> str:
    """Interactive graph with search, click details, and active-agent highlighting."""

    groups = {
        "Company": {"color": {"background": "#eaf4ef", "border": "#3f6f5b"}},
        "Mentor": {"color": {"background": "#f3effb", "border": "#7a4f93"}},
        "Flow": {"color": {"background": "#fff6d9", "border": "#9a5b13"}},
        "Skill": {"color": {"background": "#eef3fb", "border": "#4f6f8f"}},
        "Connector": {"color": {"background": "#fff0e8", "border": "#9f5b39"}},
        "Server": {"color": {"background": "#f4f0f2", "border": "#756b70"}},
        "ExecutionTrace": {"color": {"background": "#edf5f7", "border": "#4d7882"}},
        "Outcome": {"color": {"background": "#fde8ee", "border": "#b4234a"}},
        "Programme": {"color": {"background": "#fff2cb", "border": "#8a6f2b"}},
        "WebSite": {"color": {"background": "#eef3fb", "border": "#4f6f8f"}},
        "WebPage": {"color": {"background": "#f3f7e7", "border": "#788a3c"}},
        "WebEntity": {"color": {"background": "#fff0f6", "border": "#9d174d"}},
        "AppProfile": {"color": {"background": "#eaf4ef", "border": "#9d174d"}},
        "Pipeline": {"color": {"background": "#fff1df", "border": "#9a5b13"}},
        "SkillProposal": {"color": {"background": "#f3effb", "border": "#7660a8"}},
        "SchemaChangeProposal": {"color": {"background": "#fff2cb", "border": "#8a6f2b"}},
        "Project": {"color": {"background": "#eaf4ef", "border": "#3f6f5b"}},
        "Repository": {"color": {"background": "#edf5f7", "border": "#4d7882"}},
        "File": {"color": {"background": "#f6f1eb", "border": "#756b70"}},
        "Route": {"color": {"background": "#eef3fb", "border": "#4f6f8f"}},
        "Service": {"color": {"background": "#fff6d9", "border": "#9a5b13"}},
        "Function": {"color": {"background": "#eef3fb", "border": "#4f6f8f"}},
        "DatabaseModel": {"color": {"background": "#f3effb", "border": "#7660a8"}},
        "DatabaseTable": {"color": {"background": "#f3effb", "border": "#7660a8"}},
        "DataStore": {"color": {"background": "#eaf4ef", "border": "#3f6f5b"}},
        "Entity": {"color": {"background": "#fff0f6", "border": "#9d174d"}},
        "Workflow": {"color": {"background": "#fff1df", "border": "#9a5b13"}},
        "BusinessFlow": {"color": {"background": "#fff1df", "border": "#9a5b13"}},
        "FlowStep": {"color": {"background": "#fff6d9", "border": "#9a5b13"}},
        "Integration": {"color": {"background": "#fff0e8", "border": "#9f5b39"}},
        "Artifact": {"color": {"background": "#f3f7e7", "border": "#788a3c"}},
        "Risk": {"color": {"background": "#fde8ee", "border": "#b4234a"}},
        "Agent": {"color": {"background": "#f9dce8", "border": "#9d174d"}},
        "AgentTool": {"color": {"background": "#eef3fb", "border": "#4f6f8f"}},
        "GraphStore": {"color": {"background": "#f3effb", "border": "#7660a8"}},
        "Runtime": {"color": {"background": "#edf5f7", "border": "#4d7882"}},
        "SandboxRuntime": {"color": {"background": "#eaf4ef", "border": "#3f6f5b"}},
        "Approval": {"color": {"background": "#fff3f8", "border": "#9d174d"}},
        "Registry": {"color": {"background": "#fff6d9", "border": "#9a5b13"}},
        "ArchitectureProposal": {"color": {"background": "#fff0f6", "border": "#9d174d"}},
    }

    size_map = {
        "Project": 26,
        "Company": 24,
        "Mentor": 24,
        "Repository": 22,
        "Programme": 20,
        "Flow": 20,
        "Workflow": 22,
        "BusinessFlow": 24,
        "FlowStep": 16,
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
        "SchemaChangeProposal": 16,
        "Agent": 24,
        "AgentTool": 18,
        "GraphStore": 20,
        "Runtime": 18,
        "SandboxRuntime": 20,
        "Approval": 20,
        "Registry": 20,
        "ArchitectureProposal": 17,
    }

    # Light structured-diagram palette used only for ERD/architecture scopes.
    erd_colors: dict[str, dict[str, str]] = {
        "Project":       {"bg": "#eaf4ef", "border": "#3f6f5b"},
        "Repository":    {"bg": "#f4f0f2", "border": "#756b70"},
        "BusinessFlow":  {"bg": "#fff1df", "border": "#9a5b13"},
        "Workflow":      {"bg": "#fff1df", "border": "#9a5b13"},
        "Route":         {"bg": "#eef3fb", "border": "#4f6f8f"},
        "FlowStep":      {"bg": "#fff6d9", "border": "#9a5b13"},
        "Service":       {"bg": "#eef3fb", "border": "#4f6f8f"},
        "Function":      {"bg": "#eef3fb", "border": "#4f6f8f"},
        "File":          {"bg": "#ffffff", "border": "#d9c4cf"},
        "DataStore":     {"bg": "#eaf4ef", "border": "#3f6f5b"},
        "DatabaseModel": {"bg": "#f3effb", "border": "#7660a8"},
        "DatabaseTable": {"bg": "#f3effb", "border": "#7660a8"},
        "Integration":   {"bg": "#fff0e8", "border": "#9f5b39"},
        "Artifact":      {"bg": "#f3f7e7", "border": "#788a3c"},
        "Risk":          {"bg": "#fde8ee", "border": "#b4234a"},
        "Flow":          {"bg": "#f9dce8", "border": "#9d174d"},
        "Entity":        {"bg": "#fff0f6", "border": "#9d174d"},
        "Pipeline":      {"bg": "#fff2cb", "border": "#8a6f2b"},
        "Skill":         {"bg": "#eef3fb", "border": "#4f6f8f"},
        "Connector":     {"bg": "#fff0e8", "border": "#9f5b39"},
        "Server":        {"bg": "#f4f0f2", "border": "#756b70"},
        "Agent":         {"bg": "#f9dce8", "border": "#9d174d"},
        "AgentTool":     {"bg": "#eef3fb", "border": "#4f6f8f"},
        "GraphStore":    {"bg": "#f3effb", "border": "#7660a8"},
        "Runtime":       {"bg": "#edf5f7", "border": "#4d7882"},
        "SandboxRuntime":{"bg": "#eaf4ef", "border": "#3f6f5b"},
        "Approval":      {"bg": "#fff3f8", "border": "#9d174d"},
        "Registry":      {"bg": "#fff6d9", "border": "#9a5b13"},
        "ArchitectureProposal": {"bg": "#fff0f6", "border": "#9d174d"},
    }

    diagram_scopes = {"Workflow Pipeline", "Storage & Risk", "Agentic Layer Links"}
    is_structured_diagram = scope in diagram_scopes
    level_map = {
        "Workflow Pipeline": {
            "Project": 0,
            "BusinessFlow": 1,
            "Workflow": 1,
            "Route": 2,
            "FlowStep": 2,
            "Service": 3,
            "Function": 3,
            "File": 3,
            "DataStore": 4,
            "DatabaseModel": 4,
            "DatabaseTable": 4,
            "Integration": 4,
            "Artifact": 4,
            "Risk": 5,
            "Flow": 6,
        },
        "Storage & Risk": {
            "Project": 0,
            "Repository": 1,
            "File": 2,
            "DataStore": 3,
            "DatabaseModel": 3,
            "DatabaseTable": 3,
            "Integration": 3,
            "Risk": 4,
        },
        "Agentic Layer Links": {
            "Project": 0,
            "GraphStore": 1,
            "AgentTool": 2,
            "Agent": 3,
            "SandboxRuntime": 4,
            "Runtime": 4,
            "Approval": 5,
            "Registry": 6,
            "Flow": 7,
            "Skill": 7,
            "ArchitectureProposal": 7,
        },
    }.get(scope, {})
    shape_map = {
        "Project": "hexagon",
        "Repository": "box",
        "File": "box",
        "Route": "box",
        "Service": "box",
        "Function": "box",
        "Workflow": "box",
        "BusinessFlow": "box",
        "FlowStep": "box",
        "Flow": "box",
        "Skill": "box",
        "Connector": "box",
        "Integration": "box",
        "DataStore": "database",
        "DatabaseModel": "database",
        "DatabaseTable": "database",
        "Entity": "box",
        "Artifact": "box",
        "Risk": "diamond",
        "ExecutionTrace": "box",
        "Outcome": "diamond",
        "SkillProposal": "box",
        "SchemaChangeProposal": "box",
        "Server": "box",
        "Company": "box",
        "Mentor": "box",
        "Programme": "box",
        "WebSite": "box",
        "WebPage": "box",
        "WebEntity": "box",
        "AppProfile": "box",
        "Pipeline": "box",
        "Agent": "box",
        "AgentTool": "box",
        "GraphStore": "database",
        "Runtime": "box",
        "SandboxRuntime": "box",
        "Approval": "diamond",
        "Registry": "database",
        "ArchitectureProposal": "box",
    }

    def styled_edge(edge: dict[str, Any]) -> dict[str, Any]:
        rel = edge.get("label", "")
        next_edge = dict(edge)
        if rel == "APPROVED_SANDBOX_OPTIMIZATION":
            next_edge.update(
                {
                    "color": {"color": "#9d174d", "highlight": "#7f123f", "hover": "#7f123f"},
                    "width": 3,
                    "dashes": True,
                }
            )
        elif "RISK" in rel:
            next_edge.update(
                {
                    "color": {"color": "#b4234a", "highlight": "#8f1d3b", "hover": "#8f1d3b"},
                    "width": 2.2,
                }
            )
        elif "DATASTORE" in rel or "MODEL" in rel or "TABLE" in rel:
            next_edge.update(
                {
                    "color": {"color": "#7660a8", "highlight": "#5f4f88", "hover": "#5f4f88"},
                    "width": 1.8,
                }
            )
        elif "INTEGRATION" in rel or "CONNECTOR" in rel:
            next_edge.update(
                {
                    "color": {"color": "#9f5b39", "highlight": "#7d442b", "hover": "#7d442b"},
                    "width": 1.8,
                }
            )
        elif rel in {"HAS_BUSINESS_FLOW", "HAS_STEP", "USES_PRIMITIVE", "FILE_DEFINES_WORKFLOW"}:
            next_edge.update(
                {
                    "color": {"color": "#9a5b13", "highlight": "#74440f", "hover": "#74440f"},
                    "width": 1.9,
                }
            )
        elif rel.startswith("FILE_DEFINES") or rel.startswith("REPOSITORY_HAS"):
            next_edge.update(
                {
                    "color": {"color": "#a99aa3", "highlight": "#756b70", "hover": "#756b70"},
                    "width": 1.35,
                }
            )
        return next_edge

    badge_colors = {
        "Project": "#3f6f5b",
        "Repository": "#4d7882",
        "File": "#756b70",
        "Route": "#4f6f8f",
        "Service": "#9a5b13",
        "Workflow": "#9a5b13",
        "BusinessFlow": "#9a5b13",
        "FlowStep": "#9a5b13",
        "Function": "#475569",
        "Skill": "#4f6f8f",
        "DataStore": "#357960",
        "DatabaseModel": "#7660a8",
        "DatabaseTable": "#7660a8",
        "Entity": "#9d174d",
        "Integration": "#9f5b39",
        "Artifact": "#788a3c",
        "Risk": "#b4234a",
        "Company": "#357960",
        "Mentor": "#7a4f93",
        "Flow": "#9d174d",
        "Connector": "#9f5b39",
        "Server": "#756b70",
        "Programme": "#8a6f2b",
        "ExecutionTrace": "#467982",
        "Outcome": "#b4234a",
        "AppProfile": "#9d174d",
        "Pipeline": "#8a6f2b",
        "SkillProposal": "#7660a8",
        "SchemaChangeProposal": "#8a6f2b",
        "WebSite": "#4f6f8f",
        "WebPage": "#788a3c",
        "WebEntity": "#9d174d",
        "Agent": "#9d174d",
        "AgentTool": "#4f6f8f",
        "GraphStore": "#7660a8",
        "Runtime": "#4d7882",
        "SandboxRuntime": "#3f6f5b",
        "Approval": "#9d174d",
        "Registry": "#9a5b13",
        "ArchitectureProposal": "#9d174d",
    }

    active_ids = set(agent_active_ids or [])
    nodes = []
    node_details = {}
    structured_rows: dict[int, int] = defaultdict(int)
    structured_totals: dict[int, int] = defaultdict(int)
    if is_structured_diagram:
        for raw_node in payload["nodes"]:
            structured_totals[level_map.get(raw_node.get("group"), 3)] += 1

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
            "shape": shape_map.get(group, "dot") if is_structured_diagram else "dot",
            "size": size_map.get(group, 14) * (1.35 if is_active else 1),
        }
        if is_structured_diagram:
            level = level_map.get(group, 3)
            row_index = structured_rows[level]
            row_total = max(structured_totals[level], 1)
            structured_rows[level] += 1
            x_spacing = 255 if scope == "Storage & Risk" else 285
            y_spacing = 84 if row_total > 8 else 104
            node_data["x"] = level * x_spacing
            node_data["y"] = (row_index - (row_total - 1) / 2) * y_spacing
            node_data["fixed"] = {"x": False, "y": False}
            node_data["margin"] = {"top": 10, "right": 16, "bottom": 10, "left": 16}
            node_data["widthConstraint"] = {"minimum": 120, "maximum": 210}
            node_data["heightConstraint"] = {"minimum": 44}
            # ERD-style label: type tag on the first line, truncated name below
            node_data["label"] = f"«{group}»\n{label[:22]}"
            _ec = erd_colors.get(group, {"bg": "#ffffff", "border": "#d9c4cf"})
            node_data["color"] = {
                "background": _ec["bg"],
                "border": _ec["border"],
                "highlight": {"background": "#ffffff", "border": "#9d174d"},
                "hover":     {"background": "#ffffff", "border": "#9d174d"},
            }
            node_data["font"] = {
                "face": "Avenir Next, Helvetica Neue, Helvetica, sans-serif",
                "size": 11,
                "color": "#20181d",
                "strokeWidth": 0,
                "multi": False,
            }
            node_data["shadow"] = {
                "enabled": True,
                "color": "rgba(71,31,51,0.08)",
                "size": 10, "x": 0, "y": 0,
            }

        if is_active:
            node_data["color"] = {
                "background": "#f9dce8",
                "border": "#9d174d",
                "highlight": {"background": "#f4bdd2", "border": "#7f123f"},
            }
            node_data["shadow"] = {
                "enabled": True,
                "color": "rgba(157,23,77,0.28)",
                "size": 16,
                "x": 0,
                "y": 0,
            }
        elif status == "sandbox_approved":
            node_data["color"] = {
                "background": "#f9dce8",
                "border": "#9d174d",
                "highlight": {"background": "#f4bdd2", "border": "#7f123f"},
                "hover": {"background": "#f4bdd2", "border": "#7f123f"},
            }
            node_data["shadow"] = {
                "enabled": True,
                "color": "rgba(157,23,77,0.25)",
                "size": 14,
                "x": 0,
                "y": 0,
            }
        elif status in ("overloaded", "critical", "deprecated", "analysis_failed"):
            node_data["color"] = {
                "background": "#fde8ee",
                "border": "#b4234a",
                "highlight": {"background": "#fbd2dd", "border": "#8f1d3b"},
            }
        elif status == "proposed":
            node_data["color"] = {
                "background": "#fff9c2",
                "border": "#bf8b16",
                "highlight": {"background": "#fff9c2", "border": "#b8860b"},
            }

        nodes.append(node_data)

        details: dict[str, Any] = {"Type": group, "Name": label}
        if status:
            details["Status"] = status
        if node.get("sandbox_only"):
            details["Implementation"] = "Approved sandbox proposal - not applied to source code yet"
        if node.get("business_flow_id"):
            details["Optimizes BusinessFlow"] = node["business_flow_id"]
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
        if node.get("entrypoint"):
            details["Entrypoint"] = node["entrypoint"]
        if node.get("flow_type"):
            details["Flow Type"] = node["flow_type"]
        if node.get("label") and group == "SchemaChangeProposal":
            details["Proposed Label"] = node["label"]
        if node.get("reason"):
            details["Reason"] = node["reason"]
        if node.get("required_fields"):
            details["Required Fields"] = compact_list(node["required_fields"], 8)
        if node.get("optional_fields"):
            details["Optional Fields"] = compact_list(node["optional_fields"], 8)
        if node.get("evidence_summary"):
            details["Evidence"] = node["evidence_summary"]
        if node.get("source_paths"):
            details["Source Paths"] = compact_list(node["source_paths"], 5)
        if node.get("order") is not None:
            details["Step Order"] = node["order"]
        if node.get("step_type"):
            details["Step Type"] = node["step_type"]
        if node.get("primitive_label"):
            details["Primitive Label"] = node["primitive_label"]
        if node.get("primitive_id"):
            details["Primitive ID"] = node["primitive_id"]
        if node.get("evidence"):
            details["Step Evidence"] = node["evidence"]
        if node.get("confidence") is not None:
            details["Confidence"] = node["confidence"]
        if node.get("description"):
            details["Description"] = node["description"]
        if node.get("technical_description"):
            details["Technical"] = node["technical_description"]
        if node.get("stakeholder_description"):
            details["Stakeholder"] = node["stakeholder_description"]
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

    edges = [styled_edge(edge) for edge in payload["edges"]]
    physics_options: dict[str, Any] | bool
    layout_options: dict[str, Any]
    physics_options = {
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {
            "gravitationalConstant": -60,
            "centralGravity": 0.008,
            "springLength": 170,
            "springConstant": 0.05,
            "damping": 0.55,
        },
        "stabilization": {"iterations": 220, "updateInterval": 20},
        "adaptiveTimestep": True,
    }
    layout_options = {"improvedLayout": True}
    edge_smooth: dict[str, Any] | bool = {"type": "cubicBezier", "forceDirection": "none", "roundness": 0.45}
    if is_structured_diagram:
        physics_options = False
        layout_options = {"improvedLayout": False}
        edge_smooth: dict[str, Any] | bool = {"type": "curvedCW", "roundness": 0.1}
        x_spacing_global = 250 if scope == "Agentic Layer Links" else 255 if scope == "Storage & Risk" else 285
        if scope == "Workflow Pipeline":
            lane_defs = [
                {"level": 0, "label": "PROJECT",      "color": "rgba(63,185,80,.07)"},
                {"level": 1, "label": "FLOWS",         "color": "rgba(240,136,62,.07)"},
                {"level": 2, "label": "STEPS",         "color": "rgba(88,166,255,.07)"},
                {"level": 3, "label": "SERVICES",      "color": "rgba(88,166,255,.04)"},
                {"level": 4, "label": "STORAGE",       "color": "rgba(188,140,255,.07)"},
                {"level": 5, "label": "RISKS",         "color": "rgba(255,123,114,.08)"},
                {"level": 6, "label": "OPTIMIZATION",  "color": "rgba(240,136,62,.04)"},
            ]
        elif scope == "Storage & Risk":
            lane_defs = [
                {"level": 0, "label": "PROJECT",  "color": "rgba(63,185,80,.07)"},
                {"level": 1, "label": "REPOS",     "color": "rgba(139,148,158,.06)"},
                {"level": 2, "label": "FILES",     "color": "rgba(139,148,158,.04)"},
                {"level": 3, "label": "STORAGE",   "color": "rgba(188,140,255,.07)"},
                {"level": 4, "label": "RISKS",     "color": "rgba(255,123,114,.08)"},
            ]
        else:
            lane_defs = [
                {"level": 0, "label": "PROJECT", "color": "rgba(63,111,91,.07)"},
                {"level": 1, "label": "GRAPH", "color": "rgba(118,96,168,.07)"},
                {"level": 2, "label": "TOOLS", "color": "rgba(79,111,143,.06)"},
                {"level": 3, "label": "AGENTS", "color": "rgba(157,23,77,.06)"},
                {"level": 4, "label": "SANDBOX", "color": "rgba(63,111,91,.06)"},
                {"level": 5, "label": "APPROVAL", "color": "rgba(157,23,77,.07)"},
                {"level": 6, "label": "REGISTRY", "color": "rgba(154,91,19,.06)"},
                {"level": 7, "label": "PROJECT OBJECTS", "color": "rgba(217,196,207,.10)"},
            ]
        graph_caption = (
            "Agentic architecture map: project graph -> tools -> agents -> sandbox -> approvals -> registry"
            if scope == "Agentic Layer Links"
            else "Drag any node to reposition — snaps to grid, position saved per session"
        )
    else:
        graph_caption = "Exploratory force layout for the full connected project graph"
    focus_groups = (
        ("BusinessFlow", "Workflow", "Project")
        if scope == "Workflow Pipeline"
        else ("Project", "Repository", "BusinessFlow")
    )
    initial_focus_id = next(
        (
            node["id"]
            for preferred_group in focus_groups
            for node in nodes
            if node.get("group") == preferred_group
        ),
        nodes[0]["id"] if nodes else None,
    )
    initial_scale = 0.72 if scope == "Workflow Pipeline" else 0.78

    if scope in {"Workflow Pipeline", "Storage & Risk"}:
        if not nodes:
            return """
            <div style="height:220px;border:1px solid #eadde4;border-radius:8px;
                        display:flex;align-items:center;justify-content:center;
                        color:#6f626a;background:#ffffff;font-family:Avenir Next,Helvetica,sans-serif;">
              No graph nodes are available for this scope yet.
            </div>
            """

        node_by_id = {node["id"]: node for node in nodes}
        x_values = [float(node.get("x", 0)) for node in nodes]
        y_values = [float(node.get("y", 0)) for node in nodes]
        min_x, max_x = min(x_values), max(x_values)
        min_y, max_y = min(y_values), max(y_values)
        pad_x, pad_y = 170, 130
        box_w, box_h = 150, 58
        canvas_w = max(960, int(max_x - min_x + pad_x * 2 + box_w))
        canvas_h = max(560, int(max_y - min_y + pad_y * 2 + box_h))

        def _sx(value: Any) -> float:
            return float(value or 0) - min_x + pad_x

        def _sy(value: Any) -> float:
            return float(value or 0) - min_y + pad_y

        lane_html = ""
        lane_labels = []
        for lane in lane_defs:
            lx = lane["level"] * x_spacing_global - min_x + pad_x - x_spacing_global * 0.5
            lane_html += (
                f"<div style='position:absolute;left:{lx}px;top:0;width:{x_spacing_global}px;"
                f"height:{canvas_h}px;background:{lane['color']};border-left:1px dashed #eadde4;'></div>"
            )
            lane_labels.append(
                f"<div style='position:absolute;left:{lx}px;top:14px;width:{x_spacing_global}px;"
                "text-align:center;font-size:10px;font-weight:800;color:#6f626a;"
                f"letter-spacing:.08em;'>{escape(lane['label'])}</div>"
            )

        edge_lines = []
        for edge in edges:
            src = node_by_id.get(edge.get("from"))
            dst = node_by_id.get(edge.get("to"))
            if not src or not dst:
                continue
            rel = str(edge.get("label") or "")
            color = "#d9c4cf"
            width = 1.4
            dash = ""
            if "RISK" in rel:
                color = "#b4234a"
                width = 2
            elif "DATASTORE" in rel or "TABLE" in rel or "MODEL" in rel:
                color = "#7660a8"
                width = 1.8
            elif rel == "APPROVED_SANDBOX_OPTIMIZATION":
                color = "#9d174d"
                width = 2.4
                dash = "stroke-dasharray='6 5'"
            x1 = _sx(src.get("x")) + box_w
            y1 = _sy(src.get("y")) + box_h / 2
            x2 = _sx(dst.get("x"))
            y2 = _sy(dst.get("y")) + box_h / 2
            mid = (x1 + x2) / 2
            edge_lines.append(
                f"<path d='M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}' "
                f"fill='none' stroke='{color}' stroke-width='{width}' {dash} marker-end='url(#arrow)' />"
            )

        node_cards = []
        for node in nodes:
            group = node.get("group", "Node")
            clean_label = str(node.get("label") or node.get("id", "")).replace("«", "").replace("»", " ")
            display_label = clean_label.replace("\n", " ").strip()
            color = node.get("color") if isinstance(node.get("color"), dict) else {}
            bg = color.get("background") or groups.get(group, {}).get("color", {}).get("background", "#ffffff")
            border = color.get("border") or groups.get(group, {}).get("color", {}).get("border", "#d9c4cf")
            details_json = json.dumps(node_details.get(node["id"], {}))
            node_cards.append(
                f"<button class='node-card' onclick='showDetails({json.dumps(node['id'])}, {details_json})' "
                f"style='left:{_sx(node.get('x'))}px;top:{_sy(node.get('y'))}px;"
                f"background:{bg};border-color:{border};'>"
                f"<span class='node-type'>{escape(group)}</span>"
                f"<span class='node-name'>{escape(display_label[:42])}</span>"
                "</button>"
            )

        return f"""
        <style>
          .staticGraphShell {{ height:720px; display:flex; gap:12px; font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif; }}
          .staticGraphViewport {{ flex:1; min-width:0; overflow:auto; border:1px solid #eadde4; border-radius:8px; background:#fff; position:relative; }}
          .staticGraphCanvas {{ position:relative; width:{canvas_w}px; height:{canvas_h}px; }}
          .node-card {{ position:absolute; width:{box_w}px; min-height:{box_h}px; border:1.5px solid; border-radius:7px;
            padding:8px 10px; text-align:left; cursor:pointer; box-shadow:0 8px 18px rgba(71,31,51,.08); color:#20181d; }}
          .node-card:hover {{ outline:2px solid rgba(157,23,77,.22); }}
          .node-type {{ display:block; font-size:9px; font-weight:800; color:#6f626a; text-transform:uppercase; letter-spacing:.06em; }}
          .node-name {{ display:block; font-size:12px; font-weight:750; line-height:1.2; margin-top:3px; overflow-wrap:anywhere; }}
          .staticDetail {{ width:310px; flex:0 0 310px; border:1px solid #eadde4; border-radius:8px; background:#fff7fa; overflow:auto; }}
          .detailRow {{ padding:8px 0; border-bottom:1px solid #eadde4; }}
          .detailKey {{ font-size:9px; font-weight:800; color:#6f626a; text-transform:uppercase; letter-spacing:.06em; }}
          .detailVal {{ font-size:12px; color:#20181d; font-weight:650; overflow-wrap:anywhere; margin-top:3px; }}
        </style>
        <div class="staticGraphShell">
          <div class="staticGraphViewport">
            <div class="staticGraphCanvas">
              {lane_html}
              {''.join(lane_labels)}
              <svg width="{canvas_w}" height="{canvas_h}" style="position:absolute;left:0;top:0;overflow:visible;">
                <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3.5" orient="auto">
                  <polygon points="0 0, 8 3.5, 0 7" fill="#9d8c96"></polygon>
                </marker></defs>
                {''.join(edge_lines)}
              </svg>
              {''.join(node_cards)}
              <div style="position:absolute;left:12px;bottom:12px;background:rgba(255,255,255,.95);border:1px solid #eadde4;
                border-radius:6px;padding:7px 10px;color:#6f626a;font-size:11px;font-weight:700;">{escape(graph_caption)}</div>
            </div>
          </div>
          <div id="staticDetail" class="staticDetail">
            <div style="padding:16px;border-bottom:1px solid #eadde4;">
              <div style="font-size:10px;font-weight:800;color:#6f626a;text-transform:uppercase;letter-spacing:.08em;">Node Properties</div>
              <div style="font-size:14px;color:#20181d;font-weight:750;margin-top:5px;">Nothing selected</div>
            </div>
            <div style="padding:28px 16px;color:#6f626a;font-size:12px;line-height:1.5;text-align:center;">
              Click a node in the graph to inspect its properties.
            </div>
          </div>
        </div>
        <script>
          function esc(v) {{
            return String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
          }}
          function showDetails(id, details) {{
            const rows = Object.entries(details || {{}})
              .filter(([k]) => !['Type','Name'].includes(k))
              .map(([k,v]) => `<div class="detailRow"><div class="detailKey">${{esc(k)}}</div><div class="detailVal">${{esc(v)}}</div></div>`)
              .join('');
            document.getElementById('staticDetail').innerHTML = `
              <div style="padding:16px;border-bottom:1px solid #eadde4;background:#fff;">
                <div style="font-size:10px;font-weight:800;color:#6f626a;text-transform:uppercase;letter-spacing:.08em;">${{esc(details.Type || 'Node')}}</div>
                <div style="font-size:14px;color:#20181d;font-weight:800;margin-top:5px;overflow-wrap:anywhere;">${{esc(details.Name || id)}}</div>
              </div>
              <div style="padding:10px 16px;">${{rows || '<div style="color:#6f626a;font-size:12px;padding:12px 0;">No extra properties.</div>'}}</div>
              <div style="margin:0 16px 14px;padding-top:8px;border-top:1px solid #eadde4;color:#9d8c96;font-size:10px;text-align:center;">id: ...${{String(id).slice(-10)}}</div>`;
          }}
        </script>
        """

    # ── Structured ERD view: light structured canvas ──────────────────────────
    if is_structured_diagram:
        html = f"""
    <style>
      #erdWrap * {{ box-sizing: border-box; }}
      .erd-btn {{
        padding:5px 11px; border-radius:3px; border:1px solid #d9c4cf;
        background:#fff7fa; color:#6f626a; cursor:pointer; font-size:11px;
        font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif; font-weight:700;
        letter-spacing:.04em; transition:border-color .15s,color .15s;
      }}
      .erd-btn:hover {{ border-color:#9d174d; color:#20181d; }}
      .erd-input {{
        padding:5px 10px; border-radius:3px; border:1px solid #d9c4cf;
        background:#ffffff; color:#20181d; font-size:11px; width:170px; outline:none;
        font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;
      }}
      .erd-input:focus {{ border-color:#9d174d; }}
      #savedBadge {{
        position:absolute; top:10px; right:10px; z-index:20;
        padding:4px 10px; border-radius:3px; font-size:10px; font-weight:700;
        background:rgba(63,185,80,.15); border:1px solid #3f6f5b; color:#3f6f5b;
        font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;
        opacity:0; transition:opacity .3s; pointer-events:none;
      }}
    </style>
    <div id="erdWrap" style="display:flex;gap:10px;height:740px;
         background:#ffffff;padding:10px;border-radius:6px;">

      <div style="flex:1;position:relative;">
        <div style="position:absolute;top:10px;left:10px;z-index:20;
                    display:flex;gap:6px;align-items:center;">
          <input id="searchBox" class="erd-input"
            placeholder="Search node…" onkeyup="searchNode()">
          <button class="erd-btn" onclick="resetView()">Reset</button>
          <button class="erd-btn" onclick="clearLayout()"
            title="Remove all saved positions and refit">Clear Layout</button>
        </div>

        <div id="savedBadge">✓ Position saved</div>

        <div style="position:absolute;bottom:10px;left:10px;z-index:10;
                    background:rgba(255,255,255,.94);border:1px solid #eadde4;
                    border-radius:3px;padding:5px 10px;color:#6f626a;
                    font-size:10px;font-weight:700;max-width:480px;
                    font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;letter-spacing:.04em;">
          {graph_caption}
        </div>

        <div id="network"
          style="height:100%;border:1px solid #eadde4;border-radius:4px;
                 background:#ffffff;
                 box-shadow:0 0 0 1px rgba(240,246,252,.04),inset 0 1px 0 rgba(240,246,252,.02);">
        </div>
      </div>

      <div id="detailPanel"
        style="width:300px;background:#fff7fa;border:1px solid #eadde4;
               border-radius:4px;overflow:hidden;
               display:flex;flex-direction:column;flex-shrink:0;">
        <div style="padding:14px 16px 10px;border-bottom:1px solid #eadde4;">
          <div style="font-size:9px;text-transform:uppercase;letter-spacing:.1em;
               color:#6f626a;font-weight:700;
               font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">NODE PROPERTIES</div>
          <div style="font-size:14px;color:#6f626a;font-weight:700;margin-top:6px;
               font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">Nothing selected</div>
        </div>
        <div style="font-size:11px;color:#6f626a;text-align:center;padding:28px 16px;
                    font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;line-height:1.6;">
          Click any node to inspect<br>its graph properties.
        </div>
      </div>
    </div>

    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <script>
      const nodesData  = new vis.DataSet({json.dumps(nodes)});
      const edgesData  = new vis.DataSet({json.dumps(edges)});
      const groups     = {json.dumps(groups)};
      const details    = {json.dumps(node_details)};
      const badgeColor = {json.dumps(badge_colors)};
      const activeIds  = {json.dumps(list(active_ids))};
      const container  = document.getElementById("network");
      const xSpacing   = {x_spacing_global};
      const laneDefs   = {json.dumps(lane_defs)};
      const GRID       = 60;
      const SKEY       = "ecl_pos_" + {json.dumps(scope)}.replace(/ /g,"_").replace(/&/g,"and");

      const options = {{
        groups,
        interaction: {{ hover:true, navigationButtons:false, keyboard:true, tooltipDelay:60 }},
        physics: false,
        nodes: {{
          font: {{
            face: "Avenir Next, Helvetica Neue, Helvetica, sans-serif",
            size: 12, color: "#20181d", strokeWidth: 0
          }},
          borderWidth: 1.5, borderWidthSelected: 2.5,
          shadow: {{ enabled:true, size:10, x:0, y:0, color:"rgba(71,31,51,0.08)" }}
        }},
        edges: {{
          arrows: {{ to: {{ enabled:true, scaleFactor:0.6, type:"arrow" }} }},
          color: {{ color:"#d9c4cf", highlight:"#9d174d", hover:"#7f123f" }},
          font: {{ size:9, align:"middle", color:"#7d7078", strokeWidth:0 }},
          smooth: {{ type:"curvedCW", roundness:0.1 }},
          width: 1.5, selectionWidth: 2.5
        }},
        layout: {{ improvedLayout: false }}
      }};

      const network = new vis.Network(container, {{ nodes:nodesData, edges:edgesData }}, options);

      // Restore saved positions from localStorage
      const savedPos = JSON.parse(localStorage.getItem(SKEY) || "{{}}");
      if (Object.keys(savedPos).length) {{
        nodesData.update(
          Object.entries(savedPos).map(([id, p]) => ({{ id, x:p.x, y:p.y, fixed:{{x:true,y:true}} }}))
        );
      }}

      // Draw lane column backgrounds in graph-space (pan + zoom with the canvas)
      network.on("beforeDrawing", function(ctx) {{
        const allPos = network.getPositions();
        const ys = Object.values(allPos).map(p => p.y);
        if (!ys.length) return;
        const yMin = Math.min(...ys) - 120;
        const yMax = Math.max(...ys) + 90;
        for (const lane of laneDefs) {{
          const cx = lane.level * xSpacing;
          const x1 = cx - xSpacing * 0.5;
          const x2 = cx + xSpacing * 0.5;
          // strip fill
          ctx.fillStyle = lane.color;
          ctx.fillRect(x1, yMin, x2 - x1, yMax - yMin);
          // left separator
          ctx.save();
          ctx.strokeStyle = "rgba(217,196,207,.8)";
          ctx.lineWidth = 1;
          ctx.setLineDash([3,3]);
          ctx.beginPath(); ctx.moveTo(x1, yMin); ctx.lineTo(x1, yMax); ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
          // column header label
          ctx.save();
          ctx.fillStyle = "rgba(111,98,106,.85)";
          ctx.font = "700 8px Avenir Next, Helvetica Neue, Helvetica, sans-serif";
          ctx.textAlign = "center";
          ctx.fillText(lane.label, cx, yMin + 18);
          ctx.restore();
        }}
      }});

      // Initial fit after first draw
      network.once("afterDrawing", function() {{
        window.setTimeout(() => {{
          network.fit({{ animation: {{ duration:700, easingFunction:"easeInOutQuad" }} }});
        }}, 60);
      }});

      // Drag end: snap to 60-px grid and persist position to localStorage
      network.on("dragEnd", function(params) {{
        if (!params.nodes.length) return;
        const id  = params.nodes[0];
        const raw = network.getPositions([id])[id];
        const snapped = {{
          x: Math.round(raw.x / GRID) * GRID,
          y: Math.round(raw.y / GRID) * GRID,
        }};
        nodesData.update([{{ id, x:snapped.x, y:snapped.y, fixed:{{x:true,y:true}} }}]);
        const saved = JSON.parse(localStorage.getItem(SKEY) || "{{}}");
        saved[id] = snapped;
        localStorage.setItem(SKEY, JSON.stringify(saved));
        flashSaved();
      }});

      function flashSaved() {{
        const el = document.getElementById("savedBadge");
        if (!el) return;
        el.style.opacity = "1";
        clearTimeout(el._fadeTimer);
        el._fadeTimer = setTimeout(() => {{ el.style.opacity = "0"; }}, 1600);
      }}

      function clearLayout() {{
        localStorage.removeItem(SKEY);
        nodesData.update(nodesData.get().map(n => ({{ id:n.id, fixed:{{x:false,y:false}} }})));
        network.fit({{ animation:{{ duration:500 }} }});
      }}

      // Detail panel — light details theme
      const BLANK_PANEL = `
        <div style="padding:14px 16px 10px;border-bottom:1px solid #eadde4;">
          <div style="font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#6f626a;
               font-weight:700;font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">NODE PROPERTIES</div>
          <div style="font-size:14px;color:#6f626a;font-weight:700;margin-top:6px;
               font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">Nothing selected</div>
        </div>
        <div style="font-size:11px;color:#6f626a;text-align:center;padding:28px 16px;
             font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;line-height:1.6;">
          Click any node to inspect<br>its graph properties.
        </div>`;

      function esc(v) {{
        return String(v ?? "")
          .replaceAll("&","&amp;").replaceAll("<","&lt;")
          .replaceAll(">","&gt;").replaceAll('"',"&quot;");
      }}
      function fmtVal(v) {{
        const t = esc(v); return t.length > 200 ? t.slice(0,200)+"…" : t;
      }}

      function renderDetails(nodeId) {{
        const panel = document.getElementById("detailPanel");
        const info  = details[nodeId];
        if (!info) return;
        const type = info["Type"]  || "Node";
        const name = info["Name"]  || nodeId;
        const col  = badgeColor[type] || "#9d174d";
        const isAct = activeIds.includes(nodeId);
        const PRI = new Set(["Status","Agent Status","Entrypoint","Route","Method",
          "Storage Type","Risk Type","Severity","Confidence","Avg Score",
          "Source Path","Evidence","Description","Technical"]);
        let pRows = "", sRows = "";
        for (const [k, v] of Object.entries(info)) {{
          if (k === "Type" || k === "Name") continue;
          const isAg = k === "Agent Status";
          const row = `<div style="padding:8px 0;border-bottom:1px solid #eadde4;">
            <div style="color:#7d7078;font-size:9px;text-transform:uppercase;
                 letter-spacing:.07em;font-weight:700;margin-bottom:3px;
                 font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">${{esc(k)}}</div>
            <div style="color:${{isAg?"#9d174d":"#20181d"}};font-size:11px;
                 font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;
                 line-height:1.45;word-break:break-word;">${{fmtVal(v)}}</div>
          </div>`;
          if (PRI.has(k) || isAg) pRows += row; else sRows += row;
        }}
        const agBanner = isAct
          ? `<div style="background:rgba(157,23,77,.08);border:1px solid #9d174d;
               border-radius:3px;padding:4px 9px;font-size:10px;color:#7f123f;margin-bottom:10px;
               font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">▶ AGENT ACTIVE</div>` : "";
        panel.innerHTML = `
          <div style="padding:14px 16px 12px;border-bottom:1px solid #eadde4;">
            ${{agBanner}}
            <div style="font-size:9px;text-transform:uppercase;letter-spacing:.1em;
                 color:#6f626a;font-weight:700;
                 font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">NODE PROPERTIES</div>
            <div style="font-size:13px;font-weight:700;color:#20181d;margin:6px 0;
                 word-break:break-word;
                 font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">${{esc(name)}}</div>
            <div style="display:inline-block;padding:2px 9px;border-radius:2px;
                 font-size:10px;font-weight:700;color:#9d174d;background:#fff3f8;border:1px solid ${{col}};
                 font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">«${{esc(type)}}»</div>
          </div>
          <div style="padding:10px 16px;overflow-y:auto;flex:1;">
            ${{pRows||'<div style="color:#7d7078;font-size:11px;padding:10px 0;font-family:\'Avenir Next\',\'Helvetica Neue\',Helvetica,sans-serif;">No properties.</div>'}}
            ${{sRows?`<details style="margin-top:8px;"><summary style="cursor:pointer;color:#6f626a;font-size:10px;font-weight:700;font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">More…</summary><div style="margin-top:6px;">${{sRows}}</div></details>`:""}}
          </div>
          <div style="padding:7px 16px;border-top:1px solid #eadde4;font-size:9px;
               color:#d9c4cf;text-align:center;
               font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">
            id: …${{String(nodeId).slice(-10)}}</div>`;
      }}

      network.on("click", function(params) {{
        if (!params.nodes.length) {{
          document.getElementById("detailPanel").innerHTML = BLANK_PANEL; return;
        }}
        renderDetails(params.nodes[0]);
      }});

      network.on("hoverNode", function() {{ container.style.cursor = "pointer"; }});
      network.on("blurNode",  function() {{ container.style.cursor = "default";  }});

      function searchNode() {{
        const q = document.getElementById("searchBox").value.toLowerCase();
        if (!q) {{ resetView(); return; }}
        const match = nodesData.get().find(n => (n.label||"").toLowerCase().includes(q));
        if (match) {{
          network.selectNodes([match.id]);
          network.focus(match.id, {{ scale:1.6, animation:{{ duration:600 }} }});
          renderDetails(match.id);
        }}
      }}

      function resetView() {{
        document.getElementById("searchBox").value = "";
        network.unselectAll();
        network.fit({{ animation:{{ duration:500 }} }});
        document.getElementById("detailPanel").innerHTML = BLANK_PANEL;
      }}
    </script>
    """
    else:
        # Force-layout view for broad project scopes.
        html = f"""
    <div style="display:flex; gap:12px; height:720px; font-family:'Avenir Next','Helvetica Neue',Helvetica,sans-serif;">
      <div style="flex:1; position:relative;">
        <div style="position:absolute; top:10px; left:10px; z-index:10; display:flex; gap:6px;">
          <input id="searchBox" placeholder="Search node..." onkeyup="searchNode()"
            style="padding:6px 12px; border-radius:8px; border:1px solid #eadde4;
                   background:#ffffff; color:#20181d; font-size:13px; width:190px; outline:none;
                   box-shadow:0 10px 24px rgba(71,31,51,0.06);">
          <button onclick="resetView()"
            style="padding:6px 12px; border-radius:8px; border:1px solid #eadde4;
                   background:#ffffff; cursor:pointer; font-size:12px; color:#6f626a;">
            Reset
          </button>
        </div>

        <div id="agentIndicator"
          style="position:absolute; top:10px; right:10px; z-index:10;
                 padding:5px 12px; border-radius:8px; font-size:0.75rem;
                 font-weight:600; display:none;
                 background:#f9dce8; border:1px solid #9d174d; color:#7f123f;">
          Agent running...
        </div>

        <div style="position:absolute; bottom:12px; left:12px; z-index:10;
                    background:rgba(255,255,255,.94); border:1px solid #eadde4;
                    border-radius:8px; padding:6px 10px; color:#6f626a;
                    font-size:11px; font-weight:600; max-width:520px;
                    box-shadow:0 10px 24px rgba(71,31,51,0.06);">
          {graph_caption}
        </div>

        <div id="network"
          style="height:100%; border:1px solid #eadde4; border-radius:12px;
                 background:#ffffff; box-shadow:0 16px 36px rgba(71,31,51,0.07);">
        </div>
      </div>

      <div id="detailPanel"
        style="width:320px; background:#ffffff; border:1px solid #eadde4;
               border-radius:12px; overflow:hidden;
               box-shadow:0 16px 36px rgba(71,31,51,0.07);
               display:flex; flex-direction:column; flex-shrink:0;">
        <div style="padding:18px 18px 10px;border-bottom:1px solid #eadde4;">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6f626a;font-weight:700;">Node properties</div>
          <div style="font-size:15px;color:#20181d;font-weight:750;margin-top:4px;">Nothing selected</div>
        </div>
        <div style="font-size:0.85rem; color:#6f626a; text-align:center; padding:34px 18px;">
          Click any node to inspect its typed graph properties.
        </div>
      </div>
    </div>

    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <script>
      const nodesData  = new vis.DataSet({json.dumps(nodes)});
      const edgesData  = new vis.DataSet({json.dumps(edges)});
      const groups     = {json.dumps(groups)};
      const details    = {json.dumps(node_details)};
      const badgeColor = {json.dumps(badge_colors)};
      const activeIds  = {json.dumps(list(active_ids))};
      const container  = document.getElementById("network");

      const options = {{
        groups,
        interaction: {{ hover:true, navigationButtons:true, keyboard:true, tooltipDelay:80 }},
        physics: {json.dumps(physics_options)},
        nodes: {{
          font: {{
            face: "Avenir Next, Helvetica Neue, Helvetica, sans-serif",
            size: 12, color: "#20181d", strokeWidth: 3, strokeColor: "#ffffff"
          }},
          borderWidth: 2, borderWidthSelected: 3,
          shadow: {{ enabled:true, size:8, x:2, y:3, color:"rgba(71,31,51,0.08)" }}
        }},
        edges: {{
          arrows: {{ to: {{ enabled:true, scaleFactor:0.45 }} }},
          color: {{ color:"#cbb9c3", highlight:"#9d174d", hover:"#9d174d" }},
          font: {{ size:9, align:"middle", color:"#7d7078", strokeWidth:2, strokeColor:"#ffffff" }},
          smooth: {json.dumps(edge_smooth)},
          width: 1.2, selectionWidth: 2.5
        }},
        layout: {json.dumps(layout_options)}
      }};

      const network = new vis.Network(container, {{ nodes:nodesData, edges:edgesData }}, options);
      let initialViewApplied = false;
      function applyInitialView() {{
        if (initialViewApplied) return;
        initialViewApplied = true;
        network.fit({{ animation:{{ duration:900, easingFunction:"easeInOutQuad" }} }});
        if (activeIds.length > 0) {{
          document.getElementById("agentIndicator").style.display = "block";
          pulseActiveNodes();
        }}
      }}
      network.once("stabilizationIterationsDone", applyInitialView);
      network.once("afterDrawing", function() {{ window.setTimeout(applyInitialView, 80); }});

      let pulseUp = true;
      function pulseActiveNodes() {{
        if (!activeIds.length) return;
        setInterval(() => {{
          const upd = activeIds.map(id => {{
            const n = nodesData.get(id); if (!n) return null;
            return {{ id, size: pulseUp ? (n.size||20)*1.15 : (n.size||20) }};
          }}).filter(Boolean);
          nodesData.update(upd); pulseUp = !pulseUp;
        }}, 700);
      }}

      function renderDetails(nodeId) {{
        const panel = document.getElementById("detailPanel");
        const info  = details[nodeId]; if (!info) return;
        const type  = info["Type"]||"Node"; const name = info["Name"]||nodeId;
        const color = badgeColor[type]||"#6f626a";
        const isActive = activeIds.includes(nodeId);
        const PRI = new Set(["Status","Agent Status","Entrypoint","Route","Method",
          "Storage Type","Risk Type","Severity","Confidence","Avg Score",
          "Source Path","Evidence","Description","Technical","Stakeholder"]);
        const he = v => String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;")
          .replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
        const fv = v => {{ const t=he(v); return t.length>180?t.slice(0,180)+"...":t; }};
        let pRows="", sRows="";
        for (const [k,v] of Object.entries(info)) {{
          if (k==="Type"||k==="Name") continue;
          const isAg = k==="Agent Status";
          const row = `<div style="padding:9px 0;border-bottom:1px solid #eadde4;
            ${{isAg?"background:#fff3f8;margin:0 -8px;padding:9px 8px;border-radius:6px;":""}}">
            <div style="color:#6f626a;font-size:10px;text-transform:uppercase;
                 letter-spacing:.04em;font-weight:750;margin-bottom:3px;">${{he(k)}}</div>
            <div style="color:${{isAg?"#9d174d":"#20181d"}};font-size:12px;
                 font-weight:650;line-height:1.35;word-break:break-word;">${{fv(v)}}</div>
          </div>`;
          if (PRI.has(k)||isAg) pRows+=row; else sRows+=row;
        }}
        const ab = isActive?`<div style="background:#f9dce8;border:1px solid #9d174d;
          border-radius:6px;padding:6px 10px;font-size:.75rem;color:#7f123f;
          font-weight:600;margin-bottom:10px;">Agent is currently processing this node</div>`:"";
        panel.innerHTML=`
          <div style="padding:18px;border-bottom:1px solid #eadde4;
               background:linear-gradient(180deg,#ffffff,#fff7fa);">
            ${{ab}}
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;
                 color:#6f626a;font-weight:750;">Node properties</div>
            <div style="font-size:1rem;font-weight:750;color:#20181d;margin:5px 0 8px;
                 word-break:break-word;">${{he(name)}}</div>
            <div style="display:inline-block;padding:4px 10px;border-radius:999px;
                 font-size:.72rem;font-weight:750;color:white;background:${{color}};">
              ${{he(type)}}</div>
          </div>
          <div style="padding:12px 18px;overflow-y:auto;">
            ${{pRows||'<div style="color:#6f626a;font-size:12px;padding:12px 0;">No high-signal properties.</div>'}}
            ${{sRows?`<details style="margin-top:10px;"><summary style="cursor:pointer;color:#6f626a;font-size:12px;font-weight:700;">More properties</summary><div style="margin-top:8px;">${{sRows}}</div></details>`:""}}
          </div>
          <div style="margin:0 18px 14px;padding-top:8px;border-top:1px solid #eadde4;
               font-size:.75rem;color:#9d8c96;text-align:center;">
            Node ID: ...${{String(nodeId).slice(-8)}}</div>`;
      }}

      network.on("click", function(params) {{
        if (!params.nodes.length) {{
          document.getElementById("detailPanel").innerHTML =
            '<div style="padding:18px 18px 10px;border-bottom:1px solid #eadde4;"><div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6f626a;font-weight:700;">Node properties</div><div style="font-size:15px;color:#20181d;font-weight:750;margin-top:4px;">Nothing selected</div></div><div style="font-size:.85rem;color:#6f626a;text-align:center;padding:34px 18px;">Click any node to inspect its typed graph properties.</div>';
          return;
        }}
        renderDetails(params.nodes[0]);
      }});

      network.on("dragEnd", function(params) {{
        if (!params.nodes.length) return;
        const nid = params.nodes[0];
        const pos = network.getPositions([nid])[nid];
        nodesData.update([{{ id:nid, x:pos.x, y:pos.y }}]);
      }});

      network.on("hoverNode", function() {{ container.style.cursor = "pointer"; }});
      network.on("blurNode",  function() {{ container.style.cursor = "default";  }});

      function searchNode() {{
        const q = document.getElementById("searchBox").value.toLowerCase();
        if (!q) {{ resetView(); return; }}
        const match = nodesData.get().find(n => n.label && n.label.toLowerCase().includes(q));
        if (match) {{
          network.selectNodes([match.id]);
          network.focus(match.id, {{ scale:1.5, animation:{{ duration:700 }} }});
          renderDetails(match.id);
        }}
      }}

      function resetView() {{
        document.getElementById("searchBox").value = "";
        network.unselectAll();
        network.fit({{ animation:{{ duration:600 }} }});
        document.getElementById("detailPanel").innerHTML =
          '<div style="padding:18px 18px 10px;border-bottom:1px solid #eadde4;"><div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6f626a;font-weight:700;">Node properties</div><div style="font-size:15px;color:#20181d;font-weight:750;margin-top:4px;">Nothing selected</div></div><div style="font-size:.85rem;color:#6f626a;text-align:center;padding:34px 18px;">Click any node to inspect its typed graph properties.</div>';
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


def run_sandbox_from_ui(
    flow_yaml: str,
    mode: str = "cloudrun",
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_mode = mode
    # Use a properly prefixed snapshot ID so simulate_flow's app-scoping logic
    # activates. Falls back to the full graph when no Company nodes match project_id.
    snapshot_id = f"snapshot_{project_id}" if project_id else "snapshot_default"
    old_mock = os.environ.get("SANDBOX_MOCK")
    old_mode = os.environ.get("SANDBOX_MODE")
    os.environ["SANDBOX_MOCK"] = "false"
    os.environ["SANDBOX_MODE"] = effective_mode
    try:
        result = simulate_flow.invoke(
            {
                "flow_yaml": flow_yaml,
                "dataset_snapshot_id": snapshot_id,
            }
        )
        result.setdefault("execution_mode", effective_mode)
        result.setdefault("requested_mode", mode)
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


@st.cache_data(ttl=20)
def load_project_database_assets(project_id: str) -> pd.DataFrame:
    rows = _run_read_cypher(
        """
        MATCH (n)
        WHERE n.project_id = $project_id
          AND any(label IN labels(n) WHERE label IN ['DataStore','DatabaseModel','DatabaseTable'])
        WITH coalesce(n.storage_type, labels(n)[0]) AS storage_type,
             coalesce(n.name, n.display_name, n.id, labels(n)[0]) AS raw_name,
             collect(DISTINCT labels(n)[0]) AS types,
             collect(DISTINCT n.source_path)[0..6] AS source_paths,
             count(*) AS evidence_count,
             max(coalesce(n.confidence, 0)) AS confidence
        WITH storage_type,
             CASE
               WHEN storage_type = 'orm' THEN raw_name
               WHEN raw_name CONTAINS ':' THEN split(raw_name, ':')[-1]
               ELSE raw_name
             END AS name,
             types,
             source_paths,
             evidence_count,
             confidence
        RETURN name,
               storage_type,
               types,
               evidence_count,
               source_paths,
               confidence
        ORDER BY evidence_count DESC, name
        """,
        {"project_id": project_id},
    )
    assets = df(rows)
    if assets.empty:
        return assets
    assets["target"] = assets.apply(
        lambda row: f"{row.get('name') or 'Database'} ({row.get('storage_type') or 'detected'})",
        axis=1,
    )
    return assets.drop_duplicates(subset=["target"]).reset_index(drop=True)


def redact_connection_uri(uri: str) -> str:
    if not uri:
        return ""
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", uri)


def resolve_sqlite_connection_uri(uri: str, base_dirs: list[str] | None = None) -> tuple[str, str | None]:
    cleaned = (uri or "").strip()
    lowered = cleaned.lower()
    if not lowered.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
        return cleaned, None

    path_part = re.sub(r"^sqlite(?:\+aiosqlite)?:///", "", cleaned, count=1, flags=re.IGNORECASE)
    normalized_note = None
    if lowered.startswith("sqlite+aiosqlite:///"):
        normalized_note = "Converted sqlite+aiosqlite to sqlite for the synchronous read-only sandbox connector."

    db_path = Path(path_part).expanduser()
    if not db_path.is_absolute():
        candidates: list[Path] = []
        for base_dir in base_dirs or []:
            if base_dir:
                candidates.append(Path(str(base_dir)).expanduser() / db_path)
        desktop = Path.home() / "Desktop"
        candidates.extend([
            desktop / "Bots_work" / "max_bot_suggestions" / db_path.name,
            ROOT / db_path,
            Path.cwd() / db_path,
        ])
        if desktop.exists():
            try:
                candidates.extend(desktop.glob(f"*/{db_path.name}"))
                candidates.extend(desktop.glob(f"*/*/{db_path.name}"))
            except Exception:
                pass

        existing_candidates = [candidate.resolve() for candidate in candidates if candidate.exists()]
        if existing_candidates:
            non_empty = [candidate for candidate in existing_candidates if candidate.stat().st_size > 0]
            db_path = (non_empty or existing_candidates)[0]

    if db_path.is_absolute():
        return f"sqlite:///{db_path.as_posix()}", normalized_note
    return f"sqlite:///{path_part}", normalized_note


def inspect_database_connection(
    uri: str,
    query: str | None,
    limit: int = 20,
    base_dirs: list[str] | None = None,
) -> dict[str, Any]:
    resolved_uri, note = resolve_sqlite_connection_uri(uri, base_dirs)
    try:
        connector = get_connector("SQL_Connector")
        result = connector.inspect(
            ConnectorInput(
                source=resolved_uri,
                query=query.strip() if query and query.strip() else None,
                limit=limit,
            )
        )
        return {
            "status": result.status,
            "schema": result.data_schema,
            "rows": result.rows,
            "metadata": {**result.metadata, "normalized_note": note},
            "connection": redact_connection_uri(resolved_uri),
            "input_connection": redact_connection_uri(uri),
        }
    except Exception as exc:
        return {
            "status": "fail",
            "schema": [],
            "rows": [],
            "metadata": {"normalized_note": note},
            "error": str(exc),
            "connection": redact_connection_uri(resolved_uri),
            "input_connection": redact_connection_uri(uri),
        }


def decode_flow_config(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def flow_yaml_from_config(raw: Any) -> str:
    config = decode_flow_config(raw)
    return str(config.get("yaml") or config.get("flow_yaml") or "")


def code_patches_from_config(raw: Any) -> list[dict[str, Any]]:
    config = decode_flow_config(raw)
    candidates: list[Any] = []
    candidates.extend(config.get("recommended_actions") or [])
    candidates.extend(config.get("actions") or [])
    if isinstance(config.get("proposed_summary"), dict):
        candidates.extend(config["proposed_summary"].get("recommended_actions") or [])

    patches: list[dict[str, Any]] = []
    for action in candidates:
        if not isinstance(action, dict):
            continue
        patch = action.get("code_patch")
        if action.get("action_type") == "modify_code" and isinstance(patch, dict):
            patches.append(patch)
    return patches


def apply_code_patches_to_repo(source_root: str, patches: list[dict[str, Any]]) -> dict[str, Any]:
    root = Path(source_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"status": "fail", "error": f"Source root does not exist: {source_root}", "applied": []}
    if not patches:
        return {"status": "fail", "error": "No concrete code patches are attached to this approved flow.", "applied": []}

    pending: list[tuple[Path, str, str, str]] = []
    for patch in patches:
        rel_path = str(patch.get("file_path") or "").strip()
        old_code = str(patch.get("old_code") or "")
        new_code = str(patch.get("new_code") or "")
        description = str(patch.get("description") or "")
        if not rel_path:
            return {"status": "fail", "error": "A patch is missing file_path.", "applied": []}
        target = (root / rel_path).resolve()
        if root not in target.parents and target != root:
            return {"status": "fail", "error": f"Patch path escapes source root: {rel_path}", "applied": []}
        if not target.exists():
            return {"status": "fail", "error": f"Target file not found: {rel_path}", "applied": []}
        original = target.read_text(encoding="utf-8")
        if not old_code or old_code not in original:
            return {"status": "fail", "error": f"old_code was not found in {rel_path}; no files were changed.", "applied": []}
        pending.append((target, old_code, new_code, description))

    applied: list[dict[str, Any]] = []
    backup_root = ROOT / ".agent_runs" / "deploy_backups" / uuid.uuid4().hex[:10]
    for target, old_code, new_code, description in pending:
        rel = target.relative_to(root)
        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        updated = target.read_text(encoding="utf-8").replace(old_code, new_code, 1)
        target.write_text(updated, encoding="utf-8")
        applied.append({"file": str(rel), "description": description, "backup": str(backup)})

    return {"status": "success", "applied": applied, "backup_root": str(backup_root)}


with st.sidebar:
    st.markdown("## EcoLink")
    page = st.radio(
        "View",
        [
            "Project Review",
            "Graph Display",
            "Real-Time Agents",
            "Flows",
            "Sandbox",
            "Agentic Architecture",
            "System Map",
            "Retry Inspector",
            "History",
            "Flow Results",
            "Chat",
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
    "System Map",
    "History",
    "Chat",
}
if neo4j_error and page in database_required_pages:
    st.error(neo4j_error)
    st.stop()


project = None if neo4j_error else selected_project()
project_ready = bool(project and project.get("analysis_status") == "analysis_complete")
if page not in ("Project Review", "Sandbox") and not project_ready:
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
if st.session_state.get("neo4j_last_read_error"):
    st.warning(
        "Neo4j read failed during page loading. The database may be paused, unreachable, "
        "or blocked by the network. Retry after the connection is available."
    )

# ── Persistent proposal notification banner ───────────────────────────────────
# Shown on every page (except Chat) so the admin never misses a pending proposal.
if not neo4j_error and page not in ("Chat", "Retry Inspector"):
    try:
        _notif_proposals = run_read(
            "MATCH (f:Flow {status:'proposed'}) "
            "RETURN f.id AS id, f.name AS name, f.avg_outcome_score AS score "
            "ORDER BY f.id DESC LIMIT 5"
        )
        if _notif_proposals:
            _n = len(_notif_proposals)
            with st.container():
                st.markdown(
                    f"<div style='background:rgba(216,168,63,.12);border:1px solid rgba(216,168,63,.5);"
                    f"border-radius:8px;padding:10px 14px;margin-bottom:12px'>"
                    f"<span style='font-weight:700;color:#d8a83f'>⏸ {_n} proposal(s) awaiting your review</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                for _np in _notif_proposals:
                    _nc1, _nc2, _nc3, _nc4 = st.columns([3, 1, 1, 1])
                    _nc1.markdown(f"**{_np.get('name') or _np['id']}**  \n`{_np['id']}`")
                    _nc2.metric("Score", _np.get("score") or "—")
                    with _nc3:
                        if st.button("✓ Approve", key=f"notif_approve_{_np['id']}", type="primary", use_container_width=True):
                            activate_proposal(_np["id"])
                            # Trigger sandbox re-run and store result for Flow Results page
                            try:
                                _flow_yaml_rows = run_read(
                                    f"MATCH (f:Flow {{id: '{_np['id']}'}}) RETURN f.yaml_config AS yaml_config"
                                )
                                if _flow_yaml_rows and _flow_yaml_rows[0].get("yaml_config"):
                                    import json as _json_notif
                                    _config = _json_notif.loads(_flow_yaml_rows[0]["yaml_config"])
                                    _yaml = _config.get("yaml", "")
                                    if _yaml:
                                        _sandbox_result = run_sandbox_from_ui(_yaml, "cloudrun")
                                        st.session_state["flow_result"] = {
                                            "proposal_id": _np["id"],
                                            "proposal_name": _np.get("name") or _np["id"],
                                            "sandbox_result": _sandbox_result,
                                            "flow_yaml": _yaml,
                                        }
                            except Exception:
                                pass
                            publish_event(source="human_approval", event_type="approved",
                                title="Proposal approved", detail=_np["id"],
                                payload={"proposal_id": _np["id"]})
                            clear_data_cache()
                            st.toast(f"✅ Proposal {_np['id']} approved and sandbox re-run started.", icon="✅")
                            st.rerun()
                    with _nc4:
                        if st.button("✗ Reject", key=f"notif_reject_{_np['id']}", use_container_width=True):
                            reject_proposal(_np["id"], "Rejected via notification banner")
                            publish_event(source="human_approval", event_type="rejected",
                                title="Proposal rejected", detail=_np["id"],
                                payload={"proposal_id": _np["id"]})
                            clear_data_cache()
                            st.toast(f"🚫 Proposal {_np['id']} rejected.", icon="🚫")
                            st.rerun()
    except Exception:
        pass

if page == "Project Review":
    st.subheader("Project Review")
    st.caption("Permission-first connection for the software project this agentic layer analyzes.")

    default_source = str((ROOT.parent / "fundraising_app" / "Crowd-Funding-App").resolve())
    all_projects = pd.DataFrame() if neo4j_error else load_projects()
    if not all_projects.empty:
        project_options = all_projects.sort_values("updated_at", ascending=False).reset_index(drop=True)
        option_ids = project_options["project_id"].astype(str).tolist()
        current_project_id = str(project.get("project_id") or "") if project else ""
        default_project_idx = option_ids.index(current_project_id) if current_project_id in option_ids else 0
        chosen_project_id = st.selectbox(
            "Connected project",
            option_ids,
            index=default_project_idx,
            format_func=lambda pid: (
                f"{project_options.loc[project_options['project_id'].astype(str) == pid, 'name'].iloc[0]} · "
                f"{project_options.loc[project_options['project_id'].astype(str) == pid, 'repo_path'].iloc[0]}"
            ),
            key="project_review_active_project",
        )
        if chosen_project_id != current_project_id:
            st.session_state["active_project_id"] = chosen_project_id
            selected_row = project_options[project_options["project_id"].astype(str) == chosen_project_id].iloc[0]
            st.session_state["project_name"] = str(selected_row.get("name") or "")
            st.session_state["project_repo_path"] = str(selected_row.get("repo_path") or "")
            st.rerun()

    if project:
        status_class = "status-good" if project.get("analysis_status") == "analysis_complete" else "status-warn"
        st.markdown(
            f"<span class='status-pill {status_class}'>{project.get('analysis_status', 'unknown')}</span>"
            f"<span class='status-pill'>Permission: {project.get('permission_status', 'unknown')}</span>",
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        c1.metric("Files", int(project.get("files", 0) or 0))
        c2.metric("Functions", int(project.get("functions", 0) or 0))
        c3.metric("Routes", int(project.get("routes", 0) or 0))
        c4.metric("Models", int(project.get("models", 0) or 0))
        c5.metric("Storage", int(project.get("datastores", 0) or 0))
        c6.metric("Risks", int(project.get("risks", 0) or 0))
        c7.metric("Flows", int(project.get("business_flows", 0) or 0))
        st.markdown(f"**Connected project:** `{project.get('name')}`")
        st.markdown(f"**Repository path:** `{project.get('repo_path')}`")
        st.markdown(f"**Last scan:** `{project.get('last_scan_id') or 'not scanned yet'}`")
        project_path_state = resolve_project_source_path(str(project.get("repo_path") or ""))
        if project_path_state.get("exists"):
            st.success("Local source folder is readable. Sandbox copy can use this project.")
        else:
            st.warning(
                "Local source folder is not readable on this machine. The storage, flows, "
                "and architecture below are cached from the last Neo4j scan; they are not "
                "enough for a new sandbox copy until the local path is repaired."
            )
            with st.expander("Checked project path candidates"):
                checked_paths = pd.DataFrame(project_path_state.get("checked", []))
                if checked_paths.empty:
                    st.caption("No path candidates were available.")
                else:
                    display_table(checked_paths, height=160)
    else:
        st.info("No project is connected yet. Approve a local repository before the graph and agents become available.")

    st.markdown("### Connect Project")
    st.markdown(
        "This analyzer reads source files, routes, services, models, workflows, "
        "integrations, and manifests. It excludes secret-looking files and common "
        "dependency/build/cache folders such as `.git`, `node_modules`, `.venv`, "
        "`dist`, and `build`."
    )
    if "project_name" not in st.session_state:
        st.session_state["project_name"] = str(project.get("name") if project else "Crowd Funding App")
    if "project_repo_path" not in st.session_state:
        st.session_state["project_repo_path"] = str(project.get("repo_path") if project else default_source)
    project_name = st.text_input("Project name", key="project_name")
    repo_path = st.text_input("Local codebase path", key="project_repo_path")
    if st.button("Approve & Analyze Codebase", type="primary", key="approve_project_analysis"):
        repo_resolution = resolve_project_source_path(repo_path)
        if not repo_resolution.get("exists"):
            st.error(
                "That local codebase path cannot be opened on this machine. Choose the real "
                "local project folder before approving analysis."
            )
            with st.expander("Checked project path candidates"):
                checked_paths = pd.DataFrame(repo_resolution.get("checked", []))
                if checked_paths.empty:
                    st.caption("No path candidates were available.")
                else:
                    display_table(checked_paths, height=160)
            st.stop()

        analysis_repo_path = str(repo_resolution.get("resolved_path") or repo_path)
        approved = approve_project(analysis_repo_path, project_name)
        project_id = approved["project_id"]
        st.session_state["active_project_id"] = project_id
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
            detail=analysis_repo_path,
            payload={"project_id": project_id, "repo_path": analysis_repo_path},
        )
        try:
            with st.spinner("Permission approved. Analyzing codebase and writing software graph..."):
                result = run_codebase_analysis(analysis_repo_path, project_name, project_id)
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
                payload={"project_id": project_id, "repo_path": analysis_repo_path},
            )
            raise

    if project_ready and project:
        project_review_section = st.radio(
            "Project review section",
            ["Software Summary", "Workflows", "Primitive Inspector", "Storage"],
            horizontal=True,
            key="project_review_section",
            label_visibility="collapsed",
        )
        if project_review_section == "Software Summary":
            business_flows = load_business_flow_rows(project["project_id"])
            storage = load_storage_summary(project["project_id"])
            c1, c2 = st.columns([1.25, 1])
            with c1:
                st.markdown("### Business Logic Flows")
                if business_flows.empty:
                    st.info("No business flows detected yet. Re-run analysis after adding route/function/action names.")
                else:
                    preview = add_business_flow_display_columns(business_flows)
                    st.caption(f"Showing all {len(preview)} extracted primary transaction journeys for this project.")
                    display_table(
                        preview[["transaction_journey", "business_flow", "entrypoint", "source_hint", "ordered_chain", "confidence"]],
                        height=min(760, max(380, 92 + len(preview) * 30)),
                    )
            with c2:
                st.markdown("### Architecture Signals")
                display_table(load_project_relationship_counts(project["project_id"]), height=220)
                st.markdown("### Storage Signals")
                display_table(storage, height=180)
        elif project_review_section == "Workflows":
            business_flows = load_business_flow_rows(project["project_id"])
            file_evidence = load_project_workflow_rows(project["project_id"])
            if business_flows.empty:
                st.info("No business logic flows detected yet.")
            else:
                business_flows = add_business_flow_display_columns(business_flows)
                st.caption(f"{len(business_flows)} extracted flows are available. Select any one below.")
                selected_flow = st.selectbox(
                    "Business flow",
                    business_flows["flow_display"].tolist(),
                    key="business_flow_source",
                )
                selected_row = business_flows[business_flows["flow_display"] == selected_flow].iloc[0]
                journey_label, _ = transaction_journey_kind(selected_row)
                st.markdown("### Primary Transaction Journey")
                st.caption(
                    f"Static-analysis map for a detected {journey_label}. "
                    "Use it as reviewable evidence, not a guaranteed live runtime trace."
                )
                st.info(business_flow_description(selected_row))
                render_business_flow_chain(selected_row, limit=16)
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**Entrypoint**\n\n{selected_row.get('entrypoint') or 'None'}")
                c2.markdown(f"**Storage / Integrations**\n\n{compact_list(selected_row['datastores'], 8)}\n\n{compact_list(selected_row['integrations'], 5)}")
                c3.markdown(f"**Risks / Confidence**\n\n{compact_list(selected_row['risks'], 5)}\n\n{round(float(selected_row.get('confidence') or 0), 2)}")
                with st.expander("All business logic flows", expanded=True):
                    rows = business_flows.copy()
                    display_table(
                        rows[["transaction_journey", "business_flow", "entrypoint", "source_hint", "ordered_chain", "datastores", "integrations", "risks", "confidence"]],
                        height=min(820, max(520, 92 + len(rows) * 30)),
                    )
            with st.expander("File-level architecture evidence"):
                if file_evidence.empty:
                    st.info("No file-level evidence detected yet.")
                else:
                    rows = file_evidence.copy()
                    rows["pipeline"] = rows.apply(workflow_sentence, axis=1)
                    display_table(rows[["file", "workflow_type", "pipeline"]], height=380)
        elif project_review_section == "Primitive Inspector":
            nodes = load_code_nodes(project["project_id"])
            if nodes.empty:
                st.info("No primitives available.")
            else:
                nodes = nodes.copy()
                nodes["label"] = nodes["display_name"].fillna(nodes["type"] + ": " + nodes["name"])
                st.caption(
                    "Use this to inspect why a primitive exists, where it came from, and what graph relationships make it useful."
                )
                type_options = ["All"] + sorted(nodes["type"].dropna().unique().tolist())
                c_filter, c_search = st.columns([0.7, 1.3])
                with c_filter:
                    primitive_type = st.selectbox("Type", type_options, key="primitive_type_filter")
                with c_search:
                    primitive_query = st.text_input("Search primitives", key="primitive_search", placeholder="route, file, storage, risk...")
                filtered = nodes
                if primitive_type != "All":
                    filtered = filtered[filtered["type"] == primitive_type]
                if primitive_query:
                    q = primitive_query.lower()
                    filtered = filtered[
                        filtered["label"].astype(str).str.lower().str.contains(q, regex=False)
                        | filtered["source_path"].astype(str).str.lower().str.contains(q, regex=False)
                        | filtered["id"].astype(str).str.lower().str.contains(q, regex=False)
                    ]
                if filtered.empty:
                    st.info("No primitives match those filters.")
                else:
                    selected_label = st.selectbox("Primitive", filtered["label"].tolist(), key="primitive_detail")
                    primitive = filtered[filtered["label"] == selected_label].iloc[0]
                    st.markdown(f"### {primitive['display_name']}")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Type", primitive["type"])
                    c2.metric("Confidence", round(float(primitive.get("confidence") or 0), 2))
                    c3.metric("Source", Path(str(primitive["source_path"])).name)
                    c4.metric("Relationships", len(load_primitive_relationship_rows(project["project_id"], primitive["id"])))

                    tabs = st.tabs(["Summary", "Graph Links", "Raw"])
                    with tabs[0]:
                        stakeholder = primitive.get("stakeholder_description") or "Stakeholder description is not available yet."
                        technical = primitive.get("technical_description") or "Technical description is not available yet."
                        st.success(stakeholder)
                        st.info(technical)
                        st.markdown(f"**Source path:** `{primitive['source_path']}`")
                    with tabs[1]:
                        rels = load_primitive_relationship_rows(project["project_id"], primitive["id"])
                        if rels.empty:
                            st.info("No incoming or outgoing relationships were found for this primitive.")
                        else:
                            display_table(rels, height=min(440, max(180, 92 + len(rels) * 30)))
                    with tabs[2]:
                        st.json(
                            {
                                "id": primitive["id"],
                                "type": primitive["type"],
                                "name": primitive.get("name"),
                                "source_path": primitive.get("source_path"),
                                "project_id": primitive.get("project_id"),
                                "scan_id": primitive.get("scan_id"),
                            }
                        )
        elif project_review_section == "Storage":
            storage = load_storage_summary(project["project_id"])
            st.markdown("### Detected Data Storage")
            if storage.empty:
                st.info("No storage mechanism detected from code yet. Add a database DSN in the Flows page to inspect a live database read-only.")
            else:
                display_table(storage, height=300)
            st.markdown("### Sandbox Connector Units")
            st.info(
                "Current sandbox data units are inspection-only: they prepare schema/sample snapshots "
                "from CSV or SQL sources for analysis. They do not create tables, write rows, or create "
                "new executable connector units inside the sandbox. New connectors are currently graph/indexer "
                "artifacts or human-review proposals, not sandbox mutations."
            )
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
            render_flow_chips([
                "Codebase/DataStore detected",
                "CSV or SQL connector inspects source",
                "Sandbox snapshot is prepared",
                "Agent proposes reviewable change",
                "Human approves or rejects",
            ])


elif page == "Graph Display":
    st.subheader("Project Analysis Graph")
    st.caption(
        "Generated from the latest approved codebase analysis. Nodes and relationships "
        "are filtered to the connected project."
    )
    graph_scope = st.radio(
        "Graph scope",
        [
            "Full Project Graph",
            "Software Architecture",
            "Workflow Pipeline",
            "Storage & Risk",
            "Agentic Layer Links",
        ],
        horizontal=True,
    )
    limit = st.slider("Node limit", min_value=40, max_value=240, value=180, step=20)
    payload = load_project_graph_payload(project["project_id"], limit, graph_scope)
    node_groups = [node.get("group") for node in payload["nodes"]]
    storage_count = sum(1 for group in node_groups if group in {"DataStore", "DatabaseModel", "DatabaseTable"})
    risk_count = sum(1 for group in node_groups if group == "Risk")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Nodes", len(payload["nodes"]))
    c2.metric("Relationships", len(payload["edges"]))
    c3.metric("Latest Scan", project.get("last_scan_id") or "n/a")
    c4.metric("Storage / Risks", f"{storage_count} / {risk_count}")
    st.markdown(graph_legend_html(), unsafe_allow_html=True)
    st.markdown(
        "<div class='graph-tip'>Click any node to inspect details. "
        "The Workflow Pipeline scope shows inferred BusinessFlow and FlowStep chains from static analysis. "
        "Approved sandbox optimizations are shown in red and mean approved proposal only, not source-code implementation.</div>",
        unsafe_allow_html=True,
    )
    active_node_ids = st.session_state.get("agent_active_nodes", [])
    components.html(graph_html(payload, agent_active_ids=active_node_ids, scope=graph_scope), height=730)

    # ── Active optimized flows panel ──────────────────────────────────────────
    # Shows every agent-approved flow that was overlaid on the graph above.
    # The dashed red edge in the graph = APPROVED_SANDBOX_OPTIMIZATION relationship.
    try:
        _opt_flows = run_read(
            f"""
            MATCH (f:Flow)
            WHERE f.project_id = {json.dumps(project['project_id'])}
              AND f.status IN ['active','approved']
              AND f.business_flow_id IS NOT NULL
            OPTIONAL MATCH (f)-[:USES]->(sk:Skill)
            OPTIONAL MATCH (f)-[:READS_FROM]->(cn:Connector)
            OPTIONAL MATCH (f)-[:RUNS_ON]->(sv:Server)
            RETURN f.id AS id,
                   coalesce(f.name,f.id) AS name,
                   f.status AS status,
                   f.avg_outcome_score AS score,
                   f.justification AS justification,
                   f.business_flow_id AS replaces,
                   collect(DISTINCT sk.name) AS skills,
                   cn.name AS connector,
                   sv.name AS server
            ORDER BY f.id DESC LIMIT 10
            """
        )
        if _opt_flows:
            st.markdown("---")
            st.markdown(
                "<span style='background:rgba(192,24,24,.15);border:1px solid rgba(192,24,24,.5);"
                "border-radius:6px;padding:4px 10px;font-size:13px;font-weight:700;color:#e07878'>"
                f"⬡ {len(_opt_flows)} active optimization flow(s) — shown as dashed red edges above"
                "</span>",
                unsafe_allow_html=True,
            )
            for _of in _opt_flows:
                with st.expander(
                    f"**{_of.get('name') or _of['id']}**  "
                    f"— score {_of.get('score') or '—'}  "
                    f"— {_of.get('status','').upper()}",
                    expanded=(len(_opt_flows) == 1),
                ):
                    _oc1, _oc2, _oc3 = st.columns(3)
                    _oc1.markdown(f"**Replaces flow:** `{ui_value(_of.get('replaces'), 'Not attached')}`")
                    _oc2.markdown(f"**Connector:** `{ui_value(_of.get('connector'))}`")
                    _oc3.markdown(f"**Server:** `{ui_value(_of.get('server'))}`")
                    if _of.get("skills"):
                        _skills = [s for s in _of["skills"] if s]
                        if _skills:
                            st.markdown(
                                "**Skills used:** " +
                                "  ".join(f"`{s}`" for s in _skills)
                            )
                    if _of.get("justification"):
                        st.info(_of["justification"])
    except Exception:
        pass

elif page == "Real-Time Agents":
    st.subheader("Real-Time Agent Structure & Communication")
    st.caption("Static LangGraph topology beside the live event stream used by the dashboard, CLI, sandbox, approval, and indexer.")

    tab_topology, tab_live = st.tabs(["Agent Structure", "Live Communication"])
    with tab_topology:
        components.html(agent_map_html(), height=760, scrolling=False)

    with tab_live:
        status = ensure_realtime_server()
        if status["connected"]:
            st.success(f"Realtime server connected. Active WebSocket clients: {status.get('clients', 0)}")
        else:
            st.warning("Realtime server disconnected. I tried to start it automatically; run `uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload` if it stays offline.")

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
            height=950,
            scrolling=False,
        )


elif page == "Flows":
    st.subheader("Flows")
    st.caption("Select an extracted business flow. The agent will analyse the graph chain and propose an improved version without leaving this page.")

    business_flows = load_business_flow_rows(project["project_id"]) if project else pd.DataFrame()
    if business_flows.empty:
        st.info("No business logic flows found. Re-run analysis from Project Review.")
    else:
        original_flows = add_business_flow_display_columns(business_flows).reset_index(drop=True)
        original_flows["score"] = original_flows["confidence"].fillna(0).astype(float) * 10
        st.caption(f"All {len(original_flows)} extracted BusinessFlow chains are loaded from the connected project.")

        status_rows = original_flows[[
            "business_flow",
            "entrypoint",
            "source_hint",
            "flow_type",
            "ordered_chain",
            "datastores",
            "integrations",
            "risks",
            "confidence",
        ]].copy()
        display_table(status_rows, height=min(720, max(360, 92 + len(status_rows) * 28)))

        st.divider()
        st.subheader("Optimize a Flow")
        st.caption("Select one of the extracted BusinessFlow chains below. The agent will analyse it and propose an improved version without leaving this page.")

        def select_extracted_flow(flow_id: str, row_idx: int) -> None:
            st.session_state["selected_flow_id"] = flow_id
            st.session_state["selected_flow_idx"] = row_idx
            st.session_state["flow_select_any"] = flow_id
            st.session_state["opt_phase"] = "idle"

        flow_ids = original_flows["id"].astype(str).tolist()
        selected_flow_id = st.session_state.get("selected_flow_id")
        selected_idx = flow_ids.index(selected_flow_id) if selected_flow_id in flow_ids else 0
        flow_labels = dict(zip(flow_ids, original_flows["flow_display"].astype(str)))
        selected_label = st.selectbox(
            "Select any extracted flow",
            flow_ids,
            index=selected_idx,
            format_func=lambda flow_id: flow_labels.get(flow_id, flow_id),
            key="flow_select_any",
        )
        selected_idx = int(original_flows.index[original_flows["id"].astype(str) == str(selected_label)][0])
        if st.session_state.get("selected_flow_id") != str(selected_label):
            st.session_state["opt_phase"] = "idle"
        st.session_state["selected_flow_id"] = str(selected_label)
        st.session_state["selected_flow_idx"] = selected_idx

        cols = st.columns(min(len(original_flows), 4))
        for i, (_, row) in enumerate(original_flows.iterrows()):
            score_val = float(row.get("score") or 0)
            if score_val <= 0:
                score_display = "N/A"
                score_color = "#6f626a"
            elif score_val < 5:
                score_display = f"{score_val:.1f} — low"
                score_color = "#b4234a"
            elif score_val < 7:
                score_display = f"{score_val:.1f} — ok"
                score_color = "#9a5b13"
            else:
                score_display = f"{score_val:.1f} — good"
                score_color = "#3f6f5b"

            is_sel = selected_idx == i
            border = "2px solid #9d174d" if is_sel else "1px solid #eadde4"
            bg = "#fff3f8" if is_sel else "#ffffff"
            txt = "#9d174d" if is_sel else "#20181d"
            sub = "#7d7078" if is_sel else "#6f626a"

            with cols[i % len(cols)]:
                st.markdown(f"""
                <div style="background:{bg};border:{border};border-radius:10px;
                            padding:12px 14px;cursor:pointer;transition:all .2s;
                            margin-bottom:8px;">
                    <div style="font-size:.82rem;font-weight:600;color:{txt};margin-bottom:4px;
                                white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                        {row['business_flow']}
                    </div>
                    <div style="font-size:.72rem;color:{score_color if not is_sel else '#a8a49e'};">
                        Score: {score_display}
                    </div>
                    <div style="font-size:.7rem;color:{sub};margin-top:2px;">
                        {row.get('flow_type','') or 'business flow'}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                st.button(
                    "Select",
                    key=f"sel_{i}",
                    use_container_width=True,
                    on_click=select_extracted_flow,
                    args=(str(row.get("id")), i),
                )

        sel_row = original_flows.iloc[selected_idx]
        sel_name = sel_row["business_flow"]
        sel_score_f = float(sel_row.get("score") or 0)
        sel_conn = compact_list(sel_row.get("integrations") or [], 3) or "—"
        sel_skills = sel_row.get("steps") or []
        sel_status = sel_row.get("flow_type") or "business flow"
        sel_chain = sel_row.get("ordered_chain") or "No ordered chain available."

        st.markdown(f"""
        <div style="background:#ffffff;border:1px solid #eadde4;border-radius:10px;
                    padding:12px 16px;margin:8px 0 12px;display:flex;gap:24px;
                    flex-wrap:wrap;align-items:center;">
            <div>
                <div style="font-size:.7rem;color:#6f626a;font-weight:500;">Selected flow</div>
                <div style="font-size:.88rem;font-weight:600;color:#20181d;">{sel_name}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#6f626a;font-weight:500;">Current score</div>
                <div style="font-size:.88rem;font-weight:600;color:#20181d;">{f"{sel_score_f:.1f}" if sel_score_f else "N/A"}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#6f626a;font-weight:500;">Connector</div>
                <div style="font-size:.88rem;font-weight:600;color:#20181d;">{sel_conn}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#6f626a;font-weight:500;">Status</div>
                <div style="font-size:.88rem;font-weight:600;color:#20181d;">{sel_status}</div>
            </div>
            <div>
                <div style="font-size:.7rem;color:#6f626a;font-weight:500;">Skills</div>
                <div style="font-size:.88rem;font-weight:600;color:#20181d;">{len(sel_skills) if isinstance(sel_skills, list) else 0} steps</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.caption(sel_chain)
        with st.expander("What this flow is doing", expanded=True):
            st.write(business_flow_description(sel_row))
            render_business_flow_chain(sel_row, limit=10)
        can_optimize, optimization_reason = flow_needs_optimization(sel_row)
        if can_optimize:
            st.info(optimization_reason)
        else:
            st.success(optimization_reason)

        opt_phase = st.session_state.get("opt_phase", "idle")
        opt_slot = st.empty()

        def opt_anim(phase="idle", flow_name=""):
            phases_map = {
                "idle":       (-1, f"Ready — review '{flow_name}'"),
                "reading":    (0,  f"Planner reading '{flow_name}' skills and history from Neo4j..."),
                "thinking":   (1,  f"Generator drafting a proposal-only alternative for '{flow_name}'..."),
                "proposing":  (2,  f"Critic validating the proposal against graph evidence..."),
                "validating": (3,  f"Simulator validating the proposed workflow without changing code..."),
                "evaluating": (4,  f"Evaluator comparing sandbox metrics against the baseline..."),
                "approval":   (5,  f"Human Approval waiting for a decision on '{flow_name}'..."),
                "done":       (6,  f"Complete — explanation and proposal saved for '{flow_name}'"),
                "error":      (-2, "Optimization stopped — review the agent output below"),
            }
            active, msg = phases_map.get(phase, phases_map["idle"])
            agents = [
                ("Planner",   "Reads flow + history", "#4f6f8f", "#eef3fb"),
                ("Generator", "Calls Gemini AI",       "#3f6f5b", "#eaf4ef"),
                ("Critic",    "Validates proposal",    "#9a5b13", "#fff6d9"),
                ("Simulator", "Tests in sandbox",      "#7a4f93", "#f3effb"),
                ("Evaluator", "Compares score",        "#7b4eb3", "#eee4ff"),
                ("Approval",  "Awaits admin",          "#b04a72", "#ffe3ef"),
            ]
            cards = ""
            for i, (name, role, color, bg) in enumerate(agents):
                is_a = active == i
                is_d = active > i and active >= 0
                op = "1" if (is_a or is_d) else "0.55"
                bd = f"2px solid {color}" if is_a else "1px solid #eadde4"
                cbg = bg if is_a else "#ffffff"
                pulse = "animation:pulse-card 1.4s ease-in-out infinite;" if is_a else ""
                shimmer = '<div style="position:absolute;top:0;left:-100%;width:60%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.55),transparent);animation:shimmer 1.3s infinite;pointer-events:none;"></div>' if is_a else ""
                if is_a:
                    dot = f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{color};animation:blink .9s infinite;margin-right:5px;flex-shrink:0;"></span>'
                elif is_d:
                    dot = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#3f6f5b;margin-right:5px;flex-shrink:0;"></span>'
                else:
                    dot = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#eadde4;margin-right:5px;flex-shrink:0;"></span>'
                cards += f'<div style="background:{cbg};border:{bd};border-radius:10px;padding:12px 10px;opacity:{op};transition:all .45s;{pulse}position:relative;overflow:hidden;">{shimmer}<div style="display:flex;align-items:center;margin-bottom:5px;">{dot}<span style="font-size:.8rem;font-weight:600;color:{color};">{name}</span></div><div style="font-size:.68rem;color:#6f626a;line-height:1.3;">{role}</div></div>'
                if i < len(agents) - 1:
                    ac = color if (is_a or is_d) else "#eadde4"
                    cards += f'<div style="display:flex;align-items:center;justify-content:center;color:{ac};font-size:16px;">&rarr;</div>'
            pct = max(0, int(active / 6 * 100)) if active >= 0 else 0
            if phase == "done":   sb,sbd,sc = "#f0faf5","#3f6f5b","#3f6f5b"
            elif phase == "error":sb,sbd,sc = "#fdf0f0","#b4234a","#b4234a"
            elif phase == "idle": sb,sbd,sc = "#fcfafb","#eadde4","#6f626a"
            else:                 sb,sbd,sc = "#edf5ff","#4f6f8f","#4f6f8f"
            return f"""<style>
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
@keyframes pulse-card{{0%,100%{{box-shadow:0 0 0 0 rgba(50,103,168,.18)}}50%{{box-shadow:0 0 0 5px rgba(50,103,168,.06)}}}}
@keyframes shimmer{{to{{left:140%}}}}
</style>
<div style="background:#ffffff;border:1px solid #eadde4;border-radius:12px;padding:16px 16px 14px;margin-bottom:10px;">
<div style="display:grid;grid-template-columns:1fr 20px 1fr 20px 1fr 20px 1fr 20px 1fr 20px 1fr;align-items:center;gap:3px;margin-bottom:12px;">{cards}</div>
<div style="background:#ede8df;border-radius:999px;height:2px;margin-bottom:9px;overflow:hidden;">
<div style="background:#9d174d;height:2px;width:{pct}%;border-radius:999px;transition:width .7s ease;"></div></div>
<div style="background:{sb};border:1px solid {sbd};border-radius:7px;padding:8px 12px;font-size:.76rem;color:{sc};font-weight:500;">{msg}</div>
</div>"""

        opt_slot.markdown(opt_anim(opt_phase, sel_name), unsafe_allow_html=True)

        if opt_phase == "done":
            st.markdown(f"""
            <div style="background:#f0faf5;border:1px solid #3f6f5b;border-radius:10px;
                        padding:14px 16px;margin-bottom:10px;">
                <div style="font-size:.8rem;font-weight:600;color:#3f6f5b;margin-bottom:8px;">What the agent improved</div>
                <div style="display:flex;gap:32px;flex-wrap:wrap;">
                    <div>
                        <div style="font-size:.68rem;color:#6f626a;">Before</div>
                        <div style="font-size:.82rem;font-weight:600;color:#20181d;">{sel_name}</div>
                        <div style="font-size:.72rem;color:#b4234a;">Score: {f"{sel_score_f:.1f}" if sel_score_f else "N/A"}</div>
                    </div>
                    <div style="font-size:18px;color:#eadde4;align-self:center;">&rarr;</div>
                    <div>
                        <div style="font-size:.68rem;color:#6f626a;">Proposed</div>
                        <div style="font-size:.82rem;font-weight:600;color:#20181d;">Human-review proposal only</div>
                        <div style="font-size:.72rem;color:#3f6f5b;">No code changed</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            st.info("Review the generated proposal below. It is a visual/text explanation, not a code mutation.")

        optimize_label = "Analyze / optimize selected flow"
        if st.button(optimize_label, type="primary", use_container_width=True, disabled=not can_optimize):
            steps = sel_row.get("steps") or []
            primitive_ids = [
                step.get("primitive_id")
                for step in steps
                if isinstance(step, dict) and step.get("primitive_id")
            ]
            optimize_payload = {
                "project_id": project["project_id"],
                "business_flow_id": sel_row.get("id"),
                "business_flow": sel_name,
                "entrypoint": sel_row.get("entrypoint"),
                "ordered_chain": sel_chain,
                "steps": steps,
                "primitive_ids": primitive_ids,
                "source_paths": sel_row.get("source_paths") or [],
                "datastores": sel_row.get("datastores") or [],
                "integrations": sel_row.get("integrations") or [],
                "risks": sel_row.get("risks") or [],
                "confidence": sel_row.get("confidence"),
            }
            optimize_payload = json.loads(json.dumps(optimize_payload, default=str))
            goal = (
                f"Create a proposal-only optimization analysis for the business flow named '{sel_name}'. Current score is {sel_score_f:.1f}. "
                "Analyse its BusinessFlow, FlowStep, primitive graph evidence, historical failures, "
                "and sandbox result. Return a visual/text before-vs-proposed explanation with justification. "
                "Do not mutate real code and do not generate modify_code/code_patch actions."
            )
            st.session_state["opt_phase"] = "reading"
            st.session_state["last_optimize_payload"] = optimize_payload
            opt_slot.markdown(opt_anim("reading", sel_name), unsafe_allow_html=True)
            thread_id = uuid.uuid4().hex[:8]
            publish_event(
                thread_id=thread_id,
                source="ui",
                target="planner",
                event_type="started",
                title="BusinessFlow optimization requested",
                detail=sel_name,
                payload=optimize_payload,
            )

            repo_path = str(project.get("repo_path") or "")

            before_flows = load_flows()
            before_proposal_ids = set()
            if not before_flows.empty:
                before_proposal_ids = set(
                    before_flows[
                        (before_flows["status"].fillna("") == "proposed")
                        & (before_flows["business_flow_id"].fillna("") == str(sel_row.get("id")))
                    ]["id"].tolist()
                )

            phase_rank = {
                "reading": 0,
                "thinking": 1,
                "proposing": 2,
                "validating": 3,
                "evaluating": 4,
                "approval": 5,
                "done": 6,
                "error": 99,
            }
            phase_agent = {
                "reading": ("planner", "generator", "Planner is reading graph evidence"),
                "thinking": ("generator", "critic", "Generator is drafting actions"),
                "proposing": ("critic", "simulator", "Critic is validating the proposal"),
                "validating": ("simulator", "evaluator", "Simulator is testing in sandbox"),
                "evaluating": ("evaluator", "human_approval", "Evaluator is comparing results"),
                "approval": ("human_approval", "", "Human approval is required"),
            }

            def advance_phase(next_phase: str) -> None:
                current = st.session_state.get("opt_phase", "idle")
                if phase_rank.get(next_phase, -1) < phase_rank.get(current, -1):
                    return
                st.session_state["opt_phase"] = next_phase
                opt_slot.markdown(opt_anim(next_phase, sel_name), unsafe_allow_html=True)
                src, target, title = phase_agent.get(next_phase, ("ui", "", next_phase))
                publish_event(
                    thread_id=thread_id,
                    source=src,
                    target=target,
                    event_type="phase",
                    title=title,
                    detail=sel_name,
                    payload=optimize_payload,
                )

            advance_phase("reading")
            cmd = [
                sys.executable, "main.py", "--goal", goal,
                "--thread-id", thread_id,
                "--project-id", project["project_id"],
                "--business-flow-id", str(sel_row.get("id")),
                "--proposal-only",
            ]
            if repo_path:
                cmd.extend(["--source-path", repo_path])

            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            stdout_lines: list[str] = []
            deadline = time.monotonic() + 240
            code = 124
            stderr = ""
            while True:
                if proc.poll() is not None:
                    code = proc.returncode
                    remaining = proc.stdout.read() if proc.stdout else ""
                    if remaining:
                        stdout_lines.append(remaining)
                    break
                if time.monotonic() > deadline:
                    proc.kill()
                    stderr = "Agent run timed out after 240 seconds."
                    code = 124
                    break
                ready, _, _ = select.select([proc.stdout], [], [], 0.25)
                if not ready:
                    continue
                raw_line = proc.stdout.readline()
                if not raw_line:
                    continue
                stdout_lines.append(raw_line)
                marker = raw_line.lower()
                if "[planner]" in marker:
                    advance_phase("reading")
                elif "[generator]" in marker or "generator" in marker:
                    advance_phase("thinking")
                elif "[critic]" in marker or "critic" in marker:
                    advance_phase("proposing")
                elif "[simulator]" in marker or "sandbox" in marker:
                    advance_phase("validating")
                elif "[evaluator]" in marker or "evaluator" in marker:
                    advance_phase("evaluating")
                elif "approval required" in marker or "[human_approval]" in marker:
                    advance_phase("approval")

            stdout = "".join(stdout_lines)
            combined_output = stdout if not stderr else f"{stdout}\n\nDiagnostics:\n{stderr}"
            clear_data_cache()

            try:
                _gctx = load_graphrag_context(goal)
                st.session_state["last_graphrag_context"] = _gctx
            except Exception:
                st.session_state.pop("last_graphrag_context", None)

            updated_flows = load_flows()
            after_proposal_ids = set()
            if not updated_flows.empty:
                after_proposal_ids = set(
                    updated_flows[
                        (updated_flows["status"].fillna("") == "proposed")
                        & (updated_flows["business_flow_id"].fillna("") == str(sel_row.get("id")))
                    ]["id"].tolist()
                )
            new_proposal_ids = sorted(after_proposal_ids - before_proposal_ids)
            st.session_state["last_optimize_result"] = {
                "flow": sel_name,
                "thread_id": thread_id,
                "exit_code": code,
                "stdout": combined_output,
                "failure_summary": summarize_agent_failure(combined_output, code),
                "new_proposal_ids": new_proposal_ids,
                "payload": optimize_payload,
            }

            if new_proposal_ids and code == 0:
                st.session_state["opt_phase"] = "done"
                opt_slot.markdown(opt_anim("done", sel_name), unsafe_allow_html=True)
                st.success(f"Optimization complete — proposal {new_proposal_ids[0]} is ready below.")
            else:
                st.session_state["opt_phase"] = "error"
                opt_slot.markdown(opt_anim("error", sel_name), unsafe_allow_html=True)
                if code != 0:
                    st.error(summarize_agent_failure(combined_output, code))
                else:
                    st.warning(summarize_agent_failure(combined_output, code))

        if "last_optimize_result" in st.session_state:
            result = st.session_state["last_optimize_result"]
            payload = result.get("payload", {}) or {}
            st.markdown("### Last Optimization Review")
            c1, c2, c3 = st.columns(3)
            c1.metric("Flow", result.get("flow") or "Selected flow")
            c2.metric("Agent Exit", result.get("exit_code"))
            c3.metric("New Proposals", len(result.get("new_proposal_ids", [])))
            render_flow_chips(_chain_items(payload.get("ordered_chain"), payload.get("steps")))
            if result.get("new_proposal_ids"):
                st.success("A human-review proposal was created. No real project code was changed.")
                latest_flows = load_flows()
                if not latest_flows.empty:
                    created = latest_flows[latest_flows["id"].isin(result.get("new_proposal_ids", []))]
                    for _, proposal_row in created.iterrows():
                        parsed_payload = parse_proposal_payload(proposal_row.get("yaml_config"))
                        before_summary = parsed_payload.get("before_summary") or {}
                        proposed_summary = parsed_payload.get("proposed_summary") or {}
                        if before_summary or proposed_summary:
                            render_human_proposal_card(
                                title=str(proposal_row.get("name") or proposal_row.get("id")),
                                before_summary=before_summary,
                                proposed_summary=proposed_summary,
                                justification=proposal_row.get("justification"),
                                parsed_payload=parsed_payload,
                            )
            else:
                st.warning(result.get("failure_summary") or summarize_agent_failure(result.get("stdout", ""), result.get("exit_code")))

            _gctx = st.session_state.get("last_graphrag_context")
            if _gctx:
                st.divider()
                with st.expander("GraphRAG Evidence Used for This Optimization", expanded=True):
                    render_graphrag_context_viz(_gctx, flow_name=result.get("flow") or "")

        st.divider()
        st.subheader("Pending Optimizations")
        flows = load_flows()
        proposals = flows[flows["status"].fillna("") == "proposed"] if not flows.empty else flows
        if proposals.empty:
            st.info("No pending proposals.")
        for _, row in proposals.iterrows():
            st.markdown(f"### {row['id']}")
            # Score comparison — baseline from stored payload, optimized from Flow node
            _prs = row.get("avg_score")
            if _prs is not None:
                _prs_f = float(_prs)
                _ppp = parse_proposal_payload(row.get("yaml_config"))
                _prs_before = _ppp.get("before_summary", {}).get("baseline_score")
                _prs_base = float(_prs_before) if _prs_before is not None else 2.8
                _prs_delta = round(_prs_f - _prs_base, 2)
                _prs_verdict = "Same or better ✓" if _prs_delta >= 0 else "Degraded ✗"
                _pv1, _pv2, _pv3 = st.columns(3)
                _pv1.metric("Optimized score", round(_prs_f, 2), delta=_prs_delta)
                _pv2.metric("Baseline score",  round(_prs_base, 2))
                _pv3.metric("Verdict", _prs_verdict)
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
                parsed_payload = parse_proposal_payload(row.get("yaml_config"))
                before_summary = parsed_payload.get("before_summary") or {}
                proposed_summary = parsed_payload.get("proposed_summary") or {}
                if before_summary or proposed_summary:
                    render_human_proposal_card(
                        title=str(row.get("name") or row.get("id")),
                        before_summary=before_summary,
                        proposed_summary=proposed_summary,
                        justification=row.get("justification"),
                        parsed_payload=parsed_payload,
                    )
                else:
                    st.markdown(f"#### {row.get('name') or row.get('id')}")
                    st.caption(f"Business flow: {row.get('business_flow_id') or 'legacy proposal'}")
                    st.metric("Proposed score", row.get("avg_score"))
                    if row.get("justification"):
                        st.info(row.get("justification"))

        # ── Inline skill approval notice ─────────────────────────────────────
        # If any pending SkillProposals exist (created by the generator when it
        # referenced skills that don't yet exist in Graph B), surface them here
        # so the admin can approve without leaving the flow review context.
        _pending_skills = load_skill_proposals()
        _pending_skills = _pending_skills[_pending_skills["status"].fillna("") == "proposed"]
        if not _pending_skills.empty:
            with st.container(border=True):
                st.markdown(
                    f"**⚠️ {len(_pending_skills)} skill proposal(s) awaiting approval**  \n"
                    "The generator referenced skills that don't exist in Graph B. "
                    "The Critic will reject flows that use unapproved skills. "
                    "Review and approve the ones you want to allow."
                )
                for _, _sk in _pending_skills.iterrows():
                    _c1, _c2, _c3 = st.columns([3, 1, 1])
                    _c1.markdown(f"**`{_sk['id']}`** — {_sk.get('purpose') or _sk.get('name', '')}")
                    with _c2:
                        if st.button("Approve", key=f"inline_sk_approve_{_sk['id']}", type="primary", use_container_width=True):
                            approve_skill_proposal(_sk["id"])
                            clear_data_cache()
                            st.rerun()
                    with _c3:
                        if st.button("Reject", key=f"inline_sk_reject_{_sk['id']}", use_container_width=True):
                            reject_skill_proposal(_sk["id"], "Rejected from Flows page")
                            clear_data_cache()
                            st.rerun()
        # ─────────────────────────────────────────────────────────────────────

        with st.expander("Supporting pipelines, database flows, sandbox, and web evidence"):
            support_tabs = st.tabs(["Software Pipelines", "Database Flows", "Sandbox", "Optional Web Evidence"])
            with support_tabs[0]:
                all_pipelines = load_pipelines()
                if all_pipelines.empty:
                    st.info("No pipelines discovered yet. Run an ingest with a source path containing routes and contract/API code.")
                else:
                    display_df = all_pipelines[["name", "app_id", "entrypoint", "steps", "entity_types", "has_contract"]].copy()
                    display_df["risk"] = display_df["has_contract"].map(lambda x: "HIGH" if x else "low")
                    display_table(display_df.drop(columns=["has_contract"]), height=280)
            with support_tabs[1]:
                flows = load_flows()
                if flows.empty:
                    st.info("No database flows found.")
                else:
                    display_table(flows, height=360)
            with support_tabs[2]:
                mode = "cloudrun"
                st.info("Sandbox target: Cloud Run only. Local/mock execution is disabled for UI sandbox runs.")
                flow_yaml = st.text_area("Sandbox flow YAML", value=default_sandbox_flow(), height=220, key="flow_sandbox_yaml")
                if st.button("Create Sandbox Run", type="primary", key="flow_sandbox_run"):
                    result = run_sandbox_from_ui(flow_yaml, mode)
                    st.session_state["last_sandbox_result"] = result
                    if result.get("status") == "success":
                        st.success("Sandbox run created successfully.")
                    else:
                        st.error(result.get("error_log", "Sandbox run failed."))
                if "last_sandbox_result" in st.session_state:
                    render_sandbox_review(st.session_state["last_sandbox_result"])
            with support_tabs[3]:
                websites = load_websites()
                if websites.empty:
                    st.info("No web evidence indexed yet. Ingest a running site or deployed URL below to attach UI/domain evidence to flow review.")
                else:
                    st.markdown("**Indexed web evidence**")
                    selected_domain = st.selectbox(
                        "Evidence source",
                        websites["domain"].tolist(),
                        key="flow_web_evidence_domain",
                    )
                    web_analysis = load_website_analysis(selected_domain)
                    funding = web_analysis.get("funding", {})
                    donations = web_analysis.get("donations", {})
                    wc1, wc2, wc3, wc4 = st.columns(4)
                    wc1.metric("Routes", web_analysis.get("routes", 0))
                    wc2.metric("Campaigns", funding.get("campaigns", 0))
                    wc3.metric("Donors", donations.get("donors", 0))
                    wc4.metric("Contract Methods", web_analysis.get("contract_methods", 0))

                    evidence_notes = []
                    if web_analysis.get("routes", 0):
                        evidence_notes.append(f"Route evidence is available for {web_analysis.get('routes', 0)} UI route(s).")
                    if web_analysis.get("contract_methods", 0):
                        evidence_notes.append(f"{web_analysis.get('contract_methods', 0)} contract/API method(s) can be compared against workflow steps.")
                    if funding.get("campaigns", 0):
                        evidence_notes.append(f"{funding.get('campaigns', 0)} campaign/funding entity node(s) were extracted.")
                    if web_analysis.get("owner_gaps"):
                        evidence_notes.append("Owner-link gaps remain: " + compact_list(web_analysis.get("owner_gaps"), 4))
                    if not evidence_notes:
                        evidence_notes.append("The site is indexed, but no strong route/entity evidence was extracted yet.")
                    for note in evidence_notes:
                        st.write(f"- {note}")

                    with st.expander("Extracted web entities", expanded=False):
                        display_table(load_web_entities(selected_domain), height=280)

                st.divider()
                default_source = str((ROOT.parent / "fundraising_app" / "Crowd-Funding-App").resolve())
                url = st.text_input("Website URL", value="http://127.0.0.1:5173", key="flow_ingest_url")
                source_path = st.text_input("Local source folder", value=default_source, key="flow_ingest_source")
                if st.button("Ingest Website & Source", type="primary"):
                    result = crawl_website(
                        start_url=url,
                        max_depth=1,
                        max_pages=30,
                        clear_existing=True,
                        source_path=source_path or None,
                    )
                    clear_data_cache()
                    st.success(f"Indexed {result['domain']}")
                    st.json(result)






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
            st.caption("These are persisted graph object types. The indexer can only write labels that exist in the schema.")
            display_table(load_label_counts(), height=360)
        with right:
            st.markdown("### Relationship Primitives")
            st.caption("These are the allowed connections the graph has actually observed or written.")
            display_table(load_relationship_counts(), height=360)
        st.markdown("### Runtime Primitives")
        st.info(
            "Runtime primitives are executable or operational resources, such as connectors, servers, and programmes. "
            "They are useful when the agent needs to choose what can run a flow or read data. They are not the same as "
            "source-code primitives like Route, Function, DataStore, or Risk."
        )
        display_table(load_runtime_primitives(), height=260)
        st.markdown("### New Node Type Policy")
        st.warning(
            "The agentic layer must not silently create unknown live node labels. If it needs a new graph object type, "
            "it creates a SchemaChangeProposal for human review. After approval, the schema/indexer can be updated and "
            "future scans can write that node type."
        )

    with tab_sandbox_arch:
        st.markdown("### Proposed Sandbox Architecture")
        st.caption("This is the new architecture being created in isolation. It does not alter production data.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Connector Units", len(CONNECTOR_REGISTRY))
        c2.metric("Mode", "Read-only")
        c3.metric("Mutation", "Blocked")
        c4.metric("Output", "Review summary")
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
        st.info(
            "Connector creation is not an executable sandbox action yet. The sandbox can inspect "
            "CSV/SQL sources, prepare snapshots, simulate workflow proposals, and return reviewable "
            "recommendations. New connector creation must remain a human-reviewed proposal until a "
            "dedicated connector proposal/apply path exists."
        )
        display_table(
            pd.DataFrame(
                [
                    {"action_type": "modify_workflow", "target": "Workflow", "sandbox_effect": "simulate proposal in flow sandbox", "modes": "all"},
                    {"action_type": "modify_code", "target": "Source file", "sandbox_effect": "apply patch in isolated code sandbox, run test suite", "modes": "source_path only"},
                    {"action_type": "create_skill", "target": "New Skill node", "sandbox_effect": "writes SkillProposal; blocked in proposal-only mode", "modes": "non-proposal-only"},
                    {"action_type": "add_validation", "target": "Route or Function", "sandbox_effect": "recommend guardrail", "modes": "all"},
                    {"action_type": "add_observability", "target": "Runtime path", "sandbox_effect": "recommend tracing/metrics", "modes": "all"},
                    {"action_type": "flag_risk", "target": "Risk", "sandbox_effect": "surface issue for review", "modes": "all"},
                    {"action_type": "request_admin_approval", "target": "Unknown capability", "sandbox_effect": "block execution", "modes": "all"},
                ]
            ),
            height=220,
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

elif page == "Retry Inspector":
    st.subheader("Retry Inspector")
    st.caption(
        "Every time the Critic or Evaluator rejects a proposal, the structured retry "
        "context is recorded here. Use this to understand why the agent looped and what "
        "it was told to fix before regenerating."
    )

    _ri_tab_retries, _ri_tab_arch = st.tabs(["Retry Events", "Architecture Sandbox"])

    with _ri_tab_arch:
        st.caption(
            "Copy the project and optional database into an isolated sandbox, analyze the "
            "data/connectors, test the copied project, then approve the tested architecture."
        )
        if project:
            with st.expander("Create tested architecture proposal", expanded=True):
                proposal_repo_path = str(project.get("repo_path") or "")
                path_resolution = resolve_project_source_path(proposal_repo_path)
                resolved_repo_path = path_resolution.get("resolved_path") or proposal_repo_path
                project_source_ready = bool(path_resolution.get("exists"))
                indexed_storage = load_exact_storage_sources(project["project_id"])
                db_detection = discover_database_sources(resolved_repo_path) if project_source_ready else {
                    "selected_source": "",
                    "detected_sources": [],
                    "graph_credentials": discover_database_sources("__missing_project_source__").get("graph_credentials", []),
                }
                proposal_db_source = db_detection.get("selected_source", "")

                c1, c2, c3 = st.columns(3)
                c1.metric("Project Source", "Ready" if project_source_ready else "Missing")
                c2.metric("Database Sources", 0 if not project_source_ready else len(db_detection.get("detected_sources", [])) + len(indexed_storage))
                c3.metric("Credential Refs", len(db_detection.get("graph_credentials", [])))
                if project_source_ready:
                    st.caption("Using the repository path saved in Project Review.")

                if not project_source_ready:
                    st.error(
                        "Retry Inspector read the repository path saved in Project Review, but this "
                        "machine cannot open that folder. The sandbox cannot copy the project until "
                        "Project Review points to a local folder that exists here."
                    )
                    st.info(
                        "Project Review may still show storage and flows because those are cached "
                        "Neo4j facts from the last successful scan. Cached facts can explain the old "
                        "architecture, but they cannot be copied or tested in a new sandbox."
                    )
                    with st.expander("Checked project path candidates"):
                        checked_paths = pd.DataFrame(path_resolution.get("checked", []))
                        if checked_paths.empty:
                            st.caption("No path candidates were available.")
                        else:
                            display_table(checked_paths, height=180)
                    if not indexed_storage.empty:
                        with st.expander("Last-scan database evidence (stale, not usable for sandbox)"):
                            stale_rows = indexed_storage.copy()
                            display_table(
                                stale_rows[
                                    [
                                        "database_or_storage",
                                        "storage_type",
                                        "evidence_file",
                                        "linked_files",
                                        "confidence",
                                    ]
                                ],
                                height=180,
                            )
                else:
                    graph_database_sources = []
                    if not indexed_storage.empty:
                        graph_database_sources = [
                            {
                                "kind": "indexed_project_storage",
                                "credential_ref": "project graph",
                                "source": row.get("database_or_storage"),
                                "value": row.get("storage_type") or "detected storage",
                                "evidence_file": row.get("evidence_file"),
                                "linked_files": row.get("linked_files"),
                                "confidence": row.get("confidence"),
                            }
                            for _, row in indexed_storage.iterrows()
                        ]
                    runtime_sources = db_detection.get("detected_sources", [])
                    visible_sources = [
                        {
                            "kind": item.get("kind"),
                            "credential_ref": item.get("credential_ref") or "local file",
                            "source": item.get("source"),
                            "value": item.get("display_value"),
                            "evidence_file": "runtime source",
                            "linked_files": [],
                            "confidence": None,
                        }
                        for item in runtime_sources
                    ] + graph_database_sources

                    st.markdown("**Detected database and storage evidence**")
                    if visible_sources:
                        display_table(pd.DataFrame(visible_sources), height=180)
                    else:
                        st.info(
                            "No runtime database credential, local database file, or indexed project storage "
                            "was detected. The proposal will still analyze project connector boundaries."
                        )

                graph_credentials = db_detection.get("graph_credentials", [])
                if graph_credentials:
                    with st.expander("Graph credential references"):
                        display_table(
                            pd.DataFrame(
                                [
                                    {
                                        "credential_ref": item.get("credential_ref"),
                                        "value": item.get("display_value"),
                                    }
                                    for item in graph_credentials
                                ]
                            ),
                            height=140,
                        )

                external_db_source = ""
                external_credential_ref = ""
                with st.expander("External database credentials", expanded=not project_source_ready):
                    use_external_db = st.checkbox(
                        "Use external database credentials for this sandbox run",
                        value=not project_source_ready,
                        key="retry_external_db_enabled",
                    )
                    if use_external_db:
                        external_credential_ref = st.text_input(
                            "Credential reference name",
                            value=f"external_db_{str(project.get('name') or 'project').lower().replace(' ', '_')}",
                            key="retry_external_db_ref",
                        ).strip()
                        input_mode = st.radio(
                            "Connection input",
                            ["Connection fields", "SQLAlchemy URL"],
                            horizontal=True,
                            key="retry_external_db_mode",
                        )
                        if input_mode == "SQLAlchemy URL":
                            external_db_source = st.text_input(
                                "SQLAlchemy database URL",
                                value="",
                                type="password",
                                placeholder="postgresql+psycopg://user:password@host:5432/database",
                                key="retry_external_db_url",
                            ).strip()
                        else:
                            db_kind = st.selectbox(
                                "Database type",
                                ["postgresql+pg8000", "mysql+pymysql", "sqlite"],
                                key="retry_external_db_kind",
                            )
                            if db_kind == "sqlite":
                                sqlite_path = st.text_input(
                                    "SQLite file path",
                                    value="",
                                    key="retry_external_sqlite_path",
                                ).strip()
                                if sqlite_path:
                                    external_db_source = sqlite_path
                            else:
                                default_port = "5432" if db_kind.startswith("postgresql") else "3306"
                                host = st.text_input("Host", value="", key="retry_external_db_host").strip()
                                port = st.text_input("Port", value=default_port, key="retry_external_db_port").strip()
                                database_name = st.text_input("Database name", value="", key="retry_external_db_name").strip()
                                username = st.text_input("Username", value="", key="retry_external_db_user").strip()
                                password = st.text_input("Password", value="", type="password", key="retry_external_db_password")
                                if host and port and database_name and username:
                                    external_db_source = (
                                        f"{db_kind}://{quote_plus(username)}:{quote_plus(password)}"
                                        f"@{host}:{port}/{quote_plus(database_name)}"
                                    )
                        st.caption(
                            "The password is used only for this sandbox run. The proposal stores "
                            "the credential reference name, not the secret value."
                        )

                replacement_mode = st.radio(
                    "Apply mode after approval",
                    ["merge", "replace"],
                    horizontal=True,
                    key="retry_arch_replacement_mode",
                )

                effective_db_source = external_db_source or proposal_db_source
                credential_refs = [external_credential_ref] if external_db_source and external_credential_ref else []
                can_run_architecture_sandbox = project_source_ready or bool(external_db_source)
                if external_db_source:
                    st.caption("Database connection will be tested before any architecture proposal is saved.")
                button_label = (
                    "Copy, Analyze & Test In Sandbox"
                    if project_source_ready
                    else "Analyze External Database In Sandbox"
                )
                if st.button(
                    button_label,
                    type="primary",
                    key="retry_arch_run",
                    disabled=not can_run_architecture_sandbox,
                ):
                    with st.spinner("Creating sandbox copy, analyzing project/database, and running validation..."):
                        try:
                            _db_probe_ok = True
                            if effective_db_source:
                                db_probe = probe_database_source(effective_db_source)
                                if not db_probe.get("ok"):
                                    _db_probe_ok = False
                                    st.error(db_probe.get("hint") or db_probe.get("error") or "Database connection failed.")
                                    with st.expander("Database connection details"):
                                        st.json(db_probe, expanded=True)
                                    publish_event(
                                        source="ui",
                                        target="sandbox",
                                        event_type="error",
                                        title="Database connection failed",
                                        detail=db_probe.get("hint") or db_probe.get("error") or "Database connection failed.",
                                        payload={"project_id": project["project_id"], "database_probe": db_probe},
                                    )
                            if _db_probe_ok:
                                if project_source_ready:
                                    payload = build_architecture_proposal(
                                        source_path=resolved_repo_path,
                                        project_id=project["project_id"],
                                        project_name=str(project.get("name") or "Project"),
                                        sandbox_home=str(ROOT / ".agent_architecture_sandbox"),
                                        database_source=effective_db_source,
                                        validation_command=None,
                                        replacement_mode=replacement_mode,
                                        credential_refs=credential_refs,
                                    )
                                else:
                                    payload = build_database_only_architecture_proposal(
                                        project_id=project["project_id"],
                                        project_name=str(project.get("name") or "Project"),
                                        sandbox_home=str(ROOT / ".agent_architecture_sandbox"),
                                        database_source=effective_db_source,
                                        replacement_mode=replacement_mode,
                                        credential_refs=credential_refs,
                                    )
                                proposal_id = create_architecture_proposal(payload)
                                publish_event(
                                    source="ui",
                                    target="sandbox",
                                    event_type="result",
                                    title="Architecture sandbox proposal tested",
                                    detail=f"{proposal_id}: {payload['validation']['status']}",
                                    payload={
                                        "proposal_id": proposal_id,
                                        "project_id": project["project_id"],
                                        "test_status": payload["validation"]["status"],
                                        "replacement_mode": replacement_mode,
                                    },
                                )
                                clear_data_cache()
                                if payload["validation"]["status"] == "success":
                                    st.success("Architecture proposal tested successfully and is ready for approval.")
                                else:
                                    st.warning("Architecture proposal was created, but validation did not pass yet.")
                                render_architecture_proposal(payload)
                        except Exception as exc:
                            publish_event(
                                source="ui",
                                target="sandbox",
                                event_type="error",
                                title="Architecture sandbox proposal failed",
                                detail=str(exc),
                                payload={"project_id": project["project_id"], "repo_path": proposal_repo_path},
                            )
                            st.error(str(exc))

            proposals_df = load_architecture_proposals(project["project_id"])
            st.markdown("### Tested Architecture Approvals")
            if proposals_df.empty:
                st.info("No architecture proposals yet. Create one above after the project analysis is complete.")
            else:
                for _, row in proposals_df.iterrows():
                    title = f"{row['id']} - {str(row.get('status') or 'unknown').upper()}"
                    with st.expander(title, expanded=row.get("status") == "proposed"):
                        try:
                            payload = json.loads(row.get("payload_json") or "{}")
                        except json.JSONDecodeError:
                            payload = {}
                        st.caption(
                            f"Mode: {row.get('replacement_mode')} | "
                            f"Test: {row.get('test_status')} | "
                            f"Created: {row.get('created_at')}"
                        )
                        if payload:
                            render_architecture_proposal(payload)
                        if row.get("status") == "proposed":
                            if row.get("tested"):
                                st.success("Changes are tested. Admin can approve this architecture.")
                                c1, c2 = st.columns(2)
                                with c1:
                                    if st.button("Approve Tested Architecture", type="primary", key=f"arch_approve_{row['id']}"):
                                        approve_architecture_proposal(row["id"])
                                        publish_event(
                                            source="ui",
                                            target="graph",
                                            event_type="approved",
                                            title="Tested architecture approved",
                                            detail=row["id"],
                                            payload={"proposal_id": row["id"], "project_id": project["project_id"]},
                                        )
                                        clear_data_cache()
                                        st.rerun()
                                with c2:
                                    if st.button("Reject", key=f"arch_reject_{row['id']}"):
                                        reject_architecture_proposal(row["id"], "Rejected in Retry Inspector")
                                        clear_data_cache()
                                        st.rerun()
                            else:
                                st.warning("Approval is blocked until sandbox validation succeeds.")

    with _ri_tab_retries:

        # Scan the full event file for retry events — don't tail-slice so old
        # threads are never truncated on busy systems. Filter at read time.
        from src.realtime.event_bus import EVENT_FILE as _RI_EVENT_FILE  # noqa: PLC0415
        _ri_raw_events: list[dict] = []
        if _RI_EVENT_FILE.exists():
            import json as _json  # noqa: PLC0415
            with _RI_EVENT_FILE.open("r", encoding="utf-8") as _fh:
                for _line in _fh:
                    if not _line.strip():
                        continue
                    try:
                        _ev = _json.loads(_line)
                    except Exception:
                        continue
                    if _ev.get("source") in ("critic", "evaluator") and _ev.get("event_type") == "decision":
                        _ri_raw_events.append(_ev)

        # A rejection event is any critic/evaluator decision that either:
        #   (a) carries retry_count (deterministic + LLM-path critic, evaluator)
        #   (b) carries failed_metric or issues (evaluator / deterministic critic)
        #   (c) has critic_passed=False (LLM-path critic before nodes.py fix)
        retry_events = [
            e for e in _ri_raw_events
            if (
                e.get("payload", {}).get("retry_count") is not None
                or e.get("payload", {}).get("failed_metric")
                or e.get("payload", {}).get("issues")
                or e.get("payload", {}).get("critic_passed") is False
            )
        ]

        if not retry_events:
            st.info(
                "No retry events found. Run an agent optimization — retries will appear here "
                "whenever the Critic rejects a flow or the Evaluator finds the simulation score "
                "insufficient."
            )
        else:
            # Group by thread_id, most-recent thread first
            by_thread: dict = {}
            for e in retry_events:
                tid = e.get("thread_id", "unknown")
                by_thread.setdefault(tid, []).append(e)

            threads_sorted = sorted(
                by_thread.keys(),
                key=lambda t: by_thread[t][-1].get("created_at", ""),
                reverse=True,
            )

            # Date filter — derive earliest date across all threads
            import datetime as _dt  # noqa: PLC0415
            _all_dates = sorted({
                e.get("created_at", "")[:10]
                for e in retry_events
                if e.get("created_at", "")[:10]
            })
            _date_opts = ["All dates"] + _all_dates
            _fi_col, col_sel, col_stat = st.columns([2, 3, 1])
            with _fi_col:
                _date_filter = st.selectbox("Date", _date_opts, key="ri_date_filter")

            # Apply date filter to visible threads
            if _date_filter != "All dates":
                threads_sorted = [
                    t for t in threads_sorted
                    if any(e.get("created_at", "").startswith(_date_filter) for e in by_thread[t])
                ]

            if not threads_sorted:
                st.info(f"No retry events on {_date_filter}.")
                st.stop()

            with col_sel:
                selected_thread = st.selectbox(
                    "Thread",
                    threads_sorted,
                    format_func=lambda t: (
                        f"{t}  ({len(by_thread[t])} rejection(s))  "
                        f"— {by_thread[t][-1].get('created_at','')[:10]}"
                    ),
                )
            with col_stat:
                st.metric("Total retries", len(by_thread[selected_thread]))

            st.divider()

            for idx, event in enumerate(by_thread[selected_thread], 1):
                source = event.get("source", "")
                payload = event.get("payload", {})
                ts = event.get("created_at", "")[:19].replace("T", " ")
                retry_no = payload.get("retry_count", idx)

                # Header pill
                src_color = "#e07845" if source == "critic" else "#9a70cc"
                src_icon = "🔍" if source == "critic" else "📊"
                st.markdown(
                    f"<span style='background:{src_color}22;border:1px solid {src_color};"
                    f"border-radius:6px;padding:4px 10px;font-size:13px;font-weight:700'>"
                    f"{src_icon} {source.title()} — retry #{retry_no}</span>"
                    f"<span style='color:#6f626a;font-size:11px;margin-left:10px'>{ts}</span>",
                    unsafe_allow_html=True,
                )

                if source == "critic":
                    _critic_path = payload.get("critic_path", "llm")
                    issues = payload.get("issues", [])
                    evidence = payload.get("evidence_node_ids", [])
                    suggestions = payload.get("suggestions", "")
                    invalid_skills = payload.get("invalid_skills", [])
                    invalid_connectors = payload.get("invalid_connectors", [])

                    if issues:
                        label = "Issues found (deterministic checks):" if _critic_path == "deterministic" else "Issues found (LLM validation):"
                        st.markdown(f"**{label}**")
                        for issue in issues:
                            st.error(issue, icon="⛔")

                    if invalid_skills or invalid_connectors:
                        _bad = []
                        if invalid_skills:
                            _bad.append(f"Unknown skills: `{'`, `'.join(invalid_skills)}`")
                        if invalid_connectors:
                            _bad.append(f"Unknown connectors: `{'`, `'.join(invalid_connectors)}`")
                        st.warning("  \n".join(_bad), icon="⚠️")

                    if suggestions:
                        st.info(f"**What to fix:** {suggestions}", icon="💡")

                    # Only show evidence grounding info on the LLM path —
                    # deterministic rejections never reach evidence validation.
                    if _critic_path == "llm":
                        if evidence:
                            st.success(
                                f"Graph-grounded evidence accepted: {', '.join(str(e) for e in evidence[:6])}"
                                + (" …" if len(evidence) > 6 else ""),
                                icon="✅",
                            )
                        else:
                            st.warning("No evidence_node_ids were provided — flow had no graph grounding.", icon="⚠️")

                    detail = event.get("detail", "")
                    if detail and _critic_path != "deterministic":
                        st.caption(f"Detail: {detail}")

                elif source == "evaluator":
                    fm = payload.get("failed_metric", {})
                    if fm:
                        c1, c2, c3 = st.columns(3)
                        score = fm.get("match_score", 0.0)
                        threshold = fm.get("threshold", 0.0)
                        delta = round(score - threshold, 2) if isinstance(score, (int, float)) else None
                        c1.metric("Simulation score", score)
                        c2.metric("Required threshold", threshold)
                        c3.metric("Gap", delta, delta_color="inverse")
                        st.caption(f"Sim status: {fm.get('sim_status', '?')}")

                    llm_reason = payload.get("llm_reason", "")
                    if llm_reason:
                        st.info(f"**LLM reasoning:** {llm_reason}", icon="💬")

                    updated_hypothesis = payload.get("updated_hypothesis", "")
                    if updated_hypothesis:
                        st.info(f"**Revised hypothesis for next attempt:** {updated_hypothesis}", icon="🔄")

                st.markdown("")  # spacing between retries


        # ─────────────────────────────────────────────────────────────────────────────
        # Page: History
        # ─────────────────────────────────────────────────────────────────────────────
elif page == "History":
    st.subheader("Optimization History")
    st.caption(
        "Tracks how each agent run and activated flow change has moved system efficiency over time. "
        "All data is live from Neo4j — if the system hasn't run, this page stays empty."
    )

    import pandas as _pd
    import math as _math

    def _safe_float(v):
        try: return float(v)
        except: return None

    traces = load_traces()

    # ── System-silent state ──────────────────────────────────────────────────
    if traces.empty:
        try:
            _probe = run_read("RETURN 1 AS ok")
            _neo4j_live = bool(_probe)
        except Exception:
            _neo4j_live = False

        if not _neo4j_live:
            st.error(
                "Neo4j is offline. Start the database and reconnect — "
                "no history is available without it."
            )
        else:
            st.info(
                "The system hasn't run yet — nothing to show. "
                "Start an agent cycle with `python main.py --goal '...'` "
                "and return here once the first run completes."
            )
        st.stop()

    # ── Enrich traces ────────────────────────────────────────────────────────
    traces["score_f"]    = traces["score"].apply(_safe_float)
    traces["baseline_f"] = traces["baseline_score"].apply(_safe_float) if "baseline_score" in traces.columns else pd.Series(dtype=float)
    traces = traces.sort_values("timestamp").reset_index(drop=True)

    # Run-over-run delta: did the LAST update to this flow actually help?
    traces["prev_score"]  = traces.groupby("flow_id")["score_f"].shift(1)
    traces["score_delta"] = (traces["score_f"] - traces["prev_score"]).round(2)

    # ── Summary metrics ──────────────────────────────────────────────────────
    total     = len(traces)
    completed = int((traces["status"].fillna("") == "completed").sum())
    avg_sc    = traces["score_f"].dropna().mean()
    scored    = traces["score_f"].dropna()
    if len(scored) >= 2:
        trend = scored.iloc[-1] - scored.iloc[0]
    else:
        trend = None

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Agent runs",     total)
    m2.metric("Completed",      completed)
    m3.metric("Current avg score", f"{avg_sc:.2f}" if not _math.isnan(avg_sc or float("nan")) else "—")
    if trend is not None:
        m4.metric("Score drift (first → last)", f"{trend:+.2f}", delta=round(trend, 2))
    else:
        m4.metric("Score drift", "—")

    st.divider()

    # ── Efficiency over time ─────────────────────────────────────────────────
    eff_df = traces[["timestamp", "score_f", "baseline_f"]].dropna(subset=["score_f"]).copy()
    if len(eff_df) >= 2:
        eff_df = eff_df.sort_values("timestamp").reset_index(drop=True)
        eff_df.index = range(1, len(eff_df) + 1)
        eff_df = eff_df.rename(columns={"score_f": "Flow score", "baseline_f": "Random baseline"})
        st.markdown(
            "**Efficiency over agent runs** "
            "<span style='font-size:12px;color:#6f626a;font-weight:400'>"
            "— each point is one completed run; upward slope = system improving</span>",
            unsafe_allow_html=True,
        )
        st.line_chart(
            eff_df[["Flow score", "Random baseline"]],
            color=["#9d174d", "#4f6f8f"],
            height=220,
        )
    else:
        st.caption("Need at least 2 completed runs to plot the efficiency trend.")

    st.divider()

    c_left, c_right = st.columns([1, 1])

    # ── Run-over-run score delta ─────────────────────────────────────────────
    with c_left:
        st.markdown(
            "**Score change per run** "
            "<span style='font-size:11px;color:#6f626a;font-weight:400'>"
            "vs previous run of the same flow — positive = update helped</span>",
            unsafe_allow_html=True,
        )
        delta_df = traces[["timestamp", "score_delta"]].dropna(subset=["score_delta"]).tail(20).copy()
        if not delta_df.empty:
            delta_df["run"] = delta_df["timestamp"].astype("string").str[5:16]
            st.bar_chart(delta_df.set_index("run")[["score_delta"]], color=["#9d174d"], height=200)
        else:
            st.caption("Need at least 2 runs of the same flow to compute deltas.")

    # ── Active flows leaderboard ─────────────────────────────────────────────
    with c_right:
        st.markdown(
            "**Active flows by performance** "
            "<span style='font-size:11px;color:#6f626a;font-weight:400'>"
            "— flows currently live in the system, ranked by avg outcome score</span>",
            unsafe_allow_html=True,
        )
        try:
            _active_flows = run_read(
                "MATCH (f:Flow {status:'active'}) "
                "RETURN f.name AS name, f.id AS id, "
                "       f.avg_outcome_score AS score "
                "ORDER BY f.avg_outcome_score DESC LIMIT 10"
            )
        except Exception:
            _active_flows = []

        if _active_flows:
            for fl in _active_flows:
                sc = _safe_float(fl.get("score"))
                label = fl.get("name") or fl.get("id", "?")
                bar_pct = min(100, int((sc / 10) * 100)) if sc else 0
                bar_col = "#9d174d" if (sc or 0) >= 7 else "#d8a83f" if (sc or 0) >= 5 else "#b4234a"
                sc_str  = f"{sc:.1f}" if sc is not None else "—"
                st.markdown(
                    f"<div style='margin:6px 0'>"
                    f"<div style='font-size:11px;color:#6f626a;margin-bottom:3px'>{label}</div>"
                    f"<div style='display:flex;align-items:center;gap:8px'>"
                    f"<div style='flex:1;height:8px;background:#f1e6ec;border-radius:4px'>"
                    f"<div style='width:{bar_pct}%;height:100%;background:{bar_col};border-radius:4px'></div>"
                    f"</div>"
                    f"<span style='font-size:12px;font-weight:700;color:{bar_col};min-width:36px'>{sc_str}</span>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No active flows yet — approve a proposal to activate one.")



# ─────────────────────────────────────────────────────────────────────────────
# Page: Chat  — admin conversation with the agentic system
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Chat":
    st.subheader("Chat")
    st.caption(
        "Ask questions about the system, review pending actions, or give commands. "
        "The assistant has live access to proposals, skill requests, traces, events, "
        "and flow performance data."
    )

    # ── System context (gathered fresh each page load) ────────────────────────
    def _chat_context() -> str:
        lines = ["== EcoLink NeuroCore — live system state =="]

        # Pending flow proposals
        try:
            _flows = run_read(
                "MATCH (f:Flow {status:'proposed'}) "
                "RETURN f.id AS id, f.name AS name, f.avg_outcome_score AS score, "
                "       f.justification AS justification LIMIT 10"
            )
            if _flows:
                lines.append(f"\n--- Pending flow proposals ({len(_flows)}) ---")
                for r in _flows:
                    lines.append(f"  {r['id']}  name={r.get('name','')}  score={r.get('score','')}  justification={str(r.get('justification',''))[:120]}")
            else:
                lines.append("\nNo pending flow proposals.")
        except Exception:
            pass

        # Pending skill proposals
        try:
            _skills = run_read(
                "MATCH (s:SkillProposal {status:'proposed'}) "
                "RETURN s.id AS id, s.name AS name, s.purpose AS purpose, s.proposed_by AS by LIMIT 20"
            )
            if _skills:
                lines.append(f"\n--- Pending skill proposals ({len(_skills)}) ---")
                for r in _skills:
                    lines.append(f"  {r['id']}  name={r.get('name','')}  purpose={str(r.get('purpose',''))[:100]}  proposed_by={r.get('by','')}")
            else:
                lines.append("\nNo pending skill proposals.")
        except Exception:
            pass

        # Recent execution traces
        try:
            _traces = run_read(
                "MATCH (et:ExecutionTrace)-[:RAN_FLOW]->(f:Flow) "
                "OPTIONAL MATCH (et)-[:RESULTED_IN]->(o:Outcome) "
                "RETURN f.id AS flow_id, et.status AS status, o.score AS score, "
                "       et.skills_applied AS skills, et.baseline_score AS baseline, "
                "       toString(et.timestamp) AS ts "
                "ORDER BY ts DESC LIMIT 10"
            )
            if _traces:
                lines.append(f"\n--- Recent execution traces ({len(_traces)}) ---")
                for r in _traces:
                    lines.append(
                        f"  flow={r.get('flow_id','')}  status={r.get('status','')}  "
                        f"score={r.get('score','')}  baseline={r.get('baseline','')}  "
                        f"skills={r.get('skills',[])}  ts={str(r.get('ts',''))[:16]}"
                    )
        except Exception:
            pass

        # Last 10 retry events
        try:
            _evts = [
                e for e in read_events(limit=200)
                if e.get("source") in ("critic", "evaluator")
                and e.get("event_type") == "decision"
            ][-10:]
            if _evts:
                lines.append(f"\n--- Recent retry events ({len(_evts)}) ---")
                for e in _evts:
                    p = e.get("payload", {})
                    lines.append(
                        f"  [{e.get('source')}] retry#{p.get('retry_count','?')}  "
                        f"issues={p.get('issues',[])}  "
                        f"failed_metric={p.get('failed_metric',{})}  "
                        f"thread={e.get('thread_id','')}"
                    )
        except Exception:
            pass

        return "\n".join(lines)

    # ── Action executor: parse commands from assistant response ───────────────
    def _execute_actions(text: str) -> list[str]:
        """Detect and execute simple action keywords in the assistant reply."""
        import re as _re
        executed = []

        # APPROVE_SKILL <id>
        for m in _re.finditer(r"APPROVE_SKILL\s+([^\s,\n]+)", text, _re.IGNORECASE):
            skill_id = m.group(1).strip("`\"'")
            try:
                approve_skill_proposal(skill_id)
                executed.append(f"✅ Approved skill: `{skill_id}`")
            except Exception as exc:
                executed.append(f"❌ Could not approve `{skill_id}`: {exc}")

        # REJECT_SKILL <id>
        for m in _re.finditer(r"REJECT_SKILL\s+([^\s,\n]+)", text, _re.IGNORECASE):
            skill_id = m.group(1).strip("`\"'")
            try:
                reject_skill_proposal(skill_id, "Rejected via Chat")
                executed.append(f"🚫 Rejected skill: `{skill_id}`")
            except Exception as exc:
                executed.append(f"❌ Could not reject `{skill_id}`: {exc}")

        # APPROVE_PROPOSAL <id>
        for m in _re.finditer(r"APPROVE_PROPOSAL\s+([^\s,\n]+)", text, _re.IGNORECASE):
            prop_id = m.group(1).strip("`\"'")
            try:
                activate_proposal(prop_id)
                executed.append(f"✅ Approved proposal: `{prop_id}`")
            except Exception as exc:
                executed.append(f"❌ Could not approve `{prop_id}`: {exc}")

        # REJECT_PROPOSAL <id>
        for m in _re.finditer(r"REJECT_PROPOSAL\s+([^\s,\n]+)", text, _re.IGNORECASE):
            prop_id = m.group(1).strip("`\"'")
            reason_m = _re.search(r"reason[:\s]+(.+)", text, _re.IGNORECASE)
            reason = reason_m.group(1)[:200] if reason_m else "Rejected via Chat"
            try:
                reject_proposal(prop_id, reason)
                executed.append(f"🚫 Rejected proposal: `{prop_id}`")
            except Exception as exc:
                executed.append(f"❌ Could not reject `{prop_id}`: {exc}")

        if executed:
            clear_data_cache()
        return executed

    # ── LLM response generator ────────────────────────────────────────────────
    def _generate_response(history: list[dict], user_msg: str) -> str:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI as _Gemini
            from langchain_core.messages import HumanMessage as _HM, AIMessage as _AI, SystemMessage as _SM

            _llm = _Gemini(
                model="gemini-2.5-flash",
                google_api_key=os.environ.get("GOOGLE_API_KEY", ""),
                temperature=0.3,
            )

            ctx = _chat_context()
            system = f"""You are the EcoLink NeuroCore admin assistant.
You have access to the live system state below and can answer questions, explain decisions, and take actions.

{ctx}

You can execute the following commands by including them in your response:
  APPROVE_SKILL <skill_id>      — approve a pending SkillProposal
  REJECT_SKILL  <skill_id>      — reject a pending SkillProposal
  APPROVE_PROPOSAL <proposal_id> — activate a pending flow proposal
  REJECT_PROPOSAL  <proposal_id> — reject a pending flow proposal

Always explain what you are doing before issuing a command.
Be concise. Use markdown for structure. If you don't know something, say so."""

            msgs = [_SM(content=system)]
            for m in history[-10:]:  # keep last 10 turns in context
                if m["role"] == "user":
                    msgs.append(_HM(content=m["content"]))
                else:
                    msgs.append(_AI(content=m["content"]))
            msgs.append(_HM(content=user_msg))

            return _llm.invoke(msgs).content
        except Exception as exc:
            return f"⚠️ Could not reach the language model: {exc}"

    # ── Chat UI ───────────────────────────────────────────────────────────────
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    # Render existing messages
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input box
    if user_input := st.chat_input("Ask about the system, explain a retry, approve a skill…"):
        # Show user message immediately
        with st.chat_message("user"):
            st.markdown(user_input)
        st.session_state["chat_history"].append({"role": "user", "content": user_input})

        # Generate and show assistant response
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                reply = _generate_response(st.session_state["chat_history"][:-1], user_input)
            st.markdown(reply)

            # Execute any action commands found in the reply
            actions = _execute_actions(reply)
            if actions:
                st.markdown("---")
                for a in actions:
                    st.markdown(a)
                reply += "\n\n---\n" + "\n".join(actions)

        st.session_state["chat_history"].append({"role": "assistant", "content": reply})

    # Sidebar clear button
    with st.sidebar:
        if st.button("Clear chat", key="clear_chat_btn"):
            st.session_state["chat_history"] = []
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Page: Flow Results — sandbox execution results for approved proposals
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Flow Results":
    st.subheader("Flow Results")
    st.caption(
        "Sandbox execution results for approved optimization proposals. "
        "Each approved flow is re-run through the skill execution engine so you can "
        "see per-company match quality, improvement over random baseline, and which "
        "skills produced the best outcomes."
    )

    import pandas as _pd_fr
    import json as _json_fr

    # ── load recently activated flows ─────────────────────────────────────────
    activated = run_read(
        "MATCH (f:Flow {status:'active'}) "
        "RETURN f.id AS id, f.name AS name, f.yaml_config AS yaml_config, "
        "       f.avg_outcome_score AS score, f.justification AS justification, "
        "       f.project_id AS project_id "
        "ORDER BY f.id DESC LIMIT 20"
    )

    if not activated:
        st.info(
            "No approved flows yet. Approve a proposal from the Flows page or the notification "
            "banner — the sandbox runs automatically on approval."
        )
    else:
        flow_names = [f.get("name") or f["id"] for f in activated]
        flow_map   = {f.get("name") or f["id"]: f for f in activated}

        # Show last session result first if available
        _default_idx = 0
        if "flow_result" in st.session_state:
            _last_name = st.session_state["flow_result"].get("proposal_name")
            if _last_name in flow_names:
                _default_idx = flow_names.index(_last_name)

        selected_name = st.selectbox("Select approved flow", flow_names, index=_default_idx)
        sel_flow = flow_map[selected_name]

        # Extract YAML from stored yaml_config
        _yaml_str = ""
        try:
            _cfg = _json_fr.loads(sel_flow.get("yaml_config") or "{}")
            _yaml_str = _cfg.get("yaml", "")
        except Exception:
            pass

        # ── metrics header ─────────────────────────────────────────────────
        mf1, mf2, mf3 = st.columns(3)
        mf1.metric("Flow ID",    sel_flow["id"])
        mf2.metric("Score",      sel_flow.get("score") or "—")
        mf3.metric("Status",     "✅ Active")
        if sel_flow.get("justification"):
            st.info(sel_flow["justification"])

        st.divider()

        # ── last session result (from notification approval) ──────────────
        _sess_result = st.session_state.get("flow_result", {})
        if _sess_result.get("proposal_id") == sel_flow["id"]:
            st.markdown("**Latest sandbox run result** _(from approval action)_")
            _sr = _sess_result.get("sandbox_result", {})
            if _sr.get("status") == "success":
                _m = _sr.get("metrics", {})
                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Match score",     _m.get("match_score", "—"))
                sc2.metric("vs baseline",     _m.get("sandbox_baseline_score", "—"))
                sc3.metric("Sample size",     _m.get("sample_size", "—"))
                sc4.metric("Latency ms",      _m.get("latency_ms", "—"))

                _traces = _sr.get("traces", [])
                if _traces:
                    st.markdown("**Per-company match breakdown**")
                    _tr_df = _pd_fr.DataFrame([{
                        "Company":    t.get("company_id"),
                        "Mentor":     t.get("mentor_id"),
                        "Score":      t.get("simulated_outcome_score"),
                        "Skills":     " → ".join(t.get("skills_applied", [])) if isinstance(t.get("skills_applied"), list) else str(t.get("skills_applied", "")),
                        "Status":     t.get("status"),
                    } for t in _traces])
                    # Color score column
                    if "Score" in _tr_df.columns:
                        st.dataframe(
                            _tr_df,
                            column_config={
                                "Score": st.column_config.ProgressColumn(
                                    "Score", min_value=0, max_value=10, format="%.2f"
                                )
                            },
                            hide_index=True,
                            use_container_width=True,
                        )
                    else:
                        display_table(_tr_df)
            else:
                st.error(f"Sandbox failed: {_sr.get('error_log', 'unknown error')}")
            st.divider()

        # ── re-run sandbox on demand ───────────────────────────────────────
        st.markdown("**Run simulation again**")
        col_run, col_mode = st.columns([2, 1])
        with col_mode:
            _mode = "cloudrun"
            st.metric("Mode", "Cloud Run")
        with col_run:
            if st.button("▶ Run Sandbox", type="primary", key="fr_run_sandbox", use_container_width=True):
                if not _yaml_str:
                    st.warning("No flow YAML stored for this proposal. The sandbox cannot run without it.")
                else:
                    with st.spinner("Running sandbox simulation…"):
                        _result = run_sandbox_from_ui(_yaml_str, _mode)
                    st.session_state["flow_result"] = {
                        "proposal_id":   sel_flow["id"],
                        "proposal_name": selected_name,
                        "sandbox_result": _result,
                        "flow_yaml": _yaml_str,
                    }
                    st.rerun()

        # ── flow YAML viewer ───────────────────────────────────────────────
        if _yaml_str:
            with st.expander("View flow YAML"):
                st.code(_yaml_str, language="yaml")


# ─────────────────────────────────────────────────────────────────────────────
# Page: System Map
# Live architecture analysis: database state, external services, flow topology,
# agent tool wiring. No generic diagrams — only what is real and connected now.
# ─────────────────────────────────────────────────────────────────────────────
elif page == "System Map":
    st.subheader("System Map")
    st.caption(
        "Live analysis of every connected component: database schema & counts, "
        "external service status, active flow topology, and agent-to-tool wiring. "
        "All data is read directly from Neo4j and the runtime environment."
    )

    import pandas as _pd_sm

    def _sm_section(title: str, icon: str = "") -> None:
        st.markdown(f"### {icon} {title}" if icon else f"### {title}")

    def _sm_badge(label: str, value: str, ok: bool | None = None) -> str:
        color = ("#9d174d" if ok else "#b4234a") if ok is not None else "#6f626a"
        bg    = ("rgba(68,194,154,.12)" if ok else "rgba(220,102,102,.12)") if ok is not None else "rgba(255,255,255,.06)"
        return (
            f"<span style='display:inline-block;background:{bg};border:1px solid {color};"
            f"border-radius:6px;padding:4px 10px;margin:3px;font-size:12px'>"
            f"<b style='color:{color}'>{label}</b> "
            f"<span style='color:#ded6c4'>{value}</span></span>"
        )

    # ── 1. Database layer ─────────────────────────────────────────────────────
    _sm_section("Database", "🗄️")
    st.caption("Neo4j AuraDB — the single persistence layer for both Graph A (historical) and Graph B (blueprint).")

    try:
        _node_counts = run_read(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt "
            "ORDER BY cnt DESC LIMIT 30"
        )
        _rel_counts = run_read(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt "
            "ORDER BY cnt DESC LIMIT 20"
        )
        _total_nodes = sum(r["cnt"] for r in _node_counts)
        _total_rels  = sum(r["cnt"] for r in _rel_counts)

        _dc1, _dc2, _dc3 = st.columns(3)
        _dc1.metric("Total nodes",         _total_nodes)
        _dc2.metric("Total relationships", _total_rels)
        _dc3.metric("Node types",          len(_node_counts))

        _tab_a, _tab_b, _tab_c = st.tabs(["Graph A — Historical", "Graph B — Blueprint", "Schema counts"])

        _GRAPH_A = {"Company","Mentor","Programme","Outcome","ExecutionTrace","LearningEvent","SkillProposal"}
        _GRAPH_B = {"Flow","Skill","Connector","Server","Pipeline"}
        _CODE    = {"Project","Repository","File","Route","Service","Function","DatabaseModel","DataStore","Entity","BusinessFlow","FlowStep","Integration","Artifact","Risk","Workflow"}

        with _tab_a:
            st.caption("Historical match data and agent learning events.")
            _a_rows = [r for r in _node_counts if r["label"] in _GRAPH_A]
            if _a_rows:
                _a_df = _pd_sm.DataFrame(_a_rows).rename(columns={"label":"Node type","cnt":"Count"})
                _a_df["% of total"] = (_a_df["Count"] / max(_total_nodes,1) * 100).round(1)
                st.dataframe(_a_df, hide_index=True, use_container_width=True)
            else:
                st.info("No Graph A nodes yet. Seed data with ecolink-graph/ingest.py.")

        with _tab_b:
            st.caption("Live system blueprint: active flows, skills, connectors, and servers.")
            _b_rows = [r for r in _node_counts if r["label"] in _GRAPH_B]
            if _b_rows:
                _b_df = _pd_sm.DataFrame(_b_rows).rename(columns={"label":"Node type","cnt":"Count"})
                _b_df["% of total"] = (_b_df["Count"] / max(_total_nodes,1) * 100).round(1)
                st.dataframe(_b_df, hide_index=True, use_container_width=True)
            else:
                st.info("No Graph B nodes yet.")

        with _tab_c:
            _sc_df = _pd_sm.DataFrame(_node_counts).rename(columns={"label":"Node type","cnt":"Nodes"})
            _sr_df = _pd_sm.DataFrame(_rel_counts).rename(columns={"rel":"Relationship","cnt":"Count"})
            _c1, _c2 = st.columns(2)
            with _c1:
                st.markdown("**Node types**")
                st.dataframe(_sc_df, hide_index=True, use_container_width=True, height=320)
            with _c2:
                st.markdown("**Relationship types**")
                st.dataframe(_sr_df, hide_index=True, use_container_width=True, height=320)

    except Exception as _e:
        st.error(f"Database query failed: {_e}")

    st.divider()

    # ── 2. External services ──────────────────────────────────────────────────
    _sm_section("External Services", "🔌")
    st.caption("Services wired into the platform. Status is derived from environment variables — not a live ping.")

    _svc_rows = []

    # Neo4j
    _neo_uri = os.environ.get("NEO4J_URI","")
    _svc_rows.append({"Service":"Neo4j AuraDB","Config":_neo_uri[:60] or "not set","Role":"Dual-graph persistence","Status":"✅ connected" if not neo4j_error else "❌ error"})

    # Gemini
    _gkey = os.environ.get("GOOGLE_API_KEY","")
    _svc_rows.append({"Service":"Google Gemini API","Config":f"key {'set' if _gkey else 'NOT SET'} ({os.environ.get('GOOGLE_API_KEY','')[:8]}…)" if _gkey else "not configured","Role":"LLM for all 6 agent nodes","Status":"✅ key present" if _gkey else "❌ not configured"})

    # Sandbox
    _mock = os.environ.get("SANDBOX_MOCK","true").lower() == "true"
    _mode = os.environ.get("SANDBOX_MODE","local")
    if _mock:
        _svc_rows.append({"Service":"Sandbox (Mock)","Config":"SANDBOX_MOCK=true","Role":"Deterministic skill scoring — no subprocess","Status":"⚠️ mock mode"})
    elif _mode == "cloudrun":
        _gcp = os.environ.get("GOOGLE_CLOUD_PROJECT","")
        _job = os.environ.get("SANDBOX_JOB_NAME","")
        _svc_rows.append({"Service":"GCP Cloud Run","Config":f"project={_gcp}  job={_job}","Role":"Remote sandbox execution","Status":"✅ configured" if _gcp and _job else "❌ not configured"})
    else:
        _svc_rows.append({"Service":"Sandbox (Local subprocess)","Config":"SANDBOX_MODE=local","Role":"sandbox_task.py subprocess","Status":"✅ local mode"})

    # Realtime event server
    try:
        _rs = realtime_status()
        _svc_rows.append({"Service":"FastAPI Event Server :8765","Config":"src/realtime/server.py","Role":"WebSocket broadcast to Live Agent Comms","Status":"✅ connected" if _rs["connected"] else "⚠️ offline"})
    except Exception:
        _svc_rows.append({"Service":"FastAPI Event Server :8765","Config":"src/realtime/server.py","Role":"WebSocket broadcast","Status":"⚠️ unknown"})

    # GCP Logging (only if Cloud Run)
    if _mode == "cloudrun":
        _svc_rows.append({"Service":"GCP Cloud Logging","Config":f"project={os.environ.get('GOOGLE_CLOUD_PROJECT','')}","Role":"Polls sandbox_task.py stdout traces","Status":"✅ configured" if os.environ.get("GOOGLE_CLOUD_PROJECT") else "❌ not configured"})

    _svc_df = _pd_sm.DataFrame(_svc_rows)
    st.dataframe(_svc_df, hide_index=True, use_container_width=True,
        column_config={"Status": st.column_config.TextColumn("Status", width="small")})

    st.divider()

    # ── 3. Active flow topology ───────────────────────────────────────────────
    _sm_section("Active Flow Topology", "⚡")
    st.caption(
        "Every active flow in Graph B with its skill pipeline, connector, and server. "
        "This is what the sandbox actually executes when a proposal is run."
    )
    try:
        _active_flows = run_read(
            """
            MATCH (f:Flow {status:'active'})
            OPTIONAL MATCH (f)-[:USES]->(sk:Skill)
            OPTIONAL MATCH (f)-[:READS_FROM]->(cn:Connector)
            OPTIONAL MATCH (f)-[:RUNS_ON]->(sv:Server)
            RETURN f.id AS flow_id,
                   coalesce(f.name,f.id) AS flow_name,
                   f.avg_outcome_score AS score,
                   f.justification AS justification,
                   collect(DISTINCT sk.id) AS skill_ids,
                   collect(DISTINCT sk.name) AS skill_names,
                   cn.id AS connector_id,
                   cn.type AS connector_type,
                   sv.id AS server_id,
                   sv.current_load AS server_load,
                   last(sv.error_rate_history) AS server_error_rate
            ORDER BY f.id DESC LIMIT 20
            """
        )
        if not _active_flows:
            st.info("No active flows in Graph B. Approve an optimization to activate one.")
        else:
            for _af in _active_flows:
                _skill_str = " -> ".join(s for s in (_af.get("skill_names") or []) if s) or "No skills linked"
                _srv_load  = _af.get("server_load")
                _srv_err   = _af.get("server_error_rate")
                _srv_ok    = (_srv_load or 0) < 80 and (_srv_err or 0) < 0.03

                with st.expander(
                    f"**{_af['flow_name']}**  ·  skills: {_skill_str}  ·  score: {_af.get('score') or '—'}",
                    expanded=False,
                ):
                    _fc1, _fc2 = st.columns(2)
                    with _fc1:
                        st.markdown(f"**Flow ID:** `{_af['flow_id']}`")
                        st.markdown(f"**Skill pipeline:** {ui_value(_skill_str, 'No skills linked')}")
                        st.markdown(
                            f"**Connector:** `{ui_value(_af.get('connector_id'))}` "
                            f"({ui_value(_af.get('connector_type'), 'type unknown')})"
                        )
                    with _fc2:
                        st.markdown(
                            f"**Server:** `{ui_value(_af.get('server_id'))}`  "
                            + ("OK" if _srv_ok else "Review")
                        )
                        if _srv_load is not None:
                            st.progress(min(int(_srv_load),100), text=f"Load {_srv_load}%")
                        if _af.get("justification"):
                            st.caption(_af["justification"])
    except Exception as _fe:
        st.warning(f"Could not load flows: {_fe}")

    st.divider()

    # ── 4. Integration connections (from codebase analysis) ───────────────────
    _sm_section("External Integrations (from codebase)", "🔗")
    st.caption(
        "Integrations detected by static analysis of the connected project's source code. "
        "These are the real third-party services your application uses."
    )
    try:
        _integ = run_read(
            f"""
            MATCH (i:Integration)
            WHERE i.project_id = {json.dumps(project['project_id'])}
            RETURN i.id AS id,
                   coalesce(i.name,i.id) AS name,
                   i.integration_type AS type,
                   i.source_path AS source_path,
                   i.confidence AS confidence
            ORDER BY confidence DESC, name LIMIT 40
            """
        )
        if _integ:
            _int_df = _pd_sm.DataFrame(_integ).rename(columns={
                "name":"Integration","type":"Type","source_path":"Detected in file","confidence":"Confidence"
            })
            _int_df = _int_df[["Integration","Type","Detected in file","Confidence"]].copy()
            for _col in ["Integration", "Type", "Detected in file"]:
                _int_df[_col] = _int_df[_col].apply(lambda v: ui_value(v, "Detected, details unavailable"))
            _int_df["Confidence"] = _pd_sm.to_numeric(_int_df["Confidence"], errors="coerce").fillna(0.0)
            st.dataframe(
                _int_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1, format="%.2f")
                },
            )
        else:
            st.info("No Integration nodes found. Re-run codebase analysis on a project with third-party API calls.")
    except Exception as _ie:
        st.caption(f"Integration query: {_ie}")

    st.divider()

    # ── 5. Agent → tool wiring ────────────────────────────────────────────────
    _sm_section("Agent → Tool Wiring", "🤖")
    st.caption(
        "Which agent node calls which LangChain tools, and which external service each tool touches. "
        "Static mapping derived from the agent source code."
    )
    _wiring = [
        {"Agent":"Planner",        "Tool / Function":"query_graph",            "External service":"Neo4j AuraDB",         "Direction":"READ"},
        {"Agent":"Planner",        "Tool / Function":"query_graph_semantic",    "External service":"Neo4j vector index",   "Direction":"READ"},
        {"Agent":"Planner",        "Tool / Function":"retrieve_context()",      "External service":"Neo4j AuraDB",         "Direction":"READ"},
        {"Agent":"Planner",        "Tool / Function":"ChatGoogleGenerativeAI",  "External service":"Gemini API",           "Direction":"READ"},
        {"Agent":"Generator",      "Tool / Function":"query_graph",             "External service":"Neo4j AuraDB",         "Direction":"READ"},
        {"Agent":"Generator",      "Tool / Function":"get_infrastructure_status","External service":"Neo4j AuraDB",        "Direction":"READ"},
        {"Agent":"Generator",      "Tool / Function":"ChatGoogleGenerativeAI",  "External service":"Gemini API",           "Direction":"READ"},
        {"Agent":"Generator",      "Tool / Function":"create_skill_proposal()", "External service":"Neo4j AuraDB",         "Direction":"WRITE"},
        {"Agent":"Critic",         "Tool / Function":"query_graph",             "External service":"Neo4j AuraDB",         "Direction":"READ"},
        {"Agent":"Critic",         "Tool / Function":"get_infrastructure_status","External service":"Neo4j AuraDB",        "Direction":"READ"},
        {"Agent":"Critic",         "Tool / Function":"ChatGoogleGenerativeAI",  "External service":"Gemini API",           "Direction":"READ"},
        {"Agent":"Simulator",      "Tool / Function":"simulate_flow",           "External service":"sandbox_task.py / GCP","Direction":"EXEC"},
        {"Agent":"Simulator",      "Tool / Function":"log_execution_trace()",   "External service":"Neo4j AuraDB",         "Direction":"WRITE"},
        {"Agent":"Evaluator",      "Tool / Function":"propose_change",          "External service":"Neo4j AuraDB",         "Direction":"WRITE"},
        {"Agent":"Evaluator",      "Tool / Function":"ChatGoogleGenerativeAI",  "External service":"Gemini API",           "Direction":"READ"},
        {"Agent":"Human Approval", "Tool / Function":"activate_proposal()",     "External service":"Neo4j AuraDB",         "Direction":"WRITE"},
        {"Agent":"Human Approval", "Tool / Function":"log_learning_event()",    "External service":"Neo4j AuraDB",         "Direction":"WRITE"},
        {"Agent":"Human Approval", "Tool / Function":"interrupt()",             "External service":"LangGraph checkpointer","Direction":"STATE"},
    ]
    _wd = _pd_sm.DataFrame(_wiring)
    _dir_map = {"READ":"🔵","WRITE":"🟠","EXEC":"🟢","STATE":"🟣"}
    _wd["Direction"] = _wd["Direction"].apply(lambda d: f"{_dir_map.get(d,'')} {d}")
    st.dataframe(_wd, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Page: Sandbox
# Full sandbox environment: configuration, run, compare, trace history.
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Sandbox":
    import pandas as _pd_sb

    st.subheader("Sandbox")
    st.caption(
        "Operational sandbox controls for database snapshots, Cloud Run execution, "
        "and approved flow testing before any real source-code deployment."
    )

    _mock_on = os.environ.get("SANDBOX_MOCK", "true").lower() == "true"
    _sb_mode = os.environ.get("SANDBOX_MODE", "local")
    _gcp_proj = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    _sb_region = os.environ.get("SANDBOX_GCP_REGION") or os.environ.get("GOOGLE_CLOUD_LOCATION") or "not set"
    _sb_job = os.environ.get("SANDBOX_JOB_NAME", "")

    _cfg_col1, _cfg_col2, _cfg_col3, _cfg_col4 = st.columns(4)
    _cfg_col1.metric("Default Mode", "Mock" if _mock_on else _sb_mode.title())
    _cfg_col2.metric("Local Engine", "available")
    _cfg_col3.metric("GCP Project", _gcp_proj[:24] or "not set")
    _cfg_col4.metric("Cloud Run Job", _sb_job or "not set")

    sandbox_section = st.radio(
        "Sandbox section",
        ["Database Credentials", "Cloud Run Sandbox", "Approved Flows + Test + Deploy"],
        horizontal=True,
        key="sandbox_section",
        label_visibility="collapsed",
    )

    if sandbox_section == "Database Credentials":
        # Status banner — stays visible whenever a snapshot is already loaded this session
        _db_snap_ready = st.session_state.get("sb_db_snapshot_result")
        if _db_snap_ready and _db_snap_ready.get("status") == "success":
            _snap_meta = _db_snap_ready.get("metadata") or {}
            st.success(
                f"Snapshot ready — {_snap_meta.get('table_count', '?')} tables imported "
                f"from `{_db_snap_ready.get('connection', '')}`. "
                "Sandbox tests in this session will use this snapshot."
            )

        # Step 1 — detect databases from the indexed project graph
        st.markdown("**Step 1 — Detected databases**")
        st.caption("Databases are detected automatically from the indexed project graph.")
        db_assets = load_project_database_assets(project["project_id"]) if project else pd.DataFrame()

        if db_assets.empty:
            st.info(
                "No database nodes (DataStore / DatabaseModel / DatabaseTable) found for this project. "
                "Run `python -m src.indexer.runner` to index it, or connect manually below."
            )
            _manual_override = st.toggle("Connect to a database manually", key="sb_db_manual_override")
        else:
            display_table(db_assets, height=200)
            _manual_override = True

        if _manual_override:
            st.divider()
            # Step 2 — credentials
            st.markdown("**Step 2 — Enter credentials**")
            db_names = db_assets["target"].astype(str).tolist() if not db_assets.empty else ["Manual entry"]
            selected_db = st.selectbox("Database target", db_names, key="sb_db_target")
            conn_uri = st.text_input(
                "SQLAlchemy connection URI",
                value=st.session_state.get(f"sb_db_uri_{selected_db}", ""),
                type="password",
                key=f"sb_db_uri_input_{selected_db}",
                placeholder="postgresql+psycopg://user:password@host:5432/db  or  sqlite:////absolute/path.db",
            )
            sample_query = st.text_area(
                "Optional read-only sample query",
                value="",
                height=80,
                key=f"sb_db_query_{selected_db}",
                placeholder="SELECT * FROM your_table LIMIT 20",
            )
            row_limit = st.slider("Preview row limit", 1, 100, 20, key=f"sb_db_limit_{selected_db}")

            st.divider()
            # Step 3 — test and import
            st.markdown("**Step 3 — Test and import**")
            if st.button("Test Connection + Import Snapshot", type="primary", key=f"sb_db_test_{selected_db}"):
                st.session_state[f"sb_db_uri_{selected_db}"] = conn_uri
                if not conn_uri.strip():
                    st.warning("Enter a connection URI first.")
                else:
                    with st.spinner("Connecting and importing schema..."):
                        st.session_state["sb_db_snapshot_result"] = inspect_database_connection(
                            conn_uri,
                            sample_query,
                            row_limit,
                            [str(project.get("repo_path") or "")] if project else None,
                        )
                    st.rerun()

            db_snapshot = st.session_state.get("sb_db_snapshot_result")
            if db_snapshot:
                if db_snapshot.get("status") == "success":
                    meta = db_snapshot.get("metadata") or {}
                    if meta.get("normalized_note"):
                        st.caption(meta["normalized_note"])
                    m1, m2 = st.columns(2)
                    m1.metric("Tables imported", meta.get("table_count", 0))
                    m2.metric("Row preview", meta.get("row_preview_count", 0))
                    schema = db_snapshot.get("schema") or []
                    rows = db_snapshot.get("rows") or []
                    if schema:
                        st.markdown("**Schema**")
                        schema_columns = schema_columns_table(schema)
                        if not schema_columns.empty:
                            display_table(schema_columns, height=300)
                        with st.expander("Raw schema metadata", expanded=False):
                            st.json(schema)
                    if rows:
                        st.markdown("**Row preview**")
                        display_table(pd.DataFrame(rows), height=240)
                else:
                    st.error(db_snapshot.get("error") or "Database connection failed.")

    elif sandbox_section == "Cloud Run Sandbox":
        # ── Section 1: Deployed container URL ────────────────────────────────
        st.markdown("**Deployed Container**")
        st.caption(
            "Container URLs are stored on approved Flow nodes in Neo4j. "
            "They are set after deployment via the form below, or can be updated at any time."
        )

        # Load active/approved flows and find any that have a container_url
        _cr_all_flows = load_flows()
        _cr_active = _cr_all_flows[_cr_all_flows["status"].fillna("").isin(["active", "approved"])].copy()
        if project and not _cr_active.empty:
            _cr_proj = _cr_active[_cr_active["project_id"].fillna("") == project["project_id"]]
            _cr_active = _cr_proj if not _cr_proj.empty else _cr_active

        _flows_with_url = (
            _cr_active[_cr_active["container_url"].fillna("").str.strip().ne("")]
            if "container_url" in _cr_active.columns and not _cr_active.empty
            else pd.DataFrame()
        )

        if not _flows_with_url.empty:
            for _, _fc_row in _flows_with_url.iterrows():
                _fc_url = str(_fc_row["container_url"]).strip()
                _fc_label = str(_fc_row.get("name") or _fc_row["id"])
                st.link_button(
                    f"Open deployed container — {_fc_label}",
                    _fc_url,
                    use_container_width=True,
                )
        else:
            st.info(
                "No container URL is stored for any active flow in this project. "
                "Approve a proposal, deploy it, then set the URL below."
            )

        # Form to set / update a container URL on any active flow
        if not _cr_active.empty:
            with st.expander("Set container URL on an approved flow", expanded=_flows_with_url.empty):
                _url_opts = {
                    str(r["id"]): str(r.get("name") or r["id"])
                    for _, r in _cr_active.iterrows()
                }
                _url_sel = st.selectbox(
                    "Flow",
                    list(_url_opts.keys()),
                    format_func=lambda fid: _url_opts[fid],
                    key="sb_cr_url_flow_sel",
                )
                _url_current = ""
                if "container_url" in _cr_active.columns:
                    _url_match = _cr_active[_cr_active["id"] == _url_sel]
                    if not _url_match.empty:
                        _url_current = str(_url_match.iloc[0].get("container_url") or "")
                _url_new = st.text_input(
                    "Container URL",
                    value=_url_current,
                    placeholder="https://my-service-abc123.run.app",
                    key="sb_cr_url_input",
                )
                if st.button("Save to Neo4j", key="sb_cr_url_save", disabled=not _url_new.strip()):
                    set_flow_container_url(_url_sel, _url_new.strip())
                    load_flows.clear()
                    st.success(f"Container URL saved for flow {_url_opts.get(_url_sel, _url_sel)}.")
                    st.rerun()

        st.divider()

        # ── Section 2: Cloud Run Job config ──────────────────────────────────
        st.markdown("**Cloud Run Job Configuration**")
        st.caption("Sandbox execution via Google Cloud Run Jobs — configured through environment variables.")
        configured = bool(_gcp_proj and _sb_job and _sb_region != "not set")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Configured", "Yes" if configured else "No")
        c2.metric("Project", _gcp_proj or "not set")
        c3.metric("Region", _sb_region)
        c4.metric("Job", _sb_job or "not set")
        job_url = cloud_run_job_url()
        if job_url:
            st.link_button("Open Cloud Run Job in GCP Console", job_url, use_container_width=True)
        else:
            st.warning(
                "Set GOOGLE_CLOUD_PROJECT, SANDBOX_GCP_REGION, and SANDBOX_JOB_NAME in .env "
                "to enable the Cloud Run link."
            )

        st.markdown("**Smoke test**")
        st.caption("Runs the default sandbox flow through the configured Cloud Run job.")
        if st.button("Test Cloud Run Sandbox", type="primary", key="sb_cloud_test", disabled=not configured):
            with st.spinner("Triggering Cloud Run sandbox job..."):
                st.session_state["sb_cloud_test_result"] = run_sandbox_from_ui(default_sandbox_flow(), "cloudrun")
        cloud_result = st.session_state.get("sb_cloud_test_result")
        if cloud_result:
            if cloud_result.get("status") == "success":
                st.success("Cloud Run sandbox completed successfully.")
                metrics = cloud_result.get("metrics") or {}
                cm1, cm2, cm3 = st.columns(3)
                cm1.metric("Score", metrics.get("match_score", "—"))
                cm2.metric("Baseline", metrics.get("sandbox_baseline_score", "—"))
                cm3.metric("Latency ms", metrics.get("latency_ms", "—"))
            else:
                st.error(cloud_result.get("error_log") or "Cloud Run sandbox failed.")
                if cloud_result.get("infra_error"):
                    st.json(cloud_result["infra_error"])

        st.divider()

        # ── Section 3: Neo4j → Sandbox Snapshot ──────────────────────────────
        st.markdown("**Neo4j → Sandbox Snapshot**")
        st.caption(
            "Builds the exact payload that Cloud Run receives — same field selection, "
            "same limits, and secrets stripped via the same sanitizer used at runtime."
        )

        if st.button("Build Snapshot from Neo4j", key="sb_neo4j_snapshot_build"):
            try:
                _snap = _build_snapshot()
                _snap["_meta"] = {
                    "source": "neo4j-live",
                    "project": _gcp_proj,
                    "built_at": str(pd.Timestamp.utcnow()),
                }
                st.session_state["sb_neo4j_snapshot"] = _snap
            except Exception as _snap_err:
                st.error(f"Could not build snapshot: {_snap_err}")

        _snap_data = st.session_state.get("sb_neo4j_snapshot")
        if _snap_data:
            sn1, sn2 = st.columns(2)
            sn1.metric("Companies", len(_snap_data.get("companies", [])))
            sn2.metric("Mentors",   len(_snap_data.get("mentors", [])))

            _snap_json = json.dumps(_snap_data, indent=2, default=str)
            st.download_button(
                "Download snapshot as JSON",
                data=_snap_json,
                file_name="neo4j_sandbox_snapshot.json",
                mime="application/json",
                key="sb_neo4j_snapshot_dl",
            )
            with st.expander("Preview snapshot", expanded=False):
                st.json(_snap_data)

    elif sandbox_section == "Approved Flows + Test + Deploy":
        st.caption(
            "Test approved optimization flows in the sandbox, then deploy code patches "
            "to the real project. Deployment requires concrete code patches and a passing sandbox test."
        )
        flows = load_flows()
        if not flows.empty:
            flows = flows[flows["status"].fillna("").isin(["active", "approved"])].copy()
            if project:
                project_flows = flows[flows["project_id"].fillna("") == project["project_id"]].copy()
                if not project_flows.empty:
                    flows = project_flows
                else:
                    st.info("No approved flows are tied to the selected project — showing all approved flows.")
        if flows.empty:
            st.info("No approved or active flows found. Run the agent and approve a proposal first.")
        else:
            _merge_flash = st.session_state.pop("sb_merge_registry_flash", None)
            if _merge_flash:
                render_merge_success_panel(_merge_flash)

            _mode = "cloudrun"
            _gcp_configured = bool(_gcp_proj and _sb_job)
            if _gcp_configured:
                st.info("Sandbox mode: Cloud Run. Tests are executed in the configured GCP Cloud Run job.")
            else:
                st.warning(
                    "Cloud Run is not configured — set GOOGLE_CLOUD_PROJECT and SANDBOX_JOB_NAME "
                    "in .env to enable sandbox tests."
                )

            # Helper badges derived from session_state so the summary table stays live
            def _test_badge(fid: str) -> str:
                r = st.session_state.get(f"sb_flow_test_result_{fid}")
                if not r:
                    return "Not tested"
                return "Passed" if r.get("status") == "success" else "Failed"

            def _deploy_badge(fid: str) -> str:
                r = st.session_state.get(f"sb_flow_deploy_result_{fid}")
                if not r:
                    return "—"
                return "Deployed" if r.get("status") == "success" else "Failed"

            # Summary table — one row per flow, all key state visible at a glance
            _summary_rows = []
            for _, _sf in flows.iterrows():
                _sfid = str(_sf["id"])
                _summary_rows.append({
                    "Name": str(_sf.get("name") or _sfid),
                    "Status": str(_sf.get("status") or "—"),
                    "Score": _sf.get("avg_score") or "—",
                    "Patches": len(code_patches_from_config(_sf.get("yaml_config"))),
                    "Test": _test_badge(_sfid),
                    "Deploy": _deploy_badge(_sfid),
                    "Container URL": str(_sf.get("container_url") or "—") if "container_url" in _sf.index else "—",
                })
            display_table(pd.DataFrame(_summary_rows), height=220)
            st.divider()

            # Per-flow detail expanders
            for _, flow in flows.iterrows():
                flow_id = str(flow["id"])
                flow_name = str(flow.get("name") or flow_id)
                flow_yaml = flow_yaml_from_config(flow.get("yaml_config"))
                patches = code_patches_from_config(flow.get("yaml_config"))
                test_key = f"sb_flow_test_result_{flow_id}"
                deploy_key = f"sb_flow_deploy_result_{flow_id}"
                active_test_key = "sb_active_flow_test_id"
                running_key = f"sb_flow_test_running_{flow_id}"
                test_badge = _test_badge(flow_id)
                deploy_badge = _deploy_badge(flow_id)
                result = st.session_state.get(test_key)
                expanded = (
                    st.session_state.get(active_test_key) == flow_id
                    or result is not None
                    or test_badge != "Not tested"
                )

                with st.expander(
                    f"{flow_name}  ·  {flow.get('status')}  ·  Test: {test_badge}  ·  {len(patches)} patch(es)",
                    expanded=expanded,
                ):
                    fc1, fc2, fc3, fc4 = st.columns(4)
                    fc1.metric("Score", flow.get("avg_score") or "—")
                    fc2.metric("BusinessFlow", flow.get("business_flow_id") or "—")
                    fc3.metric("Test", test_badge)
                    fc4.metric("Deploy", deploy_badge)

                    if flow.get("justification"):
                        st.info(flow["justification"])
                    if flow_yaml:
                        with st.expander("View flow YAML", expanded=False):
                            st.code(flow_yaml, language="yaml")
                    else:
                        st.warning("No flow YAML stored — this flow cannot be sandbox-tested.")

                    # ── Test ──────────────────────────────────────────────────
                    st.markdown("**Test in sandbox**")
                    render_sandbox_run_monitor(
                        flow_id=flow_id,
                        flow_name=flow_name,
                        result=result,
                        running=bool(st.session_state.get(running_key)),
                    )
                    if st.button(
                        "Run sandbox test",
                        key=f"sb_test_{flow_id}",
                        disabled=not bool(flow_yaml) or not _gcp_configured,
                        type="primary",
                    ):
                        st.session_state[active_test_key] = flow_id
                        st.session_state[running_key] = True
                        _thread_id = f"sandbox-{uuid.uuid4().hex[:8]}"
                        publish_event(
                            thread_id=_thread_id,
                            source="ui",
                            target="simulator",
                            event_type="started",
                            title="Approved flow sandbox requested",
                            detail=flow_name,
                            payload={"flow_id": flow_id, "mode": _mode},
                        )
                        status_box = st.status(
                            f"Running Cloud Run sandbox for {flow_name}",
                            expanded=True,
                            state="running",
                        )
                        with status_box:
                            _sb_project_id = str(flow.get("project_id") or "")
                            _job_url = cloud_run_job_url()
                            st.write("1. Preparing flow YAML and scoped sandbox snapshot.")
                            st.write("2. Minting capability token for this sandbox run.")
                            st.write("3. Uploading source bundle and starting the Cloud Run Job.")
                            cfg1, cfg2, cfg3 = st.columns(3)
                            cfg1.metric("GCP project", _gcp_proj or "not set")
                            cfg2.metric("Region", _sb_region)
                            cfg3.metric("Job", _sb_job or "not set")
                            if _job_url:
                                st.link_button(
                                    "Open Cloud Run job while this runs",
                                    _job_url,
                                    use_container_width=True,
                                )
                            started_at = time.time()
                            try:
                                _result = run_sandbox_from_ui(
                                    flow_yaml, _mode, project_id=_sb_project_id or None
                                )
                            except Exception as exc:
                                _result = {
                                    "status": "fail",
                                    "metrics": {},
                                    "error_log": str(exc),
                                    "run": {
                                        "execution_mode": _mode,
                                        "project_id": _sb_project_id,
                                        "stage": "ui_exception",
                                    },
                                }
                            _result.setdefault("ui", {})
                            _result["ui"].update(
                                {
                                    "flow_id": flow_id,
                                    "flow_name": flow_name,
                                    "started_at": started_at,
                                    "finished_at": time.time(),
                                    "duration_ms": round((time.time() - started_at) * 1000),
                                }
                            )
                            _run_meta = _result.get("run") if isinstance(_result.get("run"), dict) else {}
                            render_cloud_run_console_links(_run_meta)
                            st.write("4. Cloud Run finished; parsing Cloud Logging for sandbox traces.")
                            if _result.get("status") == "success":
                                _m = _result.get("metrics") or {}
                                s1, s2, s3 = st.columns(3)
                                s1.metric("Match score", _m.get("match_score", "n/a"))
                                s2.metric("Baseline", _m.get("sandbox_baseline_score", "n/a"))
                                s3.metric("Latency", f"{_m.get('latency_ms', 'n/a')} ms")
                            else:
                                st.error(_result.get("error_log") or "Sandbox failed before metrics were returned.")
                                if _result.get("infra_error"):
                                    st.json(_result["infra_error"])
                            st.session_state[test_key] = _result
                            st.session_state[running_key] = False
                            if _result.get("status") == "success":
                                status_box.update(label="Sandbox completed", state="complete", expanded=True)
                                publish_event(
                                    thread_id=_thread_id,
                                    source="simulator",
                                    target="evaluator",
                                    event_type="result",
                                    title="Approved flow sandbox completed",
                                    detail=flow_name,
                                    payload={
                                        "flow_id": flow_id,
                                        "metrics": _result.get("metrics", {}),
                                        "run": _result.get("run", {}),
                                    },
                                )
                            else:
                                status_box.update(label="Sandbox failed", state="error", expanded=True)
                                publish_event(
                                    thread_id=_thread_id,
                                    source="simulator",
                                    event_type="error",
                                    title="Approved flow sandbox failed",
                                    detail=_result.get("error_log") or flow_name,
                                    payload={
                                        "flow_id": flow_id,
                                        "run": _result.get("run", {}),
                                        "infra_error": _result.get("infra_error"),
                                    },
                                )
                        st.rerun()

                    result = st.session_state.get(test_key)
                    passed = bool(result and result.get("status") == "success")
                    if result:
                        metrics = result.get("metrics") or {}

                        # Delta + verdict row
                        _sb_opt = metrics.get("match_score")
                        _sb_base = metrics.get("sandbox_baseline_score")
                        if _sb_opt is not None and _sb_base is not None:
                            _sb_delta = round(float(_sb_opt) - float(_sb_base), 2)
                            _sb_verdict = "Same or better ✓" if _sb_delta >= 0 else "Degraded ✗"
                            rd1, rd2 = st.columns(2)
                            rd1.metric("Improvement over baseline", _sb_delta, delta=_sb_delta)
                            rd2.metric("Verdict", _sb_verdict)
                            if _sb_delta >= 0:
                                st.success(
                                    f"Optimized flow scores **{_sb_opt}** vs random baseline **{_sb_base}** "
                                    f"(+{_sb_delta}). Meets the same-or-better requirement."
                                )
                            else:
                                st.warning(
                                    f"Optimized flow scores **{_sb_opt}** vs random baseline **{_sb_base}** "
                                    f"({_sb_delta}). Consider reviewing the flow steps."
                                )

                        # Merge to Registry — promotes the tested flow to active in Neo4j
                        st.divider()
                        st.markdown("**Merge to Registry**")
                        st.caption(
                            "Marks this flow as active in the Neo4j registry so it becomes "
                            "the canonical live flow for its business context."
                        )
                        if st.button(
                            "Merge to Registry (set active)",
                            key=f"sb_merge_{flow_id}",
                            type="primary",
                            disabled=not passed,
                        ):
                            activated = activate_proposal(
                                flow_id,
                                merged_by="sandbox_ui",
                                merge_source="approved_flow_sandbox",
                            )
                            if not activated:
                                st.error(
                                    f"Flow `{flow_id}` was not found in Neo4j, so nothing was merged."
                                )
                                st.stop()
                            publish_event(
                                source="human_approval",
                                event_type="merged",
                                title="Flow merged to registry",
                                detail=flow_id,
                                payload={
                                    "flow_id": flow_id,
                                    "flow_name": activated.get("name") or flow_name,
                                    "status": activated.get("status"),
                                },
                            )
                            load_flows.clear()
                            st.session_state["sb_merge_registry_flash"] = {
                                **activated,
                                "flow_id": flow_id,
                                "flow_name": activated.get("name") or flow_name,
                            }
                            st.rerun()

                    # ── Deploy ────────────────────────────────────────────────
                    st.markdown("**Deploy to real code**")
                    if not patches:
                        st.warning(
                            "No code patches in this flow. Generate and approve a flow with "
                            "`modify_code` actions to enable deployment."
                        )
                    else:
                        patch_preview = [
                            {
                                "file": p.get("file_path"),
                                "description": p.get("description"),
                                "chars removed": len(str(p.get("old_code") or "")),
                                "chars added": len(str(p.get("new_code") or "")),
                            }
                            for p in patches
                        ]
                        display_table(pd.DataFrame(patch_preview), height=140)
                        repo_root = str(project.get("repo_path") or "") if project else ""
                        confirm = st.text_input(
                            f"Type  DEPLOY {flow_id}  to confirm applying patches to `{repo_root}`",
                            key=f"sb_deploy_confirm_{flow_id}",
                        )
                        deploy_disabled = not passed or confirm.strip() != f"DEPLOY {flow_id}"
                        if deploy_disabled and passed:
                            st.caption("Type the confirmation token above to unlock deployment.")
                        if st.button(
                            "Deploy to Real Code",
                            type="primary",
                            key=f"sb_deploy_{flow_id}",
                            disabled=deploy_disabled,
                        ):
                            st.session_state[deploy_key] = apply_code_patches_to_repo(repo_root, patches)
                            st.rerun()

                        deploy_result = st.session_state.get(deploy_key)
                        if deploy_result:
                            if deploy_result.get("status") == "success":
                                st.success(
                                    f"Deployed — {len(deploy_result.get('applied', []))} patch(es) applied."
                                )
                                display_table(pd.DataFrame(deploy_result.get("applied", [])), height=140)
                                # Post-deploy: save the container URL so it appears in the Cloud Run tab
                                st.markdown("**Set container URL** *(saves to Cloud Run Sandbox tab)*")
                                _post_url_current = (
                                    str(flow.get("container_url") or "")
                                    if "container_url" in flow.index else ""
                                )
                                _post_url = st.text_input(
                                    "Container URL",
                                    value=_post_url_current,
                                    placeholder="https://my-service.run.app",
                                    key=f"sb_post_deploy_url_{flow_id}",
                                )
                                if st.button(
                                    "Save container URL",
                                    key=f"sb_post_deploy_url_save_{flow_id}",
                                    disabled=not _post_url.strip(),
                                ):
                                    set_flow_container_url(flow_id, _post_url.strip())
                                    load_flows.clear()
                                    st.success("Container URL saved to Neo4j.")
                                    st.rerun()
                            else:
                                st.error(deploy_result.get("error") or "Deployment failed.")
