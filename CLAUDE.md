# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the realtime event server (required for Live Agent Comms)
uvicorn src.realtime.server:app --host 127.0.0.1 --port 8765 --reload

# Start the Streamlit dashboard
SANDBOX_MOCK=false SANDBOX_MODE=local STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
streamlit run streamlit_app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true

# Run the agent from CLI
python main.py --goal "Improve match quality for Healthtech startups"
python main.py --thread-id <ID> --approve
python main.py --thread-id <ID> --reject --reason "Too risky"

# Index a codebase or website into Neo4j
python -m src.indexer.runner --type codebase --source ./path/to/project
python -m src.indexer.runner --type web --source http://127.0.0.1:5173 --depth 1 --max-pages 30 --clear

# Inspect live GraphRAG context
python -m src.graphrag.main_graphrag --goal "..." --industry Healthtech

# Validate syntax only (no Neo4j, no LLM required)
python -m py_compile streamlit_app.py main.py src/agents/nodes.py src/agents/state.py

# Run tests
python test_realtime.py      # realtime event server
python test_sandbox.py       # sandbox simulation
python test_integration.py   # Neo4j + tools integration
python test_graphrag.py      # GraphRAG context retrieval
python test_skill_registry.py
```

## Architecture

### Dual-Graph Model
Two logical graph regions live in the same Neo4j instance:
- **Graph A (historical):** `Company`, `Mentor`, `Outcome`, `ExecutionTrace`, `LearningEvent` — read by Planner/Critic/Evaluator to ground decisions
- **Graph B (blueprint):** `Flow`, `Skill`, `Connector`, `Server`, `SkillProposal` — the live system being optimised; Generator proposes changes here

All Cypher that modifies the graph goes through `ecolink-graph/queries.py`. `src/agents/tools.py` exposes `query_graph` (read-only, rejects write keywords) and `propose_change` / `activate_proposal` (write). Never add raw write Cypher outside these paths.

### LangGraph Agent Pipeline
Six nodes wired in `src/agents/graph.py`:
```
planner → generator → critic ─(pass)─→ simulator → evaluator
              ↑          │(fail)                         │(fail, retry < 3)
              └──────────┴───────────────────────────────┘
                                                          │(success)
                                                    human_approval → END
```
`MAX_RETRIES = 3` is shared across critic failures and evaluator failures (same `retry_count` counter).

**State** is defined in `src/agents/state.py` as a `TypedDict`. All node return dicts are merged into state by LangGraph. Only `messages` uses `operator.add` (append-only); all other fields are last-write-wins. When adding a new field, also add it to `initial_state` in `main.py`.

**Structured LLM calls** all go through `_structured_invoke(llm, prompt, Schema)` in `nodes.py`, which uses Gemini's `.with_structured_output()`. All output schemas are Pydantic `BaseModel` subclasses defined at the top of `nodes.py`.

### GraphRAG Context Flow
`src/graphrag/retriever.py:retrieve_context()` is the single entry point. It queries Neo4j for all evidence and returns a `RetrievedContext` dataclass. `src/graphrag/prompt_engine.py` builds the Planner and Critic prompts from that context. The `software_nodes` section (codebase evidence from the indexed project) appears **above** failure/success patterns in the Planner prompt — this ordering is intentional so codebase facts are seen first.

### Sandbox Dispatch
`simulate_flow` tool in `tools.py` routes to one of three modes via env vars:
- `SANDBOX_MOCK=true` → `_mock_sandbox()` — deterministic, no deps, used in tests
- `SANDBOX_MOCK=false`, `SANDBOX_MODE=local` → `_local_sandbox()` — runs `sandbox-system/sandbox_task.py` as a subprocess
- `SANDBOX_MOCK=false`, `SANDBOX_MODE=cloudrun` → `_cloud_run_sandbox()` → polls Cloud Logging for `DATA_STREAM_START`/`DATA_STREAM_END` markers

The sandbox never receives raw Neo4j data — `_build_snapshot()` always calls `_sanitize_snapshot()` first, which recursively strips keys matching any of 16 secret-looking patterns.

### Realtime Events
Every agent node calls `_emit_node_event()` internally. Events are `POST`ed to the FastAPI server (`src/realtime/server.py` on port 8765), which broadcasts to WebSocket clients and appends to `.agent_events/events.jsonl`. If the server is down, events fall back to file-only. The Streamlit Live Agent Comms page connects to `WS /ws/events`.

### Indexer → Neo4j Pipeline
`src/indexer/codebase_analyzer.py` does AST parsing (Python) and regex pattern matching (JS/TS/Solidity) to extract `CodeNodeSpec` objects. `src/indexer/graph_writer.py` merges them into Neo4j using stable SHA1-based IDs so re-runs are idempotent. The written node labels (`Project`, `File`, `Route`, `Function`, `DataStore`, etc.) are what `planner_node` queries as `software_nodes` at runtime.

## Key Constraints

- `query_graph` rejects any Cypher containing `CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`, `DETACH`, or `CALL`. Writes must go through the named write helpers.
- `propose_change` only creates `Flow` nodes with `status='proposed'`. Nothing is activated until `activate_proposal()` is explicitly called after human approval.
- The `ecolink-graph/` directory is `sys.path.insert`'d at startup in `nodes.py` so `import queries as graph_queries` works without installing it as a package.
- Sandbox capability tokens (`_capability_token()` in `tools.py`) are scoped to four hardcoded skills. Flows using other skills will pass Critic validation but may fail at sandbox execution unless the token is updated.
- GraphRAG intentionally does **not** fall back to synthetic data — if Neo4j is unreachable, `retrieve_context()` raises so the agent never optimises from fake context.
