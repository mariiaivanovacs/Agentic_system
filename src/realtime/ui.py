from __future__ import annotations

import json


AGENTS = [
    {"id": "planner",        "label": "Planner",        "x": -260, "y": -30,  "role": "Finds graph evidence & forms hypothesis"},
    {"id": "generator",      "label": "Generator",      "x": -110, "y": -165, "role": "Proposes actions: modify_workflow, modify_code, add_validation, flag_risk"},
    {"id": "critic",         "label": "Critic",         "x":   55, "y": -165, "role": "Validates skills, connectors & infra"},
    {"id": "simulator",      "label": "Simulator",      "x":  210, "y": -165, "role": "Dispatches to flow sandbox, code sandbox, or graph-review (proposal-only)"},
    {"id": "evaluator",      "label": "Evaluator",      "x":  210, "y":  110, "role": "Compares simulation score to baseline"},
    {"id": "human_approval", "label": "Human Approval", "x":   55, "y":  110, "role": "Admin approves or rejects proposal"},
    {"id": "end",            "label": "End",            "x": -110, "y":  110, "role": "Run stops after approval, rejection, or retry exhaustion"},
]

AGENT_COLORS = {
    "planner":        {"background": "#eef3fb", "border": "#4f6f8f"},
    "generator":      {"background": "#fff4df", "border": "#9a5b13"},
    "critic":         {"background": "#fff0e8", "border": "#9f5b39"},
    "simulator":      {"background": "#edf8f3", "border": "#357960"},
    "evaluator":      {"background": "#f3effb", "border": "#7660a8"},
    "human_approval": {"background": "#f9dce8", "border": "#9d174d"},
    "end":            {"background": "#f4f0f2", "border": "#756b70"},
}

# Agent icons shown on event cards and the thinking overlay
AGENT_ICONS = {
    "planner":        "🗺️",
    "generator":      "✏️",
    "critic":         "🔍",
    "simulator":      "⚗️",
    "evaluator":      "📊",
    "human_approval": "👤",
    "end":            "■",
    "ui":             "🖥️",
    "agent":          "🤖",
    "indexer":        "📂",
}

EVENT_TYPE_ICONS = {
    "message":          "💬",
    "thinking":         "⏳",
    "phase":            "↻",
    "log":              ">_",
    "decision":         "⚖️",
    "result":           "✅",
    "approval_required":"⏸️",
    "error":            "❌",
    "approved":         "✅",
    "rejected":         "🚫",
    "started":          "▶️",
    "tool_call":        "🔧",
}

EDGES = [
    {"from": "planner",   "to": "generator",      "label": "hypothesis", "smooth": {"enabled": False},                        "retry": False},
    {"from": "generator", "to": "critic",          "label": "actions",    "smooth": {"type": "curvedCW",  "roundness": 0.12},  "retry": False},
    {"from": "critic",    "to": "simulator",       "label": "pass",       "smooth": {"enabled": False},                        "retry": False},
    {"from": "critic",    "to": "generator",       "label": "retry",      "smooth": {"type": "curvedCW",  "roundness": 0.50},  "retry": True},
    {"from": "simulator", "to": "evaluator",       "label": "metrics",    "smooth": {"enabled": False},                        "retry": False},
    {"from": "evaluator", "to": "human_approval",  "label": "proposal",   "smooth": {"enabled": False},                        "retry": False},
    {"from": "evaluator", "to": "generator",       "label": "retry",      "smooth": {"type": "curvedCCW", "roundness": 0.48},  "retry": True},
    {"from": "critic",    "to": "end",             "label": "max retry",  "smooth": {"type": "curvedCCW", "roundness": 0.35},  "retry": False},
    {"from": "evaluator", "to": "end",             "label": "max retry",  "smooth": {"type": "curvedCW",  "roundness": 0.35},  "retry": False},
    {"from": "human_approval", "to": "end",         "label": "done",       "smooth": {"enabled": False},                        "retry": False},
]


def agent_map_html() -> str:
    return _shell_html(initial_events=[], live=False)


def live_comms_html(initial_events: list[dict], api_base: str, ws_url: str) -> str:
    return _shell_html(initial_events=initial_events, live=True, api_base=api_base, ws_url=ws_url)


def _build_vis_nodes() -> list[dict]:
    return [
        {
            "id":    ag["id"],
            "label": ag["label"],
            "title": ag["role"],
            "x":     ag["x"],
            "y":     ag["y"],
            "fixed": True,
            "physics": False,
            "color": {
                **AGENT_COLORS[ag["id"]],
                "highlight": {"background": AGENT_COLORS[ag["id"]]["background"], "border": "#9d174d"},
                "hover":     {"background": AGENT_COLORS[ag["id"]]["background"], "border": "#9d174d"},
            },
        }
        for ag in AGENTS
    ]


def _build_vis_edges() -> list[dict]:
    rows = []
    for e in EDGES:
        edge: dict = {"id": f"{e['from']}-{e['to']}", "from": e["from"], "to": e["to"], "label": e["label"], "smooth": e["smooth"]}
        if e["retry"]:
            edge["dashes"] = True
            edge["color"]  = {"color": "rgba(157,23,77,.24)", "highlight": "#9a5b13", "hover": "#9a5b13"}
        else:
            edge["color"]  = {"color": "rgba(217,196,207,.78)", "highlight": "#9d174d", "hover": "#9d174d"}
        rows.append(edge)
    return rows


def event_visual_transition(event: dict) -> dict:
    """Return the node/edge transition the live UI should show for an event."""
    source = event.get("source") or ""
    target = event.get("target") or ""
    event_type = event.get("event_type") or "message"
    terminal = event_type in {"approved", "rejected"} or (event_type == "decision" and not target)
    active_node = "end" if terminal and source else source
    active_edge = None
    if source and target:
        active_edge = f"{source}-{target}"
    elif terminal and source:
        active_edge = f"{source}-end"
    retry_edges = {f"{edge['from']}-{edge['to']}" for edge in EDGES if edge.get("retry")}
    return {
        "active_node": active_node,
        "active_edge": active_edge,
        "is_retry": active_edge in retry_edges if active_edge else False,
        "is_error": event_type in {"error", "rejected"},
        "is_terminal": terminal,
    }


def _shell_html(
    *,
    initial_events: list[dict],
    live: bool,
    api_base: str = "http://127.0.0.1:8765",
    ws_url: str = "ws://127.0.0.1:8765/ws/events",
) -> str:
    vis_nodes_json   = json.dumps(_build_vis_nodes())
    vis_edges_json   = json.dumps(_build_vis_edges())
    node_colors_json = json.dumps(AGENT_COLORS)
    agent_icons_json = json.dumps(AGENT_ICONS)
    event_icons_json = json.dumps(EVENT_TYPE_ICONS)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
  :root {{
    --bg: #fcfafb; --panel: #ffffff; --line: #eadde4; --text: #20181d; --muted: #6f626a;
    --accent: #9d174d; --gold: #9a5b13; --bad: #b4234a; --blue: #4f6f8f;
    --purple: #7660a8; --orange: #9f5b39;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text);
    font-family: "Avenir Next", "Helvetica Neue", Helvetica, sans-serif; }}

  /* ── layout ── */
  .wrap {{
    padding: 16px;
    background: var(--bg);
  }}
  .topbar {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }}
  .title  {{ font-size: 24px; font-weight: 720; flex: 1; }}
  .grid   {{ display: grid; grid-template-columns: minmax(420px, 1.15fr) minmax(320px, .85fr); gap: 12px; }}
  .mapOnly {{ grid-template-columns: 1fr; }}
  .panel  {{ border: 1px solid var(--line); border-radius: 8px;
    background: var(--panel);
    box-shadow: 0 16px 36px rgba(71,31,51,.07); overflow: hidden; position: relative; }}
  .panelHead {{ display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 12px; }}
  .networkStage {{ height: 520px; background: transparent; position: relative; }}
  #network-container {{ height: 520px; background: transparent; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 14px 10px; border-top: 1px solid var(--line); }}
  .legend span {{ color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 3px 9px; font-size: 11px; }}

  /* ── pills & buttons ── */
  .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px;
    color: var(--muted); background: #fff7fa; font-size: 12px; white-space: nowrap; }}
  .pill.ok  {{ color: var(--accent); border-color: rgba(157,23,77,.35); }}
  .pill.bad {{ color: var(--bad);    border-color: rgba(180,35,74,.35); }}
  .feedControls {{ display: grid; grid-template-columns: 1fr auto; gap: 8px;
    padding: 10px 12px; border-bottom: 1px solid var(--line); }}
  input {{ border: 1px solid var(--line); background: #ffffff; color: var(--text);
    border-radius: 7px; padding: 8px 10px; outline: none; width: 100%; }}
  button {{ border: 1px solid rgba(157,23,77,.35); background: rgba(157,23,77,.07);
    color: var(--accent); border-radius: 7px; padding: 7px 11px; cursor: pointer; }}
  .feed {{ height: 450px; overflow: auto; padding: 10px 12px 12px; }}
  .logsPanel {{ grid-column: 1 / -1; }}
  .logFeed {{
    height: 190px; overflow: auto; padding: 10px 12px 12px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    background: #fff7fa;
  }}
  .logLine {{
    display: grid; grid-template-columns: 72px 112px 1fr; gap: 10px;
    padding: 5px 0; border-bottom: 1px solid rgba(234,221,228,.8);
    color: #45333d; font-size: 11px; line-height: 1.35;
  }}
  .logLine:last-child {{ border-bottom: none; }}
  .logTime {{ color: var(--muted); }}
  .logNode {{ color: var(--accent); text-transform: uppercase; letter-spacing: .04em; }}
  .logText {{ white-space: pre-wrap; word-break: break-word; }}

  /* ── vis.js tooltip ── */
  .vis-tooltip {{
    background: #ffffff !important; border: 1px solid #eadde4 !important;
    color: #20181d !important; font-size: 12px !important;
    border-radius: 6px !important; padding: 6px 10px !important;
  }}

  /* ── thinking overlay (floats over canvas, shows near active node) ── */
  #think-overlay {{
    position: absolute; display: none;
    background: rgba(255,247,250,.96); border: 1px solid var(--accent);
    border-radius: 20px; padding: 5px 12px;
    font-size: 12px; color: var(--accent); white-space: nowrap;
    pointer-events: none; z-index: 10;
    box-shadow: 0 10px 24px rgba(157,23,77,.16);
    transition: opacity .15s;
  }}
  #think-overlay .think-icon {{ margin-right: 5px; font-size: 14px; }}
  #think-overlay .think-dots span {{
    animation: think-blink 1.4s infinite;
    display: inline-block;
  }}
  #think-overlay .think-dots span:nth-child(2) {{ animation-delay: .25s; }}
  #think-overlay .think-dots span:nth-child(3) {{ animation-delay: .50s; }}
  @keyframes think-blink {{ 0%,80%,100% {{ opacity:.15; }} 40% {{ opacity:1; }} }}

  /* ── activity bar in panel head ── */
  #activity-bar {{
    display: none; align-items: center; gap: 6px; font-size: 12px; color: var(--accent);
  }}
  #activity-bar.on {{ display: flex; }}
  .activity-pulse {{
    width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
    animation: activity-ring 1s infinite;
  }}
  @keyframes activity-ring {{
    0%   {{ box-shadow: 0 0 0 0 rgba(157,23,77,.45); }}
    70%  {{ box-shadow: 0 0 0 8px rgba(157,23,77,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(157,23,77,0); }}
  }}

  /* ── event cards ── */
  .event {{
    border-left: 3px solid var(--line); padding: 9px 9px 9px 11px;
    margin: 0 0 8px; background: #fff7fa; border-radius: 0 6px 6px 0;
    transition: border-left-color .2s;
  }}
  .event.result              {{ border-left-color: var(--accent); }}
  .event.decision,
  .event.approval_required   {{ border-left-color: var(--gold);   }}
  .event.error,
  .event.rejected            {{ border-left-color: var(--bad);    }}
  .event.approved            {{ border-left-color: var(--blue);   }}
  .event.started             {{ border-left-color: var(--purple); }}
  .event.tool_call           {{ border-left-color: var(--orange); }}
  .event.thinking,
  .event.phase,
  .event.log                 {{ border-left-color: var(--accent); }}
  .event.new {{ animation: card-flash .6s ease-out; }}
  @keyframes card-flash {{
    0%   {{ background: rgba(157,23,77,.12); }}
    100% {{ background: #fff7fa; }}
  }}
  .eventTop {{ display: flex; justify-content: space-between; gap: 8px;
    color: var(--muted); font-size: 11px; margin-bottom: 4px; }}
  .eventSource {{ display: flex; align-items: center; gap: 5px; }}
  .agentIcon   {{ font-size: 13px; }}
  .typeIcon    {{ font-size: 11px; opacity: .7; }}
  .eventTitle  {{ font-weight: 700; margin-bottom: 4px; font-size: 13px; }}
  .eventDetail {{ color: #45333d; font-size: 12px; line-height: 1.38; white-space: pre-wrap; }}

  /* ── evidence section ── */
  .evDetails {{ margin-top: 7px; border-top: 1px solid rgba(75,74,61,.6); padding-top: 5px; }}
  .evDetails summary {{
    cursor: pointer; color: var(--muted); font-size: 11px; letter-spacing: .04em;
    user-select: none; list-style: none; display: flex; align-items: center; gap: 5px;
  }}
  .evDetails summary::before {{ content: "▶"; font-size: 8px; transition: transform .15s; }}
  .evDetails[open] summary::before {{ transform: rotate(90deg); }}
  .evBody  {{ margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }}
  .evRow   {{ display: flex; gap: 8px; font-size: 11px; align-items: baseline; }}
  .evRow span:first-child {{ color: var(--muted); min-width: 110px; flex-shrink: 0; }}
  .evRow span:last-child  {{ color: #45333d; word-break: break-all; }}
  .evRow code {{ font-family: ui-monospace, monospace; font-size: 10px;
    background: #f4f0f2; padding: 1px 4px; border-radius: 3px; color: var(--blue); }}
  .evTag {{ display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 10px; font-weight: 700; letter-spacing: .04em; }}
  .evTag.ok  {{ background: rgba(157,23,77,.12); color: var(--accent); }}
  .evTag.bad {{ background: rgba(180,35,74,.12); color: var(--bad);   }}

  @media (max-width: 820px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="title">{'Live Agent Communications' if live else 'Agent Communication Map'}</div>
    <div id="activity-bar"><div class="activity-pulse"></div><span id="activity-label">running</span></div>
    <div id="status" class="pill">Static topology</div>
    <div id="eventCount" class="pill">0 events</div>
    <div class="pill">LangGraph</div>
  </div>
  <div class="grid {'mapOnly' if not live else ''}">
    <section class="panel">
      <div class="panelHead">
        <span>Agent network</span>
        <span id="activeAgentLabel" style="color:var(--muted)">idle</span>
      </div>
      <div class="networkStage">
        <div id="network-container"></div>
        <div id="think-overlay">
          <span class="think-icon" id="think-icon">⚗️</span>
          <span id="think-name">running</span>
          <span class="think-dots"><span>.</span><span>.</span><span>.</span></span>
        </div>
      </div>
      <div class="legend">
        <span style="border-color:rgba(157,23,77,.45);color:var(--accent)">&#9679; active</span>
        <span style="border-color:rgba(216,168,63,.5);color:var(--gold)">&#9679; retry</span>
        <span style="border-color:rgba(180,35,74,.45);color:var(--bad)">&#9679; error</span>
        <span>&#8212; main flow</span>
        <span style="letter-spacing:2px">&#xB7;&#xB7;&#xB7; retry</span>
      </div>
    </section>
    {'<section class="panel"><div class="panelHead"><span>Realtime event feed</span><span id="lastSeen">waiting…</span></div><div class="feedControls"><input id="threadFilter" placeholder="Filter by thread id" /><button id="clearFilter">All</button></div><div id="feed" class="feed"></div></section><section class="panel logsPanel"><div class="panelHead"><span>Live execution logs</span><span id="logCount">0 lines</span></div><div id="logFeed" class="logFeed"></div></section>' if live else ''}
  </div>
</div>

<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<script>
// ── static data ───────────────────────────────────────────────────────────────
const visNodesData   = {vis_nodes_json};
const visEdgesData   = {vis_edges_json};
const nodeColors     = {node_colors_json};
const agentIcons     = {agent_icons_json};
const eventTypeIcons = {event_icons_json};
const initialEvents  = {json.dumps(initial_events)};
const live           = {json.dumps(live)};
const apiBase        = {json.dumps(api_base)};
const wsUrl          = {json.dumps(ws_url)};

// ── vis.js setup ──────────────────────────────────────────────────────────────
const visNodes = new vis.DataSet(visNodesData);
const visEdges = new vis.DataSet(visEdgesData);
const networkOptions = {{
  physics: false,
  nodes: {{
    shape: "dot", size: 20,
    font: {{ face: "Avenir Next, Helvetica Neue, Helvetica, sans-serif", size: 13, color: "#20181d", strokeWidth: 2, strokeColor: "#ffffff" }},
    borderWidth: 2,
    shadow: {{ enabled: true, color: "rgba(71,31,51,.10)", size: 10, x: 2, y: 4 }},
  }},
  edges: {{
    arrows: {{ to: {{ enabled: true, scaleFactor: 0.65 }} }},
    width: 1.5, selectionWidth: 0,
    font: {{ size: 10, color: "#6f626a", align: "middle", strokeWidth: 2, strokeColor: "#ffffff" }},
  }},
  interaction: {{ hover: true, tooltipDelay: 100, zoomView: false, dragView: false, dragNodes: false, multiselect: false, selectable: false }},
}};
const container = document.getElementById("network-container");
const network   = new vis.Network(container, {{ nodes: visNodes, edges: visEdges }}, networkOptions);
network.once("afterDrawing", () => network.fit({{ animation: false }}));

// ── pulse animation state ─────────────────────────────────────────────────────
let pulseRAF    = null;
let pulseNodeId = null;
let pulseFrame  = 0;
let pulseError  = false;

function _stopPulse() {{
  if (pulseRAF) {{ cancelAnimationFrame(pulseRAF); pulseRAF = null; }}
  if (pulseNodeId) {{
    const nc = nodeColors[pulseNodeId];
    if (nc) visNodes.update({{
      id: pulseNodeId,
      borderWidth: 2,
      shadow: {{ enabled: true, color: "rgba(71,31,51,.10)", size: 10, x: 2, y: 4 }},
    }});
  }}
  pulseNodeId = null;
  _hideThinking();
}}

function _startPulse(nodeId, isError) {{
  _stopPulse();
  pulseNodeId = nodeId;
  pulseFrame  = 0;
  pulseError  = isError;

  function step() {{
    pulseFrame++;
    const t = pulseFrame * 0.08;
    const intensity = 0.35 + 0.65 * Math.abs(Math.sin(t * Math.PI));
    const color     = isError ? "220,102,102" : pulseError ? "216,168,63" : "68,194,154";
    visNodes.update({{
      id: nodeId,
      borderWidth: 2 + intensity * 4,
      shadow: {{
        enabled: true,
        color:   `rgba(${{color}},${{(intensity * 0.75).toFixed(2)}})`,
        size:    8 + intensity * 22,
        x: 0, y: 0,
      }},
    }});
    pulseRAF = requestAnimationFrame(step);
  }}
  step();
  _showThinkingAt(nodeId, isError);
}}

// ── thinking overlay ──────────────────────────────────────────────────────────
function _showThinkingAt(nodeId, isError) {{
  const overlay  = document.getElementById("think-overlay");
  const iconEl   = document.getElementById("think-icon");
  const nameEl   = document.getElementById("think-name");
  const positions = network.getPositions([nodeId]);
  if (!positions[nodeId]) return;
  const dom = network.canvasToDOM(positions[nodeId]);
  overlay.style.left = Math.max(4, dom.x - 52) + "px";
  overlay.style.top  = Math.max(4, dom.y - 44) + "px";
  overlay.style.borderColor = isError ? "var(--bad)" : "var(--accent)";
  overlay.style.color       = isError ? "var(--bad)" : "var(--accent)";
  overlay.style.boxShadow   = isError
    ? "0 0 12px rgba(180,35,74,.28)" : "0 0 12px rgba(157,23,77,.24)";
  iconEl.textContent = agentIcons[nodeId] || "🤖";
  nameEl.textContent = nodeId.replace("_", " ");
  overlay.style.display = "block";
  // Update active label in panel head
  const lbl = document.getElementById("activeAgentLabel");
  if (lbl) lbl.textContent = (agentIcons[nodeId] || "") + " " + nodeId.replace("_", " ");
  // Activity bar in topbar
  const bar = document.getElementById("activity-bar");
  if (bar) {{
    bar.classList.add("on");
    const al = document.getElementById("activity-label");
    if (al) al.textContent = nodeId.replace("_", " ");
  }}
}}

function _hideThinking() {{
  const overlay = document.getElementById("think-overlay");
  if (overlay) overlay.style.display = "none";
  const lbl = document.getElementById("activeAgentLabel");
  if (lbl) lbl.textContent = "idle";
  const bar = document.getElementById("activity-bar");
  if (bar) bar.classList.remove("on");
}}

// ── network helpers ───────────────────────────────────────────────────────────
function resetNetwork() {{
  _stopPulse();
  visNodes.update(visNodes.getIds().map(id => ({{
    id,
    color: {{
      ...nodeColors[id],
      highlight: {{ background: nodeColors[id]?.background ?? "#ffffff", border: "#9d174d" }},
      hover:     {{ background: nodeColors[id]?.background ?? "#ffffff", border: "#9d174d" }},
    }},
    borderWidth: 2,
    shadow: {{ enabled: true, color: "rgba(71,31,51,.10)", size: 10, x: 2, y: 4 }},
  }})));
  visEdges.update(visEdges.getIds().map(id => {{
    const e = visEdges.get(id);
    return {{ id, color: e.dashes
      ? {{ color: "rgba(157,23,77,.24)", highlight: "#9a5b13", hover: "#9a5b13" }}
      : {{ color: "rgba(217,196,207,.78)", highlight: "#9d174d", hover: "#9d174d" }},
      width: 1.5 }};
  }}));
}}

function activateNode(nodeId, isError) {{
  if (!nodeColors[nodeId]) return;
  const borderColor = isError ? "#b4234a" : nodeColors[nodeId].border ?? "#9d174d";
  visNodes.update({{
    id: nodeId,
    color: {{
      background: nodeColors[nodeId].background,
      border: borderColor,
      highlight: {{ background: nodeColors[nodeId].background, border: borderColor }},
      hover:     {{ background: nodeColors[nodeId].background, border: borderColor }},
    }},
  }});
  _startPulse(nodeId, isError);
}}

function activateEdge(fromId, toId, isRetry) {{
  const eid = `${{fromId}}-${{toId}}`;
  const e   = visEdges.get(eid);
  if (!e) return;
  visEdges.update({{ id: eid, color: {{ color: isRetry ? "#9a5b13" : "#9d174d" }}, width: 3 }});
}}

function edgeIsRetry(fromId, toId) {{
  const e = visEdges.get(`${{fromId}}-${{toId}}`);
  return e ? !!e.dashes : false;
}}

// ── event feed ────────────────────────────────────────────────────────────────
const state = {{ events: [], filter: "" }};

function eventTime(ev) {{
  try {{ return new Date(ev.created_at).toLocaleTimeString(); }} catch {{ return ""; }}
}}

function esc(v) {{
  return String(v).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

function buildEvidenceSection(ev) {{
  const p = ev.payload || {{}};
  const rows = [];
  if (p.graphrag) {{
    const g = p.graphrag;
    if (g.failure_patterns !== undefined) rows.push(`<div class="evRow"><span>Failure patterns:</span><span>${{g.failure_patterns}}</span></div>`);
    if (g.success_patterns !== undefined) rows.push(`<div class="evRow"><span>Success patterns:</span><span>${{g.success_patterns}}</span></div>`);
    if (g.available_skills !== undefined) rows.push(`<div class="evRow"><span>Available skills:</span><span>${{g.available_skills}}</span></div>`);
    if (g.active_flows     !== undefined) rows.push(`<div class="evRow"><span>Active flows:</span><span>${{g.active_flows}}</span></div>`);
  }}
  if (p.identified_problem_flow) rows.push(`<div class="evRow"><span>Problem flow:</span><span><code>${{esc(p.identified_problem_flow)}}</code></span></div>`);
  if (p.baseline_score !== undefined && ev.source === "planner") rows.push(`<div class="evRow"><span>Baseline score:</span><span>${{p.baseline_score}}</span></div>`);
  const eids = p.evidence_node_ids || [];
  if (eids.length > 0) rows.push(`<div class="evRow"><span>Evidence (${{eids.length}}):</span><span>${{eids.map(id=>`<code>${{esc(id)}}</code>`).join(" ")}}</span></div>`);
  if (p.failed_metric) {{
    const fm = p.failed_metric;
    rows.push(`<div class="evRow"><span>Score:</span><span>${{fm.match_score ?? "—"}} / threshold ${{fm.threshold ?? "—"}}</span></div>`);
    if (fm.sim_status) rows.push(`<div class="evRow"><span>Sim status:</span><span>${{esc(fm.sim_status)}}</span></div>`);
  }}
  if (p.llm_reason && (p.failed_metric || p.decision === "failure")) rows.push(`<div class="evRow"><span>LLM reason:</span><span style="color:var(--muted)">${{esc(p.llm_reason)}}</span></div>`);
  if (p.decision === "success" && p.sim_score !== undefined) {{
    rows.push(
      `<div class="evRow"><span>Decision:</span><span><span class="evTag ok">SUCCESS</span></span></div>` +
      `<div class="evRow"><span>Score:</span><span>${{p.sim_score}} &gt; ${{p.threshold}} <span style="color:var(--muted)">(baseline ${{p.baseline_score}})</span></span></div>`
    );
    if (p.llm_reason) rows.push(`<div class="evRow"><span>LLM reason:</span><span style="color:var(--muted)">${{esc(p.llm_reason)}}</span></div>`);
  }}
  if (p.metrics) {{
    const keys = Object.keys(p.metrics).slice(0, 8);
    if (keys.length) rows.push(`<div class="evRow"><span>Metrics:</span><span>${{keys.map(k => `<code>${{esc(k)}}=${{esc(p.metrics[k])}}</code>`).join(" ")}}</span></div>`);
  }}
  if (rows.length === 0) return "";
  return `<details class="evDetails"><summary>Evidence</summary><div class="evBody">${{rows.join("")}}</div></details>`;
}}

function renderFeed() {{
  const visible = state.events.filter(ev => !state.filter || (ev.thread_id || "").includes(state.filter));
  const cnt = document.getElementById("eventCount");
  if (cnt) cnt.textContent = `${{visible.length}} events`;
  const feed = document.getElementById("feed");
  if (!feed) return;
  feed.innerHTML = visible.slice(-120).reverse().map(ev => {{
    const srcIcon  = esc(agentIcons[ev.source] || "");
    const typeIcon = esc(eventTypeIcons[ev.event_type] || "");
    const source   = esc(ev.source || "");
    const target   = esc(ev.target || "");
    const threadId = esc(ev.thread_id || "");
    const title    = esc(ev.title || "");
    const detail   = esc(ev.detail || "");
    const eventType = esc(ev.event_type || "");
    const arrow    = ev.target ? ` → ${{esc(agentIcons[ev.target] || "")}} ${{target}}` : "";
    return (
      `<article class="event ${{eventType}}">` +
        `<div class="eventTop">` +
          `<span class="eventSource"><span class="agentIcon">${{srcIcon}}</span>${{source}}${{arrow}}</span>` +
          `<span><span class="typeIcon">${{typeIcon}}</span> ${{eventTime(ev)}} · ${{threadId}}</span>` +
        `</div>` +
        `<div class="eventTitle">${{title}}</div>` +
        `<div class="eventDetail">${{detail}}</div>` +
        buildEvidenceSection(ev) +
      `</article>`
    );
  }}).join("") ||
  `<div class="event"><div class="eventTitle">No events yet</div><div class="eventDetail">Start an agent run or trigger a sandbox to see live updates.</div></div>`;
}}

function renderLogs() {{
  const logFeed = document.getElementById("logFeed");
  if (!logFeed) return;
  const rows = state.events
    .filter(ev => ev.event_type === "log" || ev.event_type === "thinking" || ev.event_type === "phase")
    .filter(ev => !state.filter || (ev.thread_id || "").includes(state.filter))
    .slice(-180);
  const logCount = document.getElementById("logCount");
  if (logCount) logCount.textContent = `${{rows.length}} lines`;
  logFeed.innerHTML = rows.map(ev => {{
    const node = (ev.payload && ev.payload.node) || ev.source || "agent";
    const text = ev.event_type === "log"
      ? `${{ev.title || ""}}${{ev.detail ? "\\n" + ev.detail : ""}}`
      : `${{ev.title || ""}}${{ev.detail ? " — " + ev.detail : ""}}`;
    return `<div class="logLine"><span class="logTime">${{eventTime(ev)}}</span><span class="logNode">${{esc(node)}}</span><span class="logText">${{esc(text)}}</span></div>`;
  }}).join("") || `<div class="logLine"><span class="logTime">--:--</span><span class="logNode">system</span><span class="logText">Waiting for an agent execution.</span></div>`;
  logFeed.scrollTop = logFeed.scrollHeight;
}}

function addEvent(ev) {{
  if (!ev || !ev.event_id) return;
  if (state.events.some(e => e.event_id === ev.event_id)) return;
  state.events.push(ev);
  state.events = state.events.slice(-500);

  const isError = ["error", "rejected"].includes(ev.event_type);
  const terminal = ["approved", "rejected"].includes(ev.event_type)
    || (ev.event_type === "decision" && ev.target === "");
  resetNetwork();
  if (ev.source) activateNode(ev.source, isError);
  if (ev.source && ev.target) activateEdge(ev.source, ev.target, edgeIsRetry(ev.source, ev.target));
  if (terminal && ev.source) {{
    activateEdge(ev.source, "end", false);
    activateNode("end", isError);
  }}

  const lastSeen = document.getElementById("lastSeen");
  if (lastSeen) {{
    const icon = eventTypeIcons[ev.event_type] || "";
    lastSeen.textContent = `${{icon}} ${{ev.event_type || "event"}} · ${{eventTime(ev)}}`;
  }}

  // Flash effect on the first card after render (added class "new" then removed)
  renderFeed();
  const feed = document.getElementById("feed");
  if (feed && feed.firstElementChild) {{
    feed.firstElementChild.classList.add("new");
    setTimeout(() => feed.firstElementChild?.classList.remove("new"), 700);
  }}

  renderLogs();

  // Thinking/log/phase events represent active work, so keep the current node
  // visibly alive until the next semantic event arrives.
  clearTimeout(window._pulseTimeout);
  if (!["thinking", "log", "phase"].includes(ev.event_type)) {{
    window._pulseTimeout = setTimeout(() => {{
      _stopPulse();
      resetNetwork();
    }}, 6000);
  }}
}}

// ── initial load + WebSocket ──────────────────────────────────────────────────
async function loadInitial() {{
  initialEvents.forEach(addEvent);
  if (!live) {{
    const st = document.getElementById("status");
    if (st) st.textContent = "Static topology";
    return;
  }}
  try {{
    const res  = await fetch(`${{apiBase}}/events?limit=200`);
    const rows = await res.json();
    rows.forEach(addEvent);
  }} catch (_) {{}}
}}

let wsRetryMs = 1200;
function connect() {{
  if (!live) return;
  const status = document.getElementById("status");
  try {{
    const ws = new WebSocket(wsUrl);
    ws.onopen    = () => {{
      wsRetryMs = 1200;
      if (status) {{ status.textContent = "Realtime connected"; status.className = "pill ok"; }}
    }};
    ws.onmessage = (msg) => addEvent(JSON.parse(msg.data));
    ws.onclose   = () => {{
      if (status) {{ status.textContent = `Realtime disconnected · retrying in ${{Math.round(wsRetryMs / 1000)}}s`; status.className = "pill bad"; }}
      const wait = wsRetryMs;
      wsRetryMs = Math.min(wsRetryMs * 1.8, 12000);
      setTimeout(connect, wait);
    }};
    ws.onerror   = () => {{ if (status) {{ status.textContent = "Realtime disconnected"; status.className = "pill bad"; }} ws.close(); }};
  }} catch (_) {{
    if (status) {{ status.textContent = "Realtime disconnected"; status.className = "pill bad"; }}
  }}
}}

loadInitial();
connect();
renderFeed();
renderLogs();

const fi = document.getElementById("threadFilter");
if (fi) {{
  fi.addEventListener("input", () => {{ state.filter = fi.value.trim(); renderFeed(); renderLogs(); }});
  document.getElementById("clearFilter").addEventListener("click", () => {{
    state.filter = ""; fi.value = ""; renderFeed(); renderLogs();
  }});
}}
</script>
</body>
</html>"""
