# EcoLink NeuroCore Agentic System

EcoLink NeuroCore is a local agentic platform for analyzing, optimizing, and visualizing a mentor-startup matching system. It combines:

- **Neo4j graph memory** for historical matches, flows, infrastructure, website entities, and proposals.
- **LangGraph agent pipeline** for Planner -> Generator -> Critic -> Simulator -> Evaluator -> Human Approval.
- **Secure sandbox simulation** for testing proposed flows before approval.
- **Streamlit dashboard** for the operator UI.
- **FastAPI WebSocket sidecar** for realtime agent communication visualization.
- **Website ingestion** for extracting identities/entities from an existing app and storing them in Neo4j.

## Project Structure

```text
.
├── main.py                    # CLI entrypoint for agent runs and approval
├── streamlit_app.py           # Main Streamlit dashboard
├── requirements.txt           # Python dependencies
├── test_integration.py        # Neo4j + agent-tool integration checks
├── test_sandbox.py            # Sandbox simulation checks
├── test_realtime.py           # Realtime event server checks
├── src/
│   ├── agents/                # LangGraph state, graph, nodes, and tools
│   ├── indexer/               # System and website indexers
│   ├── realtime/              # Event bus, FastAPI server, realtime UI HTML
│   └── config/                # Graph schema metadata
├── ecolink-graph/             # Neo4j query helpers and graph utilities
└── sandbox-system/            # Local/cloud sandbox executor
```

## Prerequisites

- Python 3.11+
- Neo4j AuraDB or local Neo4j instance
- Google Gemini API key for full agent runs
- Node.js/npm only if you want to run the sibling fundraising sample app

> Note: if Gemini returns `429 RESOURCE_EXHAUSTED`, your Google AI Studio project has hit its spending cap. Non-LLM UI, sandbox, realtime, and website-ingestion flows can still work.

## Setup

```bash
cd /Users/mariaivanova/Desktop/Agentic_System

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` and set at least:

```text
NEO4J_URI=...
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j
GOOGLE_API_KEY=...
```

For local sandbox execution:

```text
SANDBOX_MOCK=false
SANDBOX_MODE=local
```

## Run The App

Start the realtime event server in one terminal:

```bash
cd /Users/mariaivanova/Desktop/Agentic_System
source .venv/bin/activate
uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload
```

Start the Streamlit dashboard in another terminal:

```bash
cd /Users/mariaivanova/Desktop/Agentic_System
source .venv/bin/activate
SANDBOX_MOCK=false SANDBOX_MODE=local STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
streamlit run streamlit_app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true
```

Open:

- Dashboard: http://127.0.0.1:8501
- Realtime health check: http://127.0.0.1:8765/health

## Dashboard Pages

- **Command Center**: high-level counts, flow portfolio, traces, low-scoring matches.
- **Graph View**: interactive graph for history, infrastructure, website data, and execution traces.
- **Agent Map**: static visualization of agent communication topology.
- **Live Agent Comms**: realtime WebSocket feed of agent/UI/sandbox/indexer events.
- **Website Ingest**: crawl a website and extract source-code identities/entities into Neo4j.
- **Agent Run**: run the LangGraph optimizer from the UI.
- **GraphRAG Context**: inspect the live graph evidence used by Planner and Critic.
- **Sandbox**: create local/cloud sandbox runs from YAML.
- **Flows**: inspect active/proposed flows.
- **Proposals**: approve or reject proposed changes.
- **Infrastructure**: inspect server load/error-rate status.
- **History**: inspect historical matches and execution traces.

## Run Agent From CLI

```bash
python main.py --goal "Improve match quality for Healthtech startups"
```

If the graph pauses for approval, it prints a thread id:

```bash
python main.py --thread-id <THREAD_ID> --approve
python main.py --thread-id <THREAD_ID> --reject --reason "Too risky"
```

Agent run records are stored locally in `.agent_runs/`.

## GraphRAG

The integrated GraphRAG package lives in:

```text
src/graphrag/
```

It retrieves live Neo4j context for the agent:

- industry performance
- failed match subgraphs
- successful match subgraphs
- active flows
- available skills/connectors
- infrastructure status
- website/code entities
- learning events

Run the context retriever:

```bash
python -m src.graphrag.main_graphrag \
  --goal "Improve match quality for Healthtech startups" \
  --industry Healthtech
```

Run the GraphRAG test:

```bash
python test_graphrag.py
```

GraphRAG intentionally does **not** fall back to synthetic data. If Neo4j is unreachable, it fails so the agent does not optimize from fake context.

## Realtime Events

Realtime events are written to:

```text
.agent_events/events.jsonl
```

The event server exposes:

```text
GET  /health
GET  /events?limit=200
POST /events
WS   /ws/events
```

Manual smoke event:

```bash
curl -X POST http://127.0.0.1:8765/events \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "manual-test",
    "source": "planner",
    "target": "generator",
    "event_type": "message",
    "title": "Manual realtime test",
    "detail": "This should appear in Live Agent Comms."
  }'
```

## Website Ingestion

To test with the sibling fundraising app:

```bash
cd /Users/mariaivanova/Desktop/fundraising_app/Crowd-Funding-App/client
npm run dev -- --host 127.0.0.1 --port 5173
```

Then from this repo:

```bash
python -m src.indexer.runner \
  --type web \
  --source http://127.0.0.1:5173 \
  --source-path ../fundraising_app/Crowd-Funding-App \
  --depth 1 \
  --max-pages 30 \
  --clear
```

Expected sample extraction:

```text
Campaign: 3
Person: 8
Route: 4
ContractMethod: 7
```

You can also use the **Website Ingest** page in Streamlit.

## Sandbox

Run sandbox tests:

```bash
python test_sandbox.py
```

Run a sandbox from the UI:

1. Open **Sandbox**.
2. Keep target as `local`.
3. Use the default flow YAML.
4. Click **Create Sandbox Run**.

Successful runs log execution traces and publish realtime events.

## Tests And Validation

Run the main checks:

```bash
python -m py_compile streamlit_app.py main.py src/agents/nodes.py src/agents/state.py src/realtime/event_bus.py src/realtime/server.py src/realtime/ui.py test_realtime.py
python test_realtime.py
python test_sandbox.py
python test_integration.py
```

Useful browser/manual checks:

1. Open **Agent Map** and confirm all nodes/edges render.
2. Open **Live Agent Comms** and confirm realtime status is connected.
3. POST a manual realtime event and confirm it appears without refresh.
4. Run a sandbox and confirm started/result events appear.
5. Ingest the fundraising website and confirm indexer events appear.
6. Approve/reject a proposal and confirm the approval event appears.

## Current Known Issues

- Full agent runs require a working Gemini API key and available billing/spend cap.
- If Gemini quota is exhausted, the run fails during Planner.
- Failed LLM runs currently publish the start event but may not publish a final terminal error event.
- Sandbox realtime payloads can be verbose because they include trace data.
- Neo4j may warn about `LearningEvent` label/properties until learning events are created.

## Troubleshooting

### Streamlit is not reachable

```bash
lsof -ti tcp:8501
```

Kill old processes if needed:

```bash
for pid in $(lsof -ti tcp:8501); do kill $pid; done
```

### Realtime server is not connected

```bash
curl http://127.0.0.1:8765/health
```

If it fails, restart:

```bash
uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload
```

### Neo4j connectivity fails

Check `.env`:

```text
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
NEO4J_DATABASE
```

Then run:

```bash
python test_integration.py
```

### Gemini quota fails

If you see:

```text
429 RESOURCE_EXHAUSTED
```

Increase the Google AI Studio spend cap or use a project/key with available quota.
