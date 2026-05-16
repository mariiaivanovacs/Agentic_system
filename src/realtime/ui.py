from __future__ import annotations

import json


AGENTS = [
    {"id": "planner",        "label": "Planner",        "x": -260, "y": -30,  "role": "Finds graph evidence & forms hypothesis"},
    {"id": "generator",      "label": "Generator",      "x": -110, "y": -165, "role": "Drafts candidate flow YAML"},
    {"id": "critic",         "label": "Critic",         "x":   55, "y": -165, "role": "Validates skills, connectors & infra"},
    {"id": "simulator",      "label": "Simulator",      "x":  210, "y": -165, "role": "Runs sandbox test & records metrics"},
    {"id": "evaluator",      "label": "Evaluator",      "x":  210, "y":  110, "role": "Compares simulation score to baseline"},
    {"id": "human_approval", "label": "Human Approval", "x":   55, "y":  110, "role": "Admin approves or rejects proposal"},
]

# Per-agent visual colours (dark-background palette, matching Graph Display page style)
AGENT_COLORS = {
    "planner":        {"background": "#1c2d3a", "border": "#70a9ff"},
    "generator":      {"background": "#2e2410", "border": "#d8a83f"},
    "critic":         {"background": "#2e1810", "border": "#e07845"},
    "simulator":      {"background": "#0f2820", "border": "#44c29a"},
    "evaluator":      {"background": "#221535", "border": "#9a70cc"},
    "human_approval": {"background": "#2a1822", "border": "#c06888"},
}

# curve/smooth settings are set per edge to fix the broken implicit logic
# that previously applied a +44 downward bias to ALL edges ending at "generator".
EDGES = [
    {
        "from": "planner",   "to": "generator",
        "label": "hypothesis",
        "smooth": {"enabled": False},
        "retry": False,
    },
    {
        "from": "generator", "to": "critic",
        "label": "flow yaml",
        "smooth": {"type": "curvedCW", "roundness": 0.12},
        "retry": False,
    },
    {
        "from": "critic",    "to": "simulator",
        "label": "pass",
        "smooth": {"enabled": False},
        "retry": False,
    },
    {
        "from": "critic",    "to": "generator",
        "label": "retry",
        "smooth": {"type": "curvedCW", "roundness": 0.50},
        "retry": True,
    },
    {
        "from": "simulator", "to": "evaluator",
        "label": "metrics",
        "smooth": {"enabled": False},
        "retry": False,
    },
    {
        "from": "evaluator", "to": "human_approval",
        "label": "proposal",
        "smooth": {"enabled": False},
        "retry": False,
    },
    {
        "from": "evaluator", "to": "generator",
        "label": "retry",
        "smooth": {"type": "curvedCCW", "roundness": 0.48},
        "retry": True,
    },
]


def agent_map_html() -> str:
    return _shell_html(initial_events=[], live=False)


def live_comms_html(initial_events: list[dict], api_base: str, ws_url: str) -> str:
    return _shell_html(
        initial_events=initial_events,
        live=True,
        api_base=api_base,
        ws_url=ws_url,
    )


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
                "highlight": {"background": AGENT_COLORS[ag["id"]]["background"], "border": "#44c29a"},
                "hover":     {"background": AGENT_COLORS[ag["id"]]["background"], "border": "#44c29a"},
            },
        }
        for ag in AGENTS
    ]


def _build_vis_edges() -> list[dict]:
    rows = []
    for e in EDGES:
        edge: dict = {
            "id":     f"{e['from']}-{e['to']}",
            "from":   e["from"],
            "to":     e["to"],
            "label":  e["label"],
            "smooth": e["smooth"],
        }
        if e["retry"]:
            edge["dashes"] = True
            edge["color"]  = {"color": "rgba(184,176,156,.32)", "highlight": "#d8a83f", "hover": "#d8a83f"}
        else:
            edge["color"]  = {"color": "rgba(184,176,156,.50)", "highlight": "#44c29a", "hover": "#44c29a"}
        rows.append(edge)
    return rows


def _shell_html(
    *,
    initial_events: list[dict],
    live: bool,
    api_base: str = "http://127.0.0.1:8765",
    ws_url: str = "ws://127.0.0.1:8765/ws/events",
) -> str:
    vis_nodes_json = json.dumps(_build_vis_nodes())
    vis_edges_json = json.dumps(_build_vis_edges())
    # Keep Python-side colour lookup for JS activation logic
    node_colors_json = json.dumps(AGENT_COLORS)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
  :root {{
    --bg: #141511;
    --line: #4b4a3d;
    --text: #f7f1e4;
    --muted: #b8b09c;
    --accent: #44c29a;
    --gold: #d8a83f;
    --bad: #dc6666;
    --blue: #70a9ff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  .wrap {{
    padding: 16px;
    background:
      linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px),
      linear-gradient(180deg, rgba(255,255,255,.025) 1px, transparent 1px),
      radial-gradient(circle at 14% 14%, rgba(68,194,154,.14), transparent 30%),
      #141511;
    background-size: 28px 28px, 28px 28px, auto, auto;
  }}
  .topbar {{
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
    margin-bottom: 12px;
  }}
  .title {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 24px;
    flex: 1;
  }}
  .pill {{
    border: 1px solid var(--line); border-radius: 999px;
    padding: 6px 12px; color: var(--muted);
    background: rgba(255,255,255,.04); font-size: 12px; white-space: nowrap;
  }}
  .pill.ok  {{ color: var(--accent); border-color: rgba(68,194,154,.45); }}
  .pill.bad {{ color: var(--bad);    border-color: rgba(220,102,102,.45); }}
  .grid {{
    display: grid;
    grid-template-columns: minmax(380px, 1.3fr) minmax(270px, .7fr);
    gap: 12px;
  }}
  .mapOnly {{ grid-template-columns: 1fr; }}
  .panel {{
    border: 1px solid var(--line); border-radius: 8px;
    background: linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,.022));
    box-shadow: 0 16px 40px rgba(0,0,0,.28); overflow: hidden;
  }}
  .panelHead {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 10px 14px;
    border-bottom: 1px solid var(--line); color: var(--muted); font-size: 12px;
  }}
  #network-container {{
    height: 520px;
    background: transparent;
  }}
  /* vis.js tooltip override */
  .vis-tooltip {{
    background: #1e201a !important;
    border: 1px solid #4b4a3d !important;
    color: #f7f1e4 !important;
    font-size: 12px !important;
    border-radius: 6px !important;
    padding: 6px 10px !important;
  }}
  .legend {{
    display: flex; flex-wrap: wrap; gap: 8px;
    padding: 8px 14px 10px; border-top: 1px solid var(--line);
  }}
  .legend span {{
    color: var(--muted); border: 1px solid var(--line);
    border-radius: 999px; padding: 3px 9px; font-size: 11px;
  }}
  .feedControls {{
    display: grid; grid-template-columns: 1fr auto; gap: 8px;
    padding: 10px 12px; border-bottom: 1px solid var(--line);
  }}
  input {{
    border: 1px solid var(--line); background: rgba(0,0,0,.18);
    color: var(--text); border-radius: 7px; padding: 8px 10px; outline: none;
    width: 100%;
  }}
  button {{
    border: 1px solid rgba(68,194,154,.45); background: rgba(68,194,154,.1);
    color: var(--accent); border-radius: 7px; padding: 7px 11px; cursor: pointer;
  }}
  .feed {{ height: 450px; overflow: auto; padding: 10px 12px 12px; }}
  .event {{
    border-left: 3px solid var(--line); padding: 9px 9px 9px 11px;
    margin: 0 0 8px; background: rgba(255,255,255,.04); border-radius: 0 6px 6px 0;
  }}
  .event.result              {{ border-left-color: var(--accent); }}
  .event.decision,
  .event.approval_required   {{ border-left-color: var(--gold); }}
  .event.error,
  .event.rejected             {{ border-left-color: var(--bad); }}
  .event.approved             {{ border-left-color: var(--blue); }}
  .eventTop {{
    display: flex; justify-content: space-between; gap: 8px;
    color: var(--muted); font-size: 11px; margin-bottom: 4px;
  }}
  .eventTitle  {{ font-weight: 700; margin-bottom: 4px; font-size: 13px; }}
  .eventDetail {{ color: #ded6c4; font-size: 12px; line-height: 1.38; white-space: pre-wrap; }}
  .evDetails {{
    margin-top: 7px; border-top: 1px solid rgba(75,74,61,.6);
    padding-top: 5px;
  }}
  .evDetails summary {{
    cursor: pointer; color: var(--muted); font-size: 11px; letter-spacing: .04em;
    user-select: none; list-style: none; display: flex; align-items: center; gap: 5px;
  }}
  .evDetails summary::before {{ content: "▶"; font-size: 8px; transition: transform .15s; }}
  .evDetails[open] summary::before {{ transform: rotate(90deg); }}
  .evBody {{
    margin-top: 6px; display: flex; flex-direction: column; gap: 4px;
  }}
  .evRow {{
    display: flex; gap: 8px; font-size: 11px; align-items: baseline;
  }}
  .evRow span:first-child {{
    color: var(--muted); min-width: 110px; flex-shrink: 0;
  }}
  .evRow span:last-child {{ color: #ded6c4; word-break: break-all; }}
  .evRow code {{
    font-family: ui-monospace, monospace; font-size: 10px;
    background: rgba(255,255,255,.06); padding: 1px 4px; border-radius: 3px;
    color: var(--blue);
  }}
  .evTag {{
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 10px; font-weight: 700; letter-spacing: .04em;
  }}
  .evTag.ok  {{ background: rgba(68,194,154,.18); color: var(--accent); }}
  .evTag.bad {{ background: rgba(220,102,102,.18); color: var(--bad); }}
  @media (max-width: 820px) {{
    .grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="title">{'Live Agent Communications' if live else 'Agent Communication Map'}</div>
    <div id="status" class="pill">Static topology</div>
    <div id="eventCount" class="pill">0 events</div>
    <div class="pill">LangGraph</div>
  </div>
  <div class="grid {'mapOnly' if not live else ''}">
    <section class="panel">
      <div class="panelHead">
        <span>Agent network</span>
        <span>6 agents · retry loops shown</span>
      </div>
      <div id="network-container"></div>
      <div class="legend">
        <span style="border-color:rgba(68,194,154,.5);color:var(--accent)">&#9679; active node</span>
        <span style="border-color:rgba(216,168,63,.5);color:var(--gold)">&#9679; retry active</span>
        <span>&#8212; main flow</span>
        <span style="letter-spacing:2px">&#xB7;&#xB7;&#xB7; retry</span>
        <span style="color:var(--bad)">&#9679; error</span>
      </div>
    </section>
    {'<section class="panel"><div class="panelHead"><span>Realtime event feed</span><span id="lastSeen">waiting for events</span></div><div class="feedControls"><input id="threadFilter" placeholder="Filter by thread id" /><button id="clearFilter">All</button></div><div id="feed" class="feed"></div></section>' if live else ''}
  </div>
</div>

<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<script>
// ── data ──────────────────────────────────────────────────────────────────────
const visNodesData  = {vis_nodes_json};
const visEdgesData  = {vis_edges_json};
const nodeColors    = {node_colors_json};
const initialEvents = {json.dumps(initial_events)};
const live          = {json.dumps(live)};
const apiBase       = {json.dumps(api_base)};
const wsUrl         = {json.dumps(ws_url)};

// ── vis.js network ────────────────────────────────────────────────────────────
const visNodes = new vis.DataSet(visNodesData);
const visEdges = new vis.DataSet(visEdgesData);

const networkOptions = {{
  physics: false,
  nodes: {{
    shape: "dot",
    size: 20,
    font: {{
      face: "Georgia, 'Times New Roman', serif",
      size: 13,
      color: "#f7f1e4",
      strokeWidth: 2,
      strokeColor: "#141511",
    }},
    borderWidth: 2,
    shadow: {{ enabled: true, color: "rgba(0,0,0,.45)", size: 10, x: 2, y: 4 }},
  }},
  edges: {{
    arrows: {{ to: {{ enabled: true, scaleFactor: 0.65 }} }},
    width: 1.5,
    selectionWidth: 0,
    font: {{ size: 10, color: "#b8b09c", align: "middle", strokeWidth: 2, strokeColor: "#141511" }},
  }},
  interaction: {{
    hover: true,
    tooltipDelay: 100,
    zoomView: false,
    dragView: false,
    dragNodes: false,
    multiselect: false,
    selectable: false,
  }},
}};

const container = document.getElementById("network-container");
const network   = new vis.Network(container, {{ nodes: visNodes, edges: visEdges }}, networkOptions);
network.once("afterDrawing", () => network.fit({{ animation: false }}));

// ── activation helpers ────────────────────────────────────────────────────────
function resetNetwork() {{
  visNodes.update(
    visNodes.getIds().map(id => ({{
      id,
      color: {{
        ...nodeColors[id],
        highlight: {{ background: nodeColors[id] ? nodeColors[id].background : "#1e201a", border: "#44c29a" }},
        hover:     {{ background: nodeColors[id] ? nodeColors[id].background : "#1e201a", border: "#44c29a" }},
      }},
      borderWidth: 2,
      shadow: {{ enabled: true, color: "rgba(0,0,0,.45)", size: 10, x: 2, y: 4 }},
    }}))
  );
  visEdges.update(
    visEdges.getIds().map(id => {{
      const e = visEdges.get(id);
      return {{
        id,
        color: e.dashes
          ? {{ color: "rgba(184,176,156,.32)", highlight: "#d8a83f", hover: "#d8a83f" }}
          : {{ color: "rgba(184,176,156,.50)", highlight: "#44c29a", hover: "#44c29a" }},
        width: 1.5,
      }};
    }})
  );
}}

function activateNode(nodeId) {{
  if (!nodeColors[nodeId]) return;
  const isError = false; // could check event_type
  visNodes.update({{
    id: nodeId,
    color: {{
      background: nodeColors[nodeId].background,
      border: "#44c29a",
      highlight: {{ background: nodeColors[nodeId].background, border: "#44c29a" }},
      hover:     {{ background: nodeColors[nodeId].background, border: "#44c29a" }},
    }},
    borderWidth: 4,
    shadow: {{ enabled: true, color: "rgba(68,194,154,.5)", size: 20, x: 0, y: 0 }},
  }});
}}

function activateEdge(fromId, toId, isRetry) {{
  const eid = `${{fromId}}-${{toId}}`;
  const e   = visEdges.get(eid);
  if (!e) return;
  visEdges.update({{
    id: eid,
    color: {{ color: isRetry ? "#d8a83f" : "#44c29a" }},
    width: 3,
  }});
}}

function edgeIsRetry(fromId, toId) {{
  const e = visEdges.get(`${{fromId}}-${{toId}}`);
  return e ? !!e.dashes : false;
}}

// ── event handling ────────────────────────────────────────────────────────────
const state = {{ events: [], filter: "", activeThread: "", loadingInitial: false }};

function eventTime(ev) {{
  try {{ return new Date(ev.created_at).toLocaleTimeString(); }} catch {{ return ""; }}
}}

// ── evidence section builder ─────────────────────────────────────────────────
function esc(v) {{
  return String(v).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

function buildEvidenceSection(ev) {{
  const p = ev.payload || {{}};
  const rows = [];

  // Planner: GraphRAG counts + identified problem flow
  if (p.graphrag) {{
    const g = p.graphrag;
    if (g.failure_patterns !== undefined)
      rows.push(`<div class="evRow"><span>Failure patterns:</span><span>${{g.failure_patterns}}</span></div>`);
    if (g.success_patterns !== undefined)
      rows.push(`<div class="evRow"><span>Success patterns:</span><span>${{g.success_patterns}}</span></div>`);
    if (g.available_skills !== undefined)
      rows.push(`<div class="evRow"><span>Available skills:</span><span>${{g.available_skills}}</span></div>`);
    if (g.active_flows !== undefined)
      rows.push(`<div class="evRow"><span>Active flows:</span><span>${{g.active_flows}}</span></div>`);
  }}
  if (p.identified_problem_flow)
    rows.push(`<div class="evRow"><span>Problem flow:</span><span><code>${{esc(p.identified_problem_flow)}}</code></span></div>`);
  if (p.baseline_score !== undefined && ev.source === "planner")
    rows.push(`<div class="evRow"><span>Baseline score:</span><span>${{p.baseline_score}}</span></div>`);

  // Critic: grounded evidence node IDs
  const eids = p.evidence_node_ids || [];
  if (eids.length > 0) {{
    const idHtml = eids.map(id => `<code>${{esc(id)}}</code>`).join(" ");
    rows.push(`<div class="evRow"><span>Evidence nodes (${{eids.length}}):</span><span>${{idHtml}}</span></div>`);
  }}

  // Evaluator retry: failed metric
  if (p.failed_metric) {{
    const fm = p.failed_metric;
    rows.push(`<div class="evRow"><span>Score:</span><span>${{fm.match_score !== undefined ? fm.match_score : "—"}} <span style="color:var(--muted)">/ threshold</span> ${{fm.threshold !== undefined ? fm.threshold : "—"}}</span></div>`);
    if (fm.sim_status)
      rows.push(`<div class="evRow"><span>Sim status:</span><span>${{esc(fm.sim_status)}}</span></div>`);
  }}
  if (p.llm_reason && (p.failed_metric || p.decision === "failure"))
    rows.push(`<div class="evRow"><span>LLM reason:</span><span style="color:var(--muted)">${{esc(p.llm_reason)}}</span></div>`);

  // Evaluator success: decision + deterministic score comparison
  if (p.decision === "success" && p.sim_score !== undefined) {{
    rows.push(
      `<div class="evRow"><span>Decision:</span><span><span class="evTag ok">SUCCESS</span></span></div>` +
      `<div class="evRow"><span>Score:</span><span>${{p.sim_score}} &gt; ${{p.threshold}} <span style="color:var(--muted)">(baseline ${{p.baseline_score}})</span></span></div>`
    );
    if (p.llm_reason)
      rows.push(`<div class="evRow"><span>LLM reason:</span><span style="color:var(--muted)">${{esc(p.llm_reason)}}</span></div>`);
  }}

  if (rows.length === 0) return "";
  return (
    `<details class="evDetails">` +
      `<summary>Evidence</summary>` +
      `<div class="evBody">${{rows.join("")}}</div>` +
    `</details>`
  );
}}

function renderFeed() {{
  const visible = state.events.filter(
    ev => !state.filter || (ev.thread_id || "").includes(state.filter)
  );
  const cnt = document.getElementById("eventCount");
  if (cnt) cnt.textContent = `${{visible.length}} events`;
  const feed = document.getElementById("feed");
  if (!feed) return;
  feed.innerHTML = visible.slice(-120).reverse().map(ev =>
    `<article class="event ${{ev.event_type || ""}}">` +
      `<div class="eventTop">` +
        `<span>${{ev.source || ""}}${{ev.target ? " → " + ev.target : ""}}</span>` +
        `<span>${{eventTime(ev)}} · ${{ev.thread_id || ""}}</span>` +
      `</div>` +
      `<div class="eventTitle">${{ev.title || ""}}</div>` +
      `<div class="eventDetail">${{ev.detail || ""}}</div>` +
      buildEvidenceSection(ev) +
    `</article>`
  ).join("") ||
  `<div class="event"><div class="eventTitle">No events yet</div>` +
  `<div class="eventDetail">Start an agent run, sandbox run, website ingest, or approval action.</div></div>`;
}}

function updateActiveThread(ev) {{
  if (!ev || !ev.thread_id) return;
  if (ev.source === "ui" && ev.target === "planner") {{
    state.activeThread = ev.thread_id;
    return;
  }}
  if (!state.activeThread) state.activeThread = ev.thread_id;
}}

function activeNodeFor(ev) {{
  if (!ev) return "";
  if (ev.source && nodeColors[ev.source]) return ev.source;
  if (ev.target && nodeColors[ev.target]) return ev.target;
  return "";
}}

function animateEvent(ev) {{
  const nodeId = activeNodeFor(ev);
  if (!nodeId) return;
  resetNetwork();
  activateNode(nodeId);
  if (ev.source && ev.target) activateEdge(ev.source, ev.target, edgeIsRetry(ev.source, ev.target));
}}

function replayLatestActiveEvent() {{
  updateActiveThread(state.events[state.events.length - 1]);
  const latest = [...state.events].reverse().find(ev =>
    (!state.activeThread || ev.thread_id === state.activeThread) && activeNodeFor(ev)
  );
  if (latest) animateEvent(latest);
}}

function addEvent(ev) {{
  if (!ev || !ev.event_id) return;
  if (state.events.some(e => e.event_id === ev.event_id)) return;
  state.events.push(ev);
  state.events = state.events.slice(-500);

  updateActiveThread(ev);
  const belongsToActiveRun = !state.activeThread || ev.thread_id === state.activeThread;
  if (!state.loadingInitial && belongsToActiveRun) animateEvent(ev);

  const lastSeen = document.getElementById("lastSeen");
  if (lastSeen) {{
    const threadLabel = state.activeThread ? ` · thread ${{state.activeThread}}` : "";
    lastSeen.textContent = `${{ev.event_type || "event"}} · ${{eventTime(ev)}}${{threadLabel}}`;
  }}
  renderFeed();
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
    state.loadingInitial = true;
    rows
      .sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0))
      .forEach(addEvent);
    state.loadingInitial = false;
    replayLatestActiveEvent();
  }} catch (_) {{}}
}}

function connect() {{
  if (!live) return;
  const status = document.getElementById("status");
  try {{
    const ws = new WebSocket(wsUrl);
    ws.onopen    = () => {{ if (status) {{ status.textContent = "Realtime connected"; status.className = "pill ok"; }} }};
    ws.onmessage = (msg) => addEvent(JSON.parse(msg.data));
    ws.onclose   = () => {{ if (status) {{ status.textContent = "Realtime disconnected"; status.className = "pill bad"; }} setTimeout(connect, 2500); }};
    ws.onerror   = () => {{ if (status) {{ status.textContent = "Realtime disconnected"; status.className = "pill bad"; }} ws.close(); }};
  }} catch (_) {{
    if (status) {{ status.textContent = "Realtime disconnected"; status.className = "pill bad"; }}
  }}
}}

loadInitial();
connect();
renderFeed();

const fi = document.getElementById("threadFilter");
if (fi) {{
  fi.addEventListener("input", () => {{ state.filter = fi.value.trim(); renderFeed(); }});
  document.getElementById("clearFilter").addEventListener("click", () => {{
    state.filter = ""; fi.value = ""; renderFeed();
  }});
}}
</script>
</body>
</html>"""
