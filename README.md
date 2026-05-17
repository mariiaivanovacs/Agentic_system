# EcoLink NeuroCore

EcoLink NeuroCore is an agentic analysis layer for an existing software project. It connects to a local codebase, indexes the real architecture into Neo4j, retrieves graph evidence with GraphRAG, proposes workflow improvements, tests approved flows in an isolated sandbox, and keeps a human operator in control through Streamlit.

The project is no longer just a mentor-matching demo. It is a project-aware operating layer for inspecting software systems, understanding business workflows, and safely promoting tested recommendations into a graph registry.

## What It Does

- **Connects to a real project**: the operator approves a local repository path before analysis starts.
- **Builds a software graph**: files, routes, functions, services, datastores, integrations, business flows, flow steps, risks, skills, proposals, and runtime objects are written to Neo4j.
- **Retrieves grounded context**: GraphRAG reads the current graph before the agent plans or critiques a change.
- **Runs an agent pipeline**: Planner -> Generator -> Critic -> Simulator -> Evaluator -> Human Approval.
- **Tests before promotion**: sandbox runs execute through Google Cloud Run Jobs and parse Cloud Logging traces.
- **Records approvals**: merging to the registry sets a Flow active and writes `RegistryMergeEvent` audit metadata in Neo4j.
- **Shows operator views**: Streamlit pages expose project review, graph display, flows, sandbox runs, system map, agent architecture, retry inspection, and results.

## Architecture

```text
Local project
  -> Project Review approval
  -> Codebase analyzer
  -> Neo4j project graph
  -> GraphRAG retrieval
  -> LangGraph agent pipeline
  -> Sandbox validation
  -> Human merge to registry
  -> Active Flow + RegistryMergeEvent
```

Main subsystems:

- `streamlit_app.py` — operator dashboard and approval UI.
- `main.py` — CLI entrypoint for agent runs.
- `src/indexer/` — codebase, website, database, OpenAPI, and graph-writing pipeline.
- `src/agents/` — LangGraph state, nodes, graph, tools, sandbox dispatch, and Neo4j write helpers.
- `src/graphrag/` — graph retrieval and prompt-context assembly.
- `src/realtime/` — FastAPI event server and WebSocket UI feed.
- `sandbox-system/` — Cloud Run/local sandbox entrypoints.
- `ecolink-graph/` — graph data/helpers retained from the earlier graph package.

## Core Pages

- **Project Review**: approve a repository path, run analysis, inspect extracted workflows, storage, primitives, and risks.
- **Graph Display**: inspect project graph scopes, including software architecture, workflow pipeline, storage/risk, and agentic layer links.
- **Real-Time Agents**: view LangGraph agent activity and live event logs.
- **Flows**: review detected business flows and pending optimization proposals.
- **Sandbox**: configure Cloud Run sandbox, build Neo4j snapshots, run approved flows, and merge passing flows to the registry.
- **Agentic Architecture**: inspect skills, artifacts, primitives, sandbox architecture, GraphRAG evidence, and agent run controls.
- **System Map**: inspect Neo4j counts, external service configuration, active flow topology, integrations, and agent-tool wiring.
- **Retry Inspector**: create and approve tested architecture sandbox proposals.
- **Flow Results**: inspect latest sandbox results from approval actions.
- **Chat**: issue approval/rejection commands and inspect agent context.

## Prerequisites

- Python 3.11+
- Neo4j AuraDB or local Neo4j
- Google Gemini API key for LLM agent runs
- Google Cloud project for Cloud Run sandbox execution
- `gcloud` authenticated locally if using Cloud Run from the dashboard

The Streamlit UI and graph inspection can still run when Gemini quota is unavailable. Full agent planning needs a valid `GOOGLE_API_KEY`.

## Setup

```bash
cd /Users/mariaivanova/Desktop/Agentic_System

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Set the required Neo4j and Google values in `.env`:

```text
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j

GOOGLE_API_KEY=...

GOOGLE_CLOUD_PROJECT=...
GOOGLE_CLOUD_LOCATION=us-central1
SANDBOX_MOCK=false
SANDBOX_MODE=cloudrun
SANDBOX_GCP_REGION=us-central1
SANDBOX_JOB_NAME=ecolink-sandbox-executor
SANDBOX_INVOKER_SERVICE_ACCOUNT=sandbox-job-invoker@<project>.iam.gserviceaccount.com
SANDBOX_SOURCE_BUCKET=...
SANDBOX_SOURCE_PATH=/absolute/path/to/project
```

Capability tokens for production Cloud Run sandbox runs use RS256 signing through Cloud KMS:

```text
CAPABILITY_TOKEN_AUDIENCE=ecolink-sandbox-job
CAPABILITY_TOKEN_TTL_SECONDS=600
CAPABILITY_KMS_KEY_VERSION=projects/<project>/locations/us-central1/keyRings/ecolink-sandbox/cryptoKeys/capability-jwt/cryptoKeyVersions/1
CAPABILITY_JWT_PUBLIC_KEY_PATH=/absolute/path/to/capability-jwt-public.pem
```

See [docs/cloud_sandbox_hardening.md](docs/cloud_sandbox_hardening.md) for Cloud Run IAM, KMS, and deployment details.

## Run

Start the realtime event server:

```bash
uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload
```

Start the Streamlit dashboard:

```bash
streamlit run streamlit_app.py \
  --server.port 8501 \
  --server.address 127.0.0.1 \
  --server.headless true
```

Open:

- Dashboard: http://127.0.0.1:8501
- Realtime health: http://127.0.0.1:8765/health

## Typical Operator Workflow

1. Open **Project Review**.
2. Enter a local repository path and approve analysis.
3. Inspect generated business flows, storage signals, risks, and primitives.
4. Open **Graph Display** to inspect architecture and workflow scopes.
5. Run an optimization from **Flows** or **Agent Run**.
6. Review proposed changes and sandbox evidence.
7. Open **Sandbox** -> **Approved Flows + Test + Deploy**.
8. Run the Cloud Run sandbox test for an approved flow.
9. If it passes, click **Merge to Registry (set active)**.
10. Confirm the merge panel shows the stored registry event.

Merge events are persisted as:

```text
(RegistryMergeEvent)-[:MERGED_FLOW]->(Flow)
```

The active Flow also receives:

```text
status = "active"
activated_at
last_registry_merge_at
last_registry_merge_by
last_registry_merge_source
registry_merge_count
```

## CLI Agent Run

```bash
python main.py --goal "Improve the selected project workflow"
```

If the graph pauses for approval:

```bash
python main.py --thread-id <THREAD_ID> --approve
python main.py --thread-id <THREAD_ID> --reject --reason "Too risky"
```

Local run records are written to `.agent_runs/`.

## GraphRAG

GraphRAG lives in `src/graphrag/` and retrieves live Neo4j context:

- selected project software nodes
- business flows and flow steps
- active flows
- skills and connectors
- failure/success patterns
- infrastructure state
- execution and learning events

Run it directly:

```bash
python -m src.graphrag.main_graphrag \
  --goal "Improve the selected project workflow" \
  --industry Auto
```

GraphRAG intentionally does not fall back to fake data. If Neo4j is unavailable, retrieval fails.

## Sandbox

The dashboard sandbox path is Cloud Run-first:

- the UI prepares flow YAML and a sanitized Neo4j snapshot
- a capability JWT scopes the run
- Cloud Run executes `sandbox-system/sandbox_task.py`
- Cloud Logging is polled for `DATA_STREAM_START` / `DATA_STREAM_END`
- Streamlit shows score, baseline, trace rows, execution URL, logs URL, and job URL

Useful sandbox files:

- `src/agents/tools.py` — `_cloud_run_sandbox`, `_local_sandbox`, `_build_snapshot`, capability token handling
- `sandbox-system/sandbox_task.py` — Cloud Run flow sandbox entrypoint
- `sandbox-system/code_sandbox_task.py` — isolated code-patch sandbox entrypoint
- `scripts/deploy_sandbox.sh` — Cloud Run job deployment helper
- `scripts/setup_cloud_sandbox_iam.sh` — IAM setup helper

## Realtime Events

Events are written to `.agent_events/events.jsonl` and served by `src/realtime/server.py`.

Endpoints:

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
    "detail": "This should appear in Real-Time Agents."
  }'
```

## Tests

Focused checks:

```bash
python -m py_compile streamlit_app.py main.py src/agents/tools.py src/agents/nodes.py
pytest -q test_cloud_run_urls.py test_activate_proposal.py
python test_realtime.py
python test_sandbox.py
python test_graphrag.py
python test_skill_registry.py
```

Integration checks requiring configured services:

```bash
python test_integration.py
python test_sandbox_capability.py
python test_capability_token_signing.py
```

## Troubleshooting

### Streamlit port is busy

```bash
lsof -ti tcp:8501
for pid in $(lsof -ti tcp:8501); do kill "$pid"; done
```

Or run on another port:

```bash
streamlit run streamlit_app.py --server.port 8502
```

### Neo4j connection fails

Check `.env`:

```text
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
NEO4J_DATABASE
```

Then use **Project Review** or **System Map** to confirm the UI can read graph counts.

### Cloud Run sandbox link or logs fail

Check:

```text
GOOGLE_CLOUD_PROJECT
SANDBOX_GCP_REGION
SANDBOX_JOB_NAME
SANDBOX_INVOKER_SERVICE_ACCOUNT
SANDBOX_SOURCE_BUCKET
CAPABILITY_KMS_KEY_VERSION
CAPABILITY_JWT_PUBLIC_KEY_PATH
```

The UI should show separate buttons for Cloud Run execution, Cloud Run logs, and Cloud Run job after a run has metadata.

### Gemini quota is exhausted

If Gemini returns `429 RESOURCE_EXHAUSTED`, agent planning stops. Project review, graph display, sandbox inspection, realtime event display, and existing flow review can still work.
