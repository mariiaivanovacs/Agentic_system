from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import threading
import queue
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

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

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

* { font-family: 'Inter', sans-serif; }

.stApp {
    background:
        linear-gradient(90deg, rgba(25,33,31,.04) 1px, transparent 1px),
        linear-gradient(180deg, rgba(25,33,31,.035) 1px, transparent 1px),
        var(--paper);
    background-size: 26px 26px;
    color: var(--ink);
}

h1 { font-family: Georgia, serif; font-size: 2.2rem; color: var(--ink); margin-bottom: 0.2rem; }
h2, h3 { color: var(--ink); }

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a1f1d 0%, #201f1b 100%);
    border-right: 1px solid #2e2c28;
}
section[data-testid="stSidebar"] * { color: #f7f1e4; }

div[data-testid="stMetric"] {
    background: rgba(255,250,240,.95);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 16px 16px 12px;
    box-shadow: 0 4px 14px rgba(33,30,22,.06);
    transition: box-shadow 0.2s;
}
div[data-testid="stMetric"]:hover { box-shadow: 0 8px 24px rgba(33,30,22,.1); }

div[data-testid="stDataFrame"] {
    border: 1px solid var(--line);
    border-radius: 10px;
    overflow: hidden;
}

.status-pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: #fffaf0;
    font-size: .8rem;
    margin-right: 6px;
    font-weight: 500;
}
.status-good { color: var(--good); border-color: rgba(22,116,71,.35); background: #f0faf5; }
.status-warn { color: var(--warn); border-color: rgba(165,91,25,.35); background: #fdf6f0; }
.status-bad  { color: var(--bad);  border-color: rgba(167,55,55,.35); background: #fdf0f0; }

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

/* Agent activity log */
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
.agent-log .step-planner  { color: #7dd3fc; }
.agent-log .step-generator{ color: #86efac; }
.agent-log .step-critic   { color: #fbbf24; }
.agent-log .step-simulator{ color: #c4b5fd; }
.agent-log .step-done     { color: #34d399; font-weight: 700; }
.agent-log .step-error    { color: #f87171; }
</style>
""", unsafe_allow_html=True)


# ── HELPERS ───────────────────────────────────────────────────────────────────
def run_read(cypher: str) -> list[dict[str, Any]]:
    return query_graph.invoke({"cypher_query": cypher})

def df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=20)
def load_overview() -> dict[str, Any]:
    counts = run_read("""
        MATCH (c:Company) WITH count(c) AS companies
        MATCH (m:Mentor) WITH companies, count(m) AS mentors
        MATCH (f:Flow) WITH companies, mentors, count(f) AS flows
        MATCH (s:Server) WITH companies, mentors, flows, count(s) AS servers
        RETURN companies, mentors, flows, servers
    """)
    avg = run_read("""
        MATCH (:Company)-[r:MATCHED_WITH]->(:Mentor)
        RETURN round(avg(r.outcome_score), 2) AS avg_match_score,
               count(r) AS historical_matches
    """)
    proposed = run_read("MATCH (f:Flow {status: 'proposed'}) RETURN count(f) AS proposed")
    traces   = run_read("MATCH (et:ExecutionTrace) RETURN count(et) AS traces")
    return {
        **(counts[0] if counts else {}),
        **(avg[0]    if avg    else {}),
        "proposed": proposed[0]["proposed"] if proposed else 0,
        "traces":   traces[0]["traces"]     if traces   else 0,
    }


@st.cache_data(ttl=20)
def load_flows() -> pd.DataFrame:
    return df(run_read("""
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
    """))


@st.cache_data(ttl=20)
def load_servers() -> pd.DataFrame:
    return df(run_read("""
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
    """))


@st.cache_data(ttl=20)
def load_traces() -> pd.DataFrame:
    return df(run_read("""
        MATCH (et:ExecutionTrace)-[:RAN_FLOW]->(f:Flow)
        OPTIONAL MATCH (et)-[:RESULTED_IN]->(o:Outcome)
        RETURN et.id AS trace_id,
               f.id AS flow_id,
               et.status AS status,
               o.score AS score,
               toString(et.timestamp) AS timestamp
        ORDER BY timestamp DESC
        LIMIT 50
    """))


@st.cache_data(ttl=20)
def load_matches() -> pd.DataFrame:
    return df(run_read("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        RETURN c.name AS company,
               c.industry AS industry,
               m.name AS mentor,
               r.outcome_score AS score,
               r.feedback AS feedback,
               r.programme_name AS programme
        ORDER BY r.outcome_score ASC
        LIMIT 30
    """))


@st.cache_data(ttl=20)
def load_graph_payload(limit: int = 60, scope: str = "Dual graph",
                       highlight_ids: list[str] | None = None) -> dict:
    scope_filter = {
        "Dual graph": "['Company','Mentor','Flow','Skill','Connector','Server','Programme']",
        "Graph A: History": "['Company','Mentor','Programme']",
        "Graph B: Code and Infrastructure": "['Flow','Skill','Connector','Server']",
        "Bridge: Execution traces": "['ExecutionTrace','Outcome','Flow']",
    }
    label_filter = scope_filter.get(scope, scope_filter["Dual graph"])

    rows = run_read(f"""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN {label_filter})
        WITH n LIMIT {limit}
        OPTIONAL MATCH (n)-[r]->(m)
        WHERE any(label IN labels(m) WHERE label IN {label_filter})
        RETURN elementId(n) AS source_id,
               labels(n) AS source_labels,
               coalesce(n.name, n.id, elementId(n)) AS source_name,
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
               type(r) AS rel_type,
               elementId(m) AS target_id,
               labels(m) AS target_labels,
               coalesce(m.name, m.id, elementId(m)) AS target_name,
               m.status AS target_status,
               m.avg_outcome_score AS target_score
    """)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for row in rows:
        sid = row["source_id"]
        nodes[sid] = {
            "id":       sid,
            "label":    str(row["source_name"]),
            "group":    row["source_labels"][0] if row["source_labels"] else "Node",
            "status":   row.get("source_status"),
            "score":    row.get("source_score"),
            "industry": row.get("source_industry"),
            "stage":    row.get("source_stage"),
            "pain":     row.get("source_pain"),
            "revenue":  row.get("source_revenue"),
            "expertise":row.get("source_expertise"),
            "success":  row.get("source_success"),
            "available":row.get("source_available"),
            "load":     row.get("source_load"),
            "region":   row.get("source_region"),
            "perf":     row.get("source_perf"),
            "error":    row.get("source_error"),
            "highlighted": sid in (highlight_ids or []),
        }
        if row.get("target_id"):
            tid = row["target_id"]
            nodes[tid] = {
                "id":    tid,
                "label": str(row["target_name"]),
                "group": row["target_labels"][0] if row["target_labels"] else "Node",
                "status":row.get("target_status"),
                "score": row.get("target_score"),
                "highlighted": tid in (highlight_ids or []),
            }
            edges.append({
                "from":  sid,
                "to":    tid,
                "label": row.get("rel_type", ""),
            })

    return {"nodes": list(nodes.values()), "edges": edges}


def clear_data_cache() -> None:
    st.cache_data.clear()


def run_agent_streaming(goal: str):
    """Run agent as subprocess and yield log lines as they come."""
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "main.py", "--goal", goal],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip())
        yield line.rstrip(), proc
    proc.wait()
    return lines, proc.returncode


def display_table(data: pd.DataFrame, height: int = 280) -> None:
    if data.empty:
        st.info("No records yet.")
        return
    st.dataframe(data, width="stretch", height=height, hide_index=True)


def proposal_payload(raw: Any) -> str:
    if not raw:
        return ""
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (TypeError, json.JSONDecodeError):
        return str(raw)


def cloud_run_job_url() -> str | None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    region  = os.environ.get("SANDBOX_GCP_REGION") or os.environ.get("GOOGLE_CLOUD_LOCATION")
    job     = os.environ.get("SANDBOX_JOB_NAME")
    if not project or not region or not job:
        return None
    return f"https://console.cloud.google.com/run/jobs/details/{region}/{job}/executions?project={project}"


def graph_legend_html() -> str:
    items = [
        ("Company",    "#d7efe5", "#167447"),
        ("Mentor",     "#e7e0ff", "#5f4bb6"),
        ("Programme",  "#f3e5ab", "#8b6d12"),
        ("Flow",       "#fff0c2", "#a55b19"),
        ("Skill",      "#dcecff", "#3267a8"),
        ("Connector",  "#ffd9cc", "#b54a2c"),
        ("Server",     "#e7e3d8", "#6d6252"),
        ("Problem",    "#fddede", "#a73737"),
        ("Proposed",   "#fff9c2", "#d4a017"),
        ("Agent Active","#d0f4de","#0f7b63"),
    ]
    dots = "".join(
        f'<div class="legend-item">'
        f'<div class="legend-dot" style="background:{bg};border-color:{border};"></div>'
        f'{label}</div>'
        for label, bg, border in items
    )
    return f'<div class="legend-box">{dots}</div>'


def classify_log_line(line: str) -> str:
    """Classify an agent log line for color coding."""
    l = line.lower()
    if any(x in l for x in ["planner", "planning", "query_graph", "querying"]):
        return "planner"
    if any(x in l for x in ["generator", "generating", "gemini", "llm", "propose"]):
        return "generator"
    if any(x in l for x in ["critic", "validat", "check"]):
        return "critic"
    if any(x in l for x in ["simulat", "sandbox", "sandbox"]):
        return "simulator"
    if any(x in l for x in ["done", "complete", "success", "approved"]):
        return "done"
    if any(x in l for x in ["error", "fail", "exception", "traceback"]):
        return "error"
    return ""


def graph_html(payload: dict, agent_active_ids: list[str] | None = None) -> str:
    """Interactive graph with click details and agent activity highlighting."""

    groups = {
        "Company":        {"color": {"background": "#d7efe5", "border": "#167447"}},
        "Mentor":         {"color": {"background": "#e7e0ff", "border": "#5f4bb6"}},
        "Flow":           {"color": {"background": "#fff0c2", "border": "#a55b19"}},
        "Skill":          {"color": {"background": "#dcecff", "border": "#3267a8"}},
        "Connector":      {"color": {"background": "#ffd9cc", "border": "#b54a2c"}},
        "Server":         {"color": {"background": "#e7e3d8", "border": "#6d6252"}},
        "ExecutionTrace": {"color": {"background": "#cdeff2", "border": "#217b84"}},
        "Outcome":        {"color": {"background": "#f0d6d6", "border": "#a73737"}},
        "Programme":      {"color": {"background": "#f3e5ab", "border": "#8b6d12"}},
    }

    size_map = {
        "Company": 24, "Mentor": 24, "Programme": 20,
        "Flow": 20, "Skill": 15, "Connector": 15,
        "Server": 18, "ExecutionTrace": 12, "Outcome": 12,
    }

    badge_colors = {
        "Company": "#167447", "Mentor": "#5f4bb6", "Flow": "#a55b19",
        "Skill": "#3267a8", "Connector": "#b54a2c", "Server": "#6d6252",
        "Programme": "#8b6d12", "ExecutionTrace": "#217b84", "Outcome": "#a73737",
    }

    active_ids = set(agent_active_ids or [])
    nodes = []
    node_details = {}

    for node in payload["nodes"]:
        group  = node["group"]
        label  = node["label"]
        status = node.get("status") or ""
        is_active = node["id"] in active_ids

        node_data: dict[str, Any] = {
            "id":    node["id"],
            "label": label[:20],
            "group": group,
            "title": f"<b>{group}</b>: {label}",
            "shape": "dot",
            "size":  size_map.get(group, 14) * (1.4 if is_active else 1),
        }

        # Color priority: agent active > problem > proposed > normal
        if is_active:
            node_data["color"] = {
                "background": "#d0f4de", "border": "#0f7b63",
                "highlight":  {"background": "#b7eecb", "border": "#0a5c49"},
            }
            node_data["shadow"] = {
                "enabled": True, "color": "rgba(15,123,99,0.4)",
                "size": 16, "x": 0, "y": 0,
            }
        elif status in ("overloaded", "critical", "deprecated"):
            node_data["color"] = {
                "background": "#fddede", "border": "#a73737",
                "highlight":  {"background": "#fddede", "border": "#7a1c1c"},
            }
        elif status == "proposed":
            node_data["color"] = {
                "background": "#fff9c2", "border": "#d4a017",
                "highlight":  {"background": "#fff9c2", "border": "#b8860b"},
            }

        nodes.append(node_data)

        # Build detail panel content
        details: dict[str, Any] = {"Type": group, "Name": label}
        if status:                   details["Status"]        = status
        if is_active:                details["Agent Status"]  = "ACTIVE — being processed"
        if node.get("industry"):     details["Industry"]      = node["industry"]
        if node.get("stage"):        details["Stage"]         = node["stage"]
        if node.get("pain"):         details["Pain Points"]   = node["pain"]
        if node.get("revenue") is not None: details["Revenue"] = f"RM {node['revenue']:,}"
        if node.get("expertise"):
            exp = node["expertise"]
            details["Expertise"] = ", ".join(exp) if isinstance(exp, list) else exp
        if node.get("success") is not None: details["Success Score"] = node["success"]
        if node.get("available") is not None: details["Available"]   = "Yes" if node["available"] else "No"
        if node.get("score") is not None:    details["Avg Score"]    = node["score"]
        if node.get("load") is not None:     details["CPU Load"]     = f"{node['load']}%"
        if node.get("region"):               details["Region"]       = node["region"]
        if node.get("perf") is not None:     details["Performance"]  = node["perf"]
        if node.get("error") is not None:    details["Error Rate"]   = node["error"]

        node_details[node["id"]] = details

    html = f"""
    <div style="display:flex; gap:12px; height:720px;">

      <!-- Graph container -->
      <div style="flex:1; position:relative;">

        <!-- Search bar -->
        <div style="position:absolute; top:10px; left:10px; z-index:10; display:flex; gap:6px;">
          <input id="searchBox" placeholder="Search node..." onkeyup="searchNode()"
            style="padding:6px 12px; border-radius:8px; border:1px solid #d8d1c2;
                   background:#fffaf0; font-size:13px; width:180px; outline:none;
                   box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <button onclick="resetView()"
            style="padding:6px 12px; border-radius:8px; border:1px solid #d8d1c2;
                   background:#fffaf0; cursor:pointer; font-size:12px; color:#65706d;">
            Reset
          </button>
        </div>

        <!-- Agent activity indicator -->
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

      <!-- Detail Panel -->
      <div id="detailPanel"
        style="width:265px; background:#fffaf0; border:1px solid #d8d1c2;
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
          tooltipDelay: 80,
        }},
        physics: {{
          solver: "forceAtlas2Based",
          forceAtlas2Based: {{
            gravitationalConstant: -60,
            centralGravity: 0.008,
            springLength: 170,
            springConstant: 0.05,
            damping: 0.55,
          }},
          stabilization: {{ iterations: 220, updateInterval: 20 }},
          adaptiveTimestep: true,
        }},
        nodes: {{
          font: {{
            face: "Inter, sans-serif",
            size: 12,
            color: "#19211f",
            strokeWidth: 3,
            strokeColor: "#fffaf0",
          }},
          borderWidth: 2,
          borderWidthSelected: 3,
          shadow: {{ enabled: true, size: 8, x: 2, y: 3, color: "rgba(0,0,0,0.07)" }},
        }},
        edges: {{
          arrows: {{ to: {{ enabled: true, scaleFactor: 0.45 }} }},
          color: {{ color: "#b5a99a", highlight: "#0f7b63", hover: "#0f7b63" }},
          font: {{ size: 9, align: "middle", color: "#7a6f63", strokeWidth: 2, strokeColor: "#fffaf0" }},
          smooth: {{ type: "cubicBezier", forceDirection: "none", roundness: 0.45 }},
          width: 1.2, selectionWidth: 2.5,
        }},
        layout: {{ improvedLayout: true }},
      }};

      const network = new vis.Network(container, {{ nodes: nodesData, edges: edgesData }}, options);

      network.once("stabilizationIterationsDone", function() {{
        network.fit({{ animation: {{ duration: 900, easingFunction: "easeInOutQuad" }} }});
        // If there are active nodes, pulse them
        if (activeIds.length > 0) {{
          document.getElementById("agentIndicator").style.display = "block";
          pulseActiveNodes();
        }}
      }});

      // Pulse animation for active nodes
      let pulseUp = true;
      function pulseActiveNodes() {{
        if (activeIds.length === 0) return;
        setInterval(() => {{
          const updates = activeIds.map(id => {{
            const node = nodesData.get(id);
            if (!node) return null;
            return {{
              id,
              size: pulseUp ? (node.size || 20) * 1.15 : (node.size || 20),
            }};
          }}).filter(Boolean);
          nodesData.update(updates);
          pulseUp = !pulseUp;
        }}, 700);
      }}

      // Click handler
      network.on("click", function(params) {{
        const panel = document.getElementById("detailPanel");
        if (params.nodes.length === 0) {{
          panel.innerHTML = '<div style="font-size:0.85rem;color:#65706d;text-align:center;margin-top:40px;">Click any node to see its details</div>';
          return;
        }}
        const nodeId = params.nodes[0];
        const info   = details[nodeId];
        if (!info) return;

        const type  = info["Type"]  || "Node";
        const name  = info["Name"]  || nodeId;
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
              <span style="color:${{isAgentRow ? '#0f7b63' : '#19211f'}};font-weight:600;text-align:right;">${{v}}</span>
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
          <div style="font-size:1rem;font-weight:700;color:#19211f;margin-bottom:4px;">${{name}}</div>
          <div style="display:inline-block;padding:3px 10px;border-radius:999px;
                      font-size:0.72rem;font-weight:600;color:white;
                      background:${{color}};margin-bottom:14px;">${{type}}</div>
          ${{rows}}
          <div style="margin-top:10px;font-size:0.75rem;color:#9c927f;text-align:center;">
            Node ID: ...${{String(nodeId).slice(-8)}}
          </div>`;
      }});

      network.on("hoverNode", function() {{ container.style.cursor = "pointer"; }});
      network.on("blurNode",  function() {{ container.style.cursor = "default"; }});

      function searchNode() {{
        const q = document.getElementById("searchBox").value.toLowerCase();
        if (!q) {{ resetView(); return; }}
        const allNodes = nodesData.get();
        const match = allNodes.find(n => n.label && n.label.toLowerCase().includes(q));
        if (match) {{
          network.selectNodes([match.id]);
          network.focus(match.id, {{ scale: 1.5, animation: {{ duration: 700 }} }});
          const info   = details[match.id];
          if (info) {{
            const panel  = document.getElementById("detailPanel");
            const type   = info["Type"] || "Node";
            const name   = info["Name"] || match.id;
            const color  = badgeColor[type] || "#65706d";
            let rows = "";
            for (const [k, v] of Object.entries(info)) {{
              if (k === "Type" || k === "Name") continue;
              rows += `<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #ede8df;font-size:0.82rem;gap:8px;"><span style="color:#65706d;font-weight:500;">${{k}}</span><span style="color:#19211f;font-weight:600;text-align:right;">${{v}}</span></div>`;
            }}
            panel.innerHTML = `<div style="font-size:1rem;font-weight:700;color:#19211f;margin-bottom:4px;">${{name}}</div><div style="display:inline-block;padding:3px 10px;border-radius:999px;font-size:0.72rem;font-weight:600;color:white;background:${{color}};margin-bottom:14px;">${{type}}</div>${{rows}}`;
          }}
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
        result = simulate_flow.invoke({"flow_yaml": flow_yaml, "dataset_snapshot_id": "ui_sandbox_snapshot"})
    finally:
        if old_mock is None: os.environ.pop("SANDBOX_MOCK", None)
        else: os.environ["SANDBOX_MOCK"] = old_mock
        if old_mode is None: os.environ.pop("SANDBOX_MODE", None)
        else: os.environ["SANDBOX_MODE"] = old_mode
    return result


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## EcoLink")
    page = st.radio(
        "View",
        ["Command Center", "Graph View", "Agent Run", "Sandbox",
         "Flows", "Proposals", "Infrastructure", "History"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    if st.button("Refresh Data", use_container_width=True):
        clear_data_cache()
        st.rerun()


# ── HEADER ────────────────────────────────────────────────────────────────────
st.title("EcoLink NeuroCore")
sandbox_label = (
    "Mock Sandbox"
    if os.environ.get("SANDBOX_MOCK", "true").lower() == "true"
    else f"{os.environ.get('SANDBOX_MODE', 'local').title()} Sandbox"
)
st.markdown(
    "<span class='status-pill status-good'>Neo4j Connected</span>"
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

# ── PAGES ─────────────────────────────────────────────────────────────────────

if page == "Command Center":
    cols = st.columns(6)
    cols[0].metric("Companies",       overview.get("companies", 0))
    cols[1].metric("Mentors",         overview.get("mentors", 0))
    cols[2].metric("Flows",           overview.get("flows", 0))
    cols[3].metric("Servers",         overview.get("servers", 0))
    cols[4].metric("Avg Match Score", overview.get("avg_match_score", 0))
    cols[5].metric("Pending",         overview.get("proposed", 0))

    left, right = st.columns([1.25, 1])
    with left:
        st.subheader("Flow Portfolio")
        flows = load_flows()
        display_table(flows[["id", "status", "avg_score", "connector", "server", "skills"]], height=320)
    with right:
        st.subheader("Recent Execution Traces")
        display_table(load_traces(), height=320)
    st.subheader("Lowest Historical Matches")
    display_table(load_matches(), height=340)


elif page == "Graph View":
    st.subheader("Dual Graph Explorer")

    col_scope, col_limit = st.columns([3, 1])
    with col_scope:
        graph_scope = st.radio(
            "Graph scope",
            ["Dual graph", "Graph A: History",
             "Graph B: Code and Infrastructure", "Bridge: Execution traces"],
            horizontal=True,
        )
    with col_limit:
        limit = st.slider("Node limit", 20, 120, 60, 10)

    st.markdown(graph_legend_html(), unsafe_allow_html=True)
    st.markdown(
        '<div class="graph-tip">Click any node to see its details on the right panel. '
        'Search by name in the top-left search box. Scroll to zoom. Drag to explore. '
        'Green glowing nodes = agent is currently working on them.</div>',
        unsafe_allow_html=True,
    )

    # Check if agent is running and which nodes it touched
    agent_active = st.session_state.get("agent_running", False)
    active_node_ids = st.session_state.get("agent_active_nodes", [])

    payload = load_graph_payload(limit=limit, scope=graph_scope, highlight_ids=active_node_ids)

    c1, c2, c3 = st.columns(3)
    c1.metric("Nodes shown",      len(payload["nodes"]))
    c2.metric("Relationships",     len(payload["edges"]))
    c3.metric("Pending Proposals", overview.get("proposed", 0))

    if agent_active:
        st.info("Agent is running — graph will update when complete. Refresh to see changes.")

    components.html(graph_html(payload, agent_active_ids=active_node_ids), height=730)


elif page == "Agent Run":
    st.subheader("Run Optimization")

    col1, col2 = st.columns([3, 1])
    with col1:
        goal = st.text_input("Goal", value="Improve match quality for Healthtech startups")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        run_clicked = st.button("Run Agent", type="primary", use_container_width=True)

    # Visual agent animation panel
    current_phase = st.session_state.get("agent_phase", "idle")
    anim_slot = st.empty()

    def agent_animation_html(phase="idle", goal=""):
        phases_map = {
            "idle":       (-1, "Ready — enter a goal and click Run Agent"),
            "reading":    (0,  "Planner is reading historical match data from the Neo4j graph"),
            "thinking":   (1,  "Generator is sending graph patterns to Gemini AI for analysis"),
            "proposing":  (2,  "Critic is checking the proposed flow against system constraints"),
            "validating": (3,  "Simulator is testing the proposal safely in the sandbox"),
            "done":       (4,  "Agent complete — new proposal is ready for admin review"),
            "error":      (-2, "Agent stopped early — API quota may be exceeded, wait and retry"),
        }
        active, msg = phases_map.get(phase, phases_map["idle"])
        goal_str = (goal[:55] + "...") if len(goal) > 57 else (goal or "No goal set yet")

        agents = [
            ("Planner",   "Reads Neo4j graph", "#3267a8", "#dcecff"),
            ("Generator", "Calls Gemini AI",   "#167447", "#d7efe5"),
            ("Critic",    "Validates proposal","#a55b19", "#fff0c2"),
            ("Simulator", "Tests in sandbox",  "#5f4bb6", "#e7e0ff"),
        ]

        cards = ""
        for i, (name, role, color, bg) in enumerate(agents):
            is_a = active == i
            is_d = active > i and active >= 0
            op   = "1" if (is_a or is_d) else "0.28"
            bd   = f"2px solid {color}" if is_a else "1px solid #d8d1c2"
            cbg  = bg if is_a else "#fffaf0"
            pulse = "animation:pulse-card 1.4s ease-in-out infinite;" if is_a else ""

            if is_a:
                dot = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};animation:blink .9s infinite;margin-right:6px;flex-shrink:0;" aria-hidden="true"></span>'
            elif is_d:
                dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#167447;margin-right:6px;flex-shrink:0;" aria-hidden="true"></span>'
            else:
                dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#d8d1c2;margin-right:6px;flex-shrink:0;" aria-hidden="true"></span>'

            cards += f'<div style="background:{cbg};border:{bd};border-radius:12px;padding:14px 12px;opacity:{op};transition:all .45s;{pulse}"><div style="display:flex;align-items:center;margin-bottom:6px;">{dot}<span style="font-size:.84rem;font-weight:600;color:{color};">{name}</span></div><div style="font-size:.73rem;color:#65706d;line-height:1.4;">{role}</div></div>'

            if i < 3:
                ac = color if (is_a or is_d) else "#d8d1c2"
                cards += f'<div style="display:flex;align-items:center;justify-content:center;color:{ac};font-size:20px;" aria-hidden="true">&rarr;</div>'

        pct = max(0, int(active / 4 * 100)) if active >= 0 else 0

        if phase == "done":   sb,sbd,sc = "#f0faf5","#167447","#167447"
        elif phase == "error":sb,sbd,sc = "#fdf0f0","#a73737","#a73737"
        elif phase == "idle": sb,sbd,sc = "#f5f2eb","#d8d1c2","#65706d"
        else:                 sb,sbd,sc = "#edf5ff","#3267a8","#3267a8"

        return f"""<style>
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
@keyframes pulse-card{{0%,100%{{box-shadow:0 0 0 0 rgba(50,103,168,.18)}}50%{{box-shadow:0 0 0 6px rgba(50,103,168,.06)}}}}
</style>
<div style="background:#fffaf0;border:1px solid #d8d1c2;border-radius:14px;padding:22px 20px 18px;margin-bottom:10px;">
<div style="font-size:.73rem;color:#65706d;font-weight:500;margin-bottom:3px;">Goal</div>
<div style="font-size:.9rem;color:#19211f;font-weight:600;margin-bottom:18px;">{goal_str}</div>
<div style="display:grid;grid-template-columns:1fr 32px 1fr 32px 1fr 32px 1fr;align-items:center;gap:3px;margin-bottom:16px;">{cards}</div>
<div style="background:#ede8df;border-radius:999px;height:3px;margin-bottom:11px;overflow:hidden;"><div style="background:#0f7b63;height:3px;width:{pct}%;border-radius:999px;transition:width .7s ease;"></div></div>
<div style="background:{sb};border:1px solid {sbd};border-radius:8px;padding:9px 13px;font-size:.79rem;color:{sc};font-weight:500;">{msg}</div>
</div>"""

    anim_slot.markdown(agent_animation_html(current_phase, st.session_state.get("agent_goal", "")), unsafe_allow_html=True)

    if run_clicked:
        st.session_state["agent_goal"]    = goal
        st.session_state["agent_phase"]   = "reading"
        st.session_state["agent_running"] = True
        st.session_state["agent_active_nodes"] = []
        anim_slot.markdown(agent_animation_html("reading", goal), unsafe_allow_html=True)

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
                if st.session_state.get("agent_phase") != "reading":
                    st.session_state["agent_phase"] = "reading"
                    anim_slot.markdown(agent_animation_html("reading", goal), unsafe_allow_html=True)
            elif any(x in ll for x in ["gemini","llm","generat","propose","200 ok","http request"]):
                if st.session_state.get("agent_phase") != "thinking":
                    st.session_state["agent_phase"] = "thinking"
                    anim_slot.markdown(agent_animation_html("thinking", goal), unsafe_allow_html=True)
            elif any(x in ll for x in ["critic","validat","check","constraint"]):
                if st.session_state.get("agent_phase") != "proposing":
                    st.session_state["agent_phase"] = "proposing"
                    anim_slot.markdown(agent_animation_html("proposing", goal), unsafe_allow_html=True)
            elif any(x in ll for x in ["simulat","sandbox","testing"]):
                if st.session_state.get("agent_phase") != "validating":
                    st.session_state["agent_phase"] = "validating"
                    anim_slot.markdown(agent_animation_html("validating", goal), unsafe_allow_html=True)

        proc.wait()
        clear_data_cache()
        st.session_state["agent_running"] = False

        if proc.returncode == 0 or len([p for p in load_flows()["status"] if p == "proposed"]) > 0:
            st.session_state["agent_phase"] = "done"
            anim_slot.markdown(agent_animation_html("done", goal), unsafe_allow_html=True)
            st.success("Agent completed — check the Proposals page to review the new proposal.")
        else:
            st.session_state["agent_phase"] = "error"
            anim_slot.markdown(agent_animation_html("error", goal), unsafe_allow_html=True)
            st.warning("Agent stopped before finishing. This is usually an API quota issue — wait a minute and try again.")

        st.info("Go to Graph View to see the updated graph.")

    st.subheader("Proposals Created")
    proposals = load_flows()
    proposals = proposals[proposals["status"].fillna("") == "proposed"]
    display_table(proposals[["id", "name", "avg_score", "server", "connector"]], height=220)


elif page == "Sandbox":
    st.subheader("Sandbox Control")
    gcp_url = cloud_run_job_url()
    mode = st.segmented_control(
        "Sandbox target", options=["local", "cloudrun"],
        default=os.environ.get("SANDBOX_MODE", "local")
        if os.environ.get("SANDBOX_MODE", "local") in {"local", "cloudrun"} else "local",
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Mode",       os.environ.get("SANDBOX_MODE", "local"))
    c2.metric("GCP Region", os.environ.get("SANDBOX_GCP_REGION", "not set"))
    c3.metric("Cloud Run",  os.environ.get("SANDBOX_JOB_NAME", "not set"))
    if gcp_url:
        st.link_button("Open GCP Cloud Run Job", gcp_url)
    else:
        st.warning("Set GOOGLE_CLOUD_PROJECT, SANDBOX_GCP_REGION, and SANDBOX_JOB_NAME.")

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
        if result.get("status") == "success":
            st.success("Sandbox run created successfully.")
            score = result.get("metrics", {}).get("match_score", 0.0)
            log_execution_trace(flow_id="flow_smart_match_v1", result_score=score, status="success")
            st.info("Execution trace logged.")
        else:
            st.error(result.get("error_log", "Sandbox run failed."))
    if "last_sandbox_result" in st.session_state:
        st.markdown("### Last Sandbox Result")
        st.json(st.session_state["last_sandbox_result"])


elif page == "Flows":
    st.subheader("Flows")
    flows = load_flows()
    status = st.multiselect(
        "Filter by status",
        sorted([s for s in flows["status"].dropna().unique()]) if not flows.empty else [],
        default=sorted([s for s in flows["status"].dropna().unique()]) if not flows.empty else [],
    )
    if status and not flows.empty:
        flows = flows[flows["status"].isin(status)]
    display_table(flows, height=320)

    st.subheader("Optimize a Flow")
    st.caption("Select a flow to run the agent and propose an improved version — no need to leave this page.")

    all_flows = load_flows()
    flow_names = all_flows["name"].tolist() if not all_flows.empty else []

    selected_flow_name = st.selectbox("Select flow to optimize", flow_names)

    # Get selected flow details for display
    selected_row = all_flows[all_flows["name"] == selected_flow_name]
    selected_score   = float(selected_row["avg_score"].values[0]) if not selected_row.empty and selected_row["avg_score"].values[0] else 0
    selected_skills  = selected_row["skills"].values[0] if not selected_row.empty else []
    selected_conn    = selected_row["connector"].values[0] if not selected_row.empty else "unknown"
    selected_status  = selected_row["status"].values[0] if not selected_row.empty else ""

    # Show selected flow info
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Flow", selected_flow_name[:22])
        c2.metric("Current Score", selected_score if selected_score else "N/A")
        c3.metric("Connector", selected_conn or "—")
        c4.metric("Status", selected_status or "—")

    # Animation function with shimmer effect
    opt_phase = st.session_state.get("opt_phase", "idle")
    opt_slot  = st.empty()

    def opt_anim(phase="idle", flow_name=""):
        phases_map = {
            "idle":       (-1, f"Ready — click Optimize to improve '{flow_name}'"),
            "reading":    (0,  f"Planner reading '{flow_name}' skills and connectors from Neo4j graph..."),
            "thinking":   (1,  f"Generator asking Gemini AI how to improve '{flow_name}'..."),
            "proposing":  (2,  f"Critic validating the proposed replacement flow against constraints..."),
            "validating": (3,  f"Simulator testing the new flow safely in sandbox environment..."),
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
            op   = "1" if (is_a or is_d) else "0.28"
            bd   = f"2px solid {color}" if is_a else "1px solid #d8d1c2"
            cbg  = bg if is_a else "#fffaf0"
            pulse = "animation:pulse-card 1.4s ease-in-out infinite;" if is_a else ""
            shimmer = '<div style="position:absolute;top:0;left:-100%;width:60%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.55),transparent);animation:shimmer 1.3s infinite;pointer-events:none;"></div>' if is_a else ""

            if is_a:
                dot = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};animation:blink .9s infinite;margin-right:6px;flex-shrink:0;" aria-hidden="true"></span>'
            elif is_d:
                dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#167447;margin-right:6px;flex-shrink:0;" aria-hidden="true"></span>'
            else:
                dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#d8d1c2;margin-right:6px;flex-shrink:0;" aria-hidden="true"></span>'

            cards += f'<div style="background:{cbg};border:{bd};border-radius:12px;padding:14px 12px;opacity:{op};transition:all .45s;{pulse}position:relative;overflow:hidden;">{shimmer}<div style="display:flex;align-items:center;margin-bottom:6px;">{dot}<span style="font-size:.84rem;font-weight:600;color:{color};">{name}</span></div><div style="font-size:.72rem;color:#65706d;line-height:1.4;">{role}</div></div>'

            if i < 3:
                ac = color if (is_a or is_d) else "#d8d1c2"
                cards += f'<div style="display:flex;align-items:center;justify-content:center;color:{ac};font-size:20px;" aria-hidden="true">&rarr;</div>'

        pct = max(0, int(active / 4 * 100)) if active >= 0 else 0

        if phase == "done":   sb,sbd,sc = "#f0faf5","#167447","#167447"
        elif phase == "error":sb,sbd,sc = "#fdf0f0","#a73737","#a73737"
        elif phase == "idle": sb,sbd,sc = "#f5f2eb","#d8d1c2","#65706d"
        else:                 sb,sbd,sc = "#edf5ff","#3267a8","#3267a8"

        return f"""<style>
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
@keyframes pulse-card{{0%,100%{{box-shadow:0 0 0 0 rgba(50,103,168,.18)}}50%{{box-shadow:0 0 0 6px rgba(50,103,168,.06)}}}}
@keyframes shimmer{{to{{left:140%}}}}
</style>
<div style="background:#fffaf0;border:1px solid #d8d1c2;border-radius:14px;padding:20px 20px 16px;">
<div style="display:grid;grid-template-columns:1fr 32px 1fr 32px 1fr 32px 1fr;align-items:center;gap:3px;margin-bottom:14px;">{cards}</div>
<div style="background:#ede8df;border-radius:999px;height:3px;margin-bottom:10px;overflow:hidden;">
<div style="background:#0f7b63;height:3px;width:{pct}%;border-radius:999px;transition:width .7s ease;"></div></div>
<div style="background:{sb};border:1px solid {sbd};border-radius:8px;padding:9px 13px;font-size:.79rem;color:{sc};font-weight:500;">{msg}</div>
</div>"""

    opt_slot.markdown(opt_anim(opt_phase, selected_flow_name), unsafe_allow_html=True)

    # Result panel after done
    if opt_phase == "done":
        with st.container(border=True):
            st.markdown("**What the agent improved**")
            ca, cb = st.columns(2)
            with ca:
                st.markdown(f"**Before:** {selected_flow_name}")
                st.markdown(f"Score: `{selected_score}`")
                st.markdown(f"Connector: `{selected_conn or '—'}`")
            with cb:
                st.markdown("**After:** New proposed flow")
                st.markdown("Score: `estimated higher`")
                st.markdown("Connector: `optimised`")
            st.info("Go to Proposals page to Approve or Reject this proposal.")

    if st.button("Optimize this flow", type="primary", use_container_width=True):
        goal = f"Optimize the flow named '{selected_flow_name}'. Current score is {selected_score}. Analyse its skills and historical match failures. Propose a better version with improved skills and connectors."
        st.session_state["opt_phase"] = "reading"
        opt_slot.markdown(opt_anim("reading", selected_flow_name), unsafe_allow_html=True)

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
                    opt_slot.markdown(opt_anim("reading", selected_flow_name), unsafe_allow_html=True)
            elif any(x in ll for x in ["gemini","llm","generat","propose","200 ok"]):
                if st.session_state.get("opt_phase") != "thinking":
                    st.session_state["opt_phase"] = "thinking"
                    opt_slot.markdown(opt_anim("thinking", selected_flow_name), unsafe_allow_html=True)
            elif any(x in ll for x in ["critic","validat","check"]):
                if st.session_state.get("opt_phase") != "proposing":
                    st.session_state["opt_phase"] = "proposing"
                    opt_slot.markdown(opt_anim("proposing", selected_flow_name), unsafe_allow_html=True)
            elif any(x in ll for x in ["simulat","sandbox"]):
                if st.session_state.get("opt_phase") != "validating":
                    st.session_state["opt_phase"] = "validating"
                    opt_slot.markdown(opt_anim("validating", selected_flow_name), unsafe_allow_html=True)

        proc.wait()
        clear_data_cache()

        # ── FIX: check proposals actually created, not return code ──
        updated_flows = load_flows()
        has_proposals = not updated_flows[updated_flows["status"].fillna("") == "proposed"].empty

        if has_proposals:
            st.session_state["opt_phase"] = "done"
            opt_slot.markdown(opt_anim("done", selected_flow_name), unsafe_allow_html=True)
            st.success("Optimization complete — go to Proposals to approve the new flow!")
        else:
            st.session_state["opt_phase"] = "error"
            opt_slot.markdown(opt_anim("error", selected_flow_name), unsafe_allow_html=True)
            st.warning("No new proposals were created. Try again in a moment.")


elif page == "Proposals":
    st.subheader("Pending Optimizations")
    flows = load_flows()
    proposals = flows[flows["status"].fillna("") == "proposed"] if not flows.empty else flows
    if proposals.empty:
        st.info("No pending proposals — the system is optimised.")
    for _, row in proposals.iterrows():
        with st.container(border=True):
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
                st.write({
                    "name": row.get("name"), "avg_score": row.get("avg_score"),
                    "server": row.get("server"), "connector": row.get("connector"),
                    "skills": row.get("skills"),
                })
            payload_str = proposal_payload(row.get("yaml_config"))
            if payload_str:
                st.code(payload_str, language="json")


elif page == "Infrastructure":
    st.subheader("Server Infrastructure")
    servers = load_servers()
    display_table(servers, height=280)
    if not servers.empty:
        st.subheader("CPU Load by Server")
        chart_data = servers[["name", "load_percent"]].set_index("name")
        st.bar_chart(chart_data)


elif page == "History":
    tab1, tab2 = st.tabs(["Matches", "Execution Traces"])
    with tab1:
        display_table(load_matches(), height=520)
    with tab2:
        display_table(load_traces(), height=520)