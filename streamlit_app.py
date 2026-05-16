from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.agents.tools import (
    activate_proposal,
    query_graph,
    reject_proposal,
    verify_neo4j_connection,
)


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

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


def clear_data_cache() -> None:
    st.cache_data.clear()


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


with st.sidebar:
    st.markdown("## EcoLink")
    page = st.radio(
        "View",
        ["Command Center", "Agent Run", "Flows", "Proposals", "Infrastructure", "History"],
        label_visibility="collapsed",
    )
    if st.button("Refresh Data", width="stretch"):
        clear_data_cache()
        st.rerun()


st.title("EcoLink NeuroCore")
st.markdown(
    "<span class='status-pill status-good'>Neo4j</span>"
    "<span class='status-pill status-warn'>Mock Sandbox</span>"
    "<span class='status-pill'>LangGraph Agent</span>",
    unsafe_allow_html=True,
)

try:
    verify_neo4j_connection()
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()


overview = load_overview()

if page == "Command Center":
    cols = st.columns(6)
    cols[0].metric("Companies", overview.get("companies", 0))
    cols[1].metric("Mentors", overview.get("mentors", 0))
    cols[2].metric("Flows", overview.get("flows", 0))
    cols[3].metric("Servers", overview.get("servers", 0))
    cols[4].metric("Avg Score", overview.get("avg_match_score", 0))
    cols[5].metric("Pending", overview.get("proposed", 0))

    left, right = st.columns([1.25, 1])
    with left:
        st.subheader("Flow Portfolio")
        flows = load_flows()
        visible = flows[["id", "status", "avg_score", "connector", "server", "skills"]]
        display_table(visible, height=320)
    with right:
        st.subheader("Recent Execution Traces")
        display_table(load_traces(), height=320)

    st.subheader("Lowest Historical Matches")
    display_table(load_matches(), height=340)

elif page == "Agent Run":
    st.subheader("Run Optimization")
    default_goal = "Improve match quality for Healthtech startups"
    goal = st.text_input("Goal", value=default_goal)
    if st.button("Run Agent", type="primary"):
        with st.spinner("Planner, generator, critic, simulator, evaluator..."):
            try:
                code, stdout, stderr, thread_id = run_agent(goal)
            except subprocess.TimeoutExpired:
                st.error("Agent run timed out after 240 seconds.")
            else:
                clear_data_cache()
                if thread_id:
                    st.session_state["last_thread_id"] = thread_id
                if code == 0:
                    st.success("Agent run completed.")
                else:
                    st.warning("Agent run stopped before a clean exit.")
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
    display_table(flows, height=520)

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
                clear_data_cache()
                st.success(f"Approved {row['id']}")
                st.rerun()
        with c2:
            if st.button("Reject", key=f"reject_{row['id']}"):
                reject_proposal(row["id"], "Rejected in Streamlit dashboard")
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
