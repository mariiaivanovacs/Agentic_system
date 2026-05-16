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
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv

from src.agents.tools import (
    activate_proposal,
    log_execution_trace,
    query_graph,
    reject_proposal,
    simulate_flow,
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


@st.cache_data(ttl=20)
def load_graph_payload(limit: int = 180) -> dict[str, list[dict[str, Any]]]:
    rows = run_read(
        f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN [
            'Company', 'Mentor', 'Flow', 'Skill', 'Connector', 'Server',
            'ExecutionTrace', 'Outcome', 'Programme'
        ])
        WITH n LIMIT {limit}
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE any(label IN labels(m) WHERE label IN [
            'Company', 'Mentor', 'Flow', 'Skill', 'Connector', 'Server',
            'ExecutionTrace', 'Outcome', 'Programme'
        ])
        RETURN elementId(n) AS source_id,
               labels(n) AS source_labels,
               coalesce(n.id, n.name, elementId(n)) AS source_name,
               n.status AS source_status,
               n.avg_outcome_score AS source_score,
               type(r) AS rel_type,
               elementId(m) AS target_id,
               labels(m) AS target_labels,
               coalesce(m.id, m.name, elementId(m)) AS target_name,
               m.status AS target_status,
               m.avg_outcome_score AS target_score
        """
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for row in rows:
        nodes[row["source_id"]] = {
            "id": row["source_id"],
            "label": str(row["source_name"]),
            "group": row["source_labels"][0] if row["source_labels"] else "Node",
            "status": row.get("source_status"),
            "score": row.get("source_score"),
        }
        if row.get("target_id"):
            nodes[row["target_id"]] = {
                "id": row["target_id"],
                "label": str(row["target_name"]),
                "group": row["target_labels"][0] if row["target_labels"] else "Node",
                "status": row.get("target_status"),
                "score": row.get("target_score"),
            }
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


def graph_html(payload: dict[str, list[dict[str, Any]]]) -> str:
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
    }

    nodes = []
    for node in payload["nodes"]:
        status = node.get("status")
        label = node["label"]
        title = f"{node['group']}<br>{label}"
        if status:
            title += f"<br>Status: {status}"
        if node.get("score") is not None:
            title += f"<br>Score: {node['score']}"
        nodes.append(
            {
                "id": node["id"],
                "label": label[:34],
                "group": node["group"],
                "title": title,
                "shape": "dot",
                "size": 18 if node["group"] in {"Flow", "Company", "Mentor"} else 13,
            }
        )

    html = f"""
    <div id="network" style="height: 680px; border: 1px solid #d8d1c2; border-radius: 8px; background: #fffaf0;"></div>
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <script>
      const nodes = new vis.DataSet({json.dumps(nodes)});
      const edges = new vis.DataSet({json.dumps(payload["edges"])});
      const groups = {json.dumps(groups)};
      const container = document.getElementById("network");
      const data = {{ nodes, edges }};
      const options = {{
        groups,
        interaction: {{ hover: true, navigationButtons: true, keyboard: true }},
        physics: {{
          solver: "forceAtlas2Based",
          forceAtlas2Based: {{ gravitationalConstant: -55, springLength: 120 }},
          stabilization: {{ iterations: 140 }}
        }},
        nodes: {{
          font: {{ face: "Georgia", size: 14, color: "#19211f" }},
          borderWidth: 2
        }},
        edges: {{
          arrows: {{ to: {{ enabled: true, scaleFactor: 0.55 }} }},
          color: {{ color: "#9c927f", highlight: "#0f7b63" }},
          font: {{ size: 10, align: "middle", color: "#574f43" }},
          smooth: {{ type: "dynamic" }}
        }}
      }};
      new vis.Network(container, data, options);
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
            "Command Center",
            "Graph View",
            "Agent Run",
            "Sandbox",
            "Flows",
            "Proposals",
            "Infrastructure",
            "History",
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
st.markdown(
    "<span class='status-pill status-good'>Neo4j</span>"
    f"<span class='status-pill status-warn'>{sandbox_label}</span>"
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

elif page == "Graph View":
    st.subheader("Dual Graph")
    graph_scope = st.radio(
        "Graph scope",
        ["Dual graph", "Graph A: History", "Graph B: Code and Infrastructure", "Bridge: Execution traces"],
        horizontal=True,
    )
    limit = st.slider("Node limit", min_value=40, max_value=240, value=180, step=20)
    payload = load_graph_payload(limit)

    if graph_scope != "Dual graph":
        groups_by_scope = {
            "Graph A: History": {"Company", "Mentor", "Programme"},
            "Graph B: Code and Infrastructure": {"Flow", "Skill", "Connector", "Server"},
            "Bridge: Execution traces": {"ExecutionTrace", "Outcome", "Flow"},
        }
        allowed = groups_by_scope[graph_scope]
        allowed_ids = {n["id"] for n in payload["nodes"] if n["group"] in allowed}
        payload = {
            "nodes": [n for n in payload["nodes"] if n["id"] in allowed_ids],
            "edges": [
                e for e in payload["edges"]
                if e["from"] in allowed_ids and e["to"] in allowed_ids
            ],
        }

    c1, c2, c3 = st.columns(3)
    c1.metric("Nodes", len(payload["nodes"]))
    c2.metric("Relationships", len(payload["edges"]))
    c3.metric("Pending Proposals", overview.get("proposed", 0))
    components.html(graph_html(payload), height=700)

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
        with st.spinner(f"Creating {mode} sandbox run..."):
            result = run_sandbox_from_ui(flow_yaml, mode)
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
