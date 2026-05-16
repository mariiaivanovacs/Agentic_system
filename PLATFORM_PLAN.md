# EcoLink NeuroCore — Full Platform Plan
**Hackathon: Build With AI 2026 KL — MyHack | 16–17 May 2026**

---

## Final Target State

The system described in `full_technical.md` is an **Autonomous Ecosystem Operating
System**: an agentic platform that can be pointed at any existing IT solution,
index its components into a knowledge graph, and continuously propose, simulate,
and optimize how those components interact — without touching production.

The four pillars it requires:

| Pillar | Description |
|---|---|
| **Dual Knowledge Graph** | Graph A = historical data (what happened). Graph B = system blueprint (how it works). Bridged by ExecutionTrace nodes. |
| **Agentic Brain** | LangGraph multi-agent pipeline (Planner → Generator → Critic → Simulator → Evaluator → HumanApproval) powered by Gemini. |
| **GraphRAG Intelligence** | Retrieval-Augmented Generation over the graph — the agent reasons by traversing history, not by memorizing training data. |
| **Platform Generalization** | Schema registry, system indexer, skill factory, connector factory — so the platform works on ANY IT solution, not just EcoLink. |

---

## Current State (what is already built)

| Component | File(s) | Status |
|---|---|---|
| Graph A + B ingestion | `ecolink-graph/ingest.py`, `data/` | ✅ Done |
| Full query library | `ecolink-graph/queries.py` | ✅ Done |
| 4 agent tools | `src/agents/tools.py` | ✅ Done |
| LangGraph 6-node pipeline | `src/agents/graph.py`, `nodes.py`, `state.py` | ✅ Done |
| ExecutionTrace bridge | `log_execution_trace()` in tools.py | ✅ Done |
| JWT capability tokens | `_capability_token()` in tools.py | ✅ Done |
| CLI entry point | `main.py` | ✅ Done |
| Mock sandbox | `_mock_sandbox()` in tools.py | ✅ Mock only |

---

## Gap Analysis — Three Documents Compared

| Feature | `response.md` | `PLATFORM_PLAN.md` (old) | `full_technical.md` | Priority |
|---|---|---|---|---|
| GraphRAG retriever | ✅ | ❌ | ✅ (as GraphRAG shortcut) | **P0** |
| GraphRAG generator | ✅ | ❌ | ✅ | **P0** |
| Prompt engine | ✅ | ❌ | ✅ | **P0** |
| YAML validator | ✅ | ❌ | ✅ | **P0** |
| FastAPI `/api/optimize` | ✅ | ❌ | ✅ | **P0** |
| React / Streamlit UI | ✅ (React) | ❌ | ✅ (Streamlit) | **P0** |
| Graph visualization | ✅ | ❌ | ✅ (pyvis) | **P0** |
| Approve/Reject in UI | ✅ | ❌ | ✅ | **P0** |
| Schema registry | ❌ | ✅ | implicit | **P1** |
| Metadata storage config | ❌ | ✅ | ✅ | **P1** |
| System indexer | ❌ | ✅ | partial | **P1** |
| Skill factory | ❌ | ✅ | ✅ | **P1** |
| Connector factory | ❌ | ✅ | ✅ | **P1** |
| GNN embeddings | ❌ | ❌ | ✅ | **P2** |
| PPO reward calculator | ❌ | ❌ | ✅ | **P2** |
| Real Docker sandbox | ❌ | deferred | ✅ | **P2** |
| Platform CLI | ❌ | ✅ | partial | **P1** |
| Git + team workflow | ✅ | ❌ | ❌ | **P0** |

**P0** = Demo-blocking. Must work for the hackathon.
**P1** = Platform generalization. Required for the system to work on any IT solution.
**P2** = Full intelligence layer. Required for the final ideal state from `full_technical.md`.

---

## Team Structure

Four parallel streams. Each person owns their stream completely.

| Stream | Person | Focus | P0 role |
|---|---|---|---|
| **Stream 1 — Graph & Data** | Backend Dev | Neo4j schema, ingestion, queries, schema registry | Seed graph, ensure all nodes exist for Leila's retriever |
| **Stream 2 — AI / GraphRAG** | Leila | GraphRAG engine, LangGraph integration, skill factory | `retriever.py`, `generator.py`, working end-to-end agent |
| **Stream 3 — Frontend & API** | Frontend Dev | FastAPI backend, Streamlit UI, graph visualization, approval flow | `/api/optimize` endpoint + basic UI showing output |
| **Stream 4 — Platform & Infra** | Cloud Dev | Schema registry, storage config, indexer, connector factory, CLI | `requirements.txt`, `.env`, platform startup, deployment |

**Rule:** If you did not create the file, ask the owner before editing it.

---

## Stream 1 — Graph & Data (Backend Dev)

### P0 Tasks — Demo Blockers

#### S1-P0-1: Verify and stabilize the seeded graph
Ensure the live Neo4j instance (`017c3af7`) has all required nodes for Leila's
retriever. Run `ecolink-graph/ingest.py` and confirm via `queries.py`:

Required node counts (minimum for demo):
- 30 Company nodes with `id, name, industry, stage, pain_points`
- 20 Mentor nodes with `id, name, expertise_tags, industry_focus, availability`
- 100 MATCHED_WITH edges with `outcome_score`
- 4 active Flow nodes, 6 Skill nodes, 4 Connector nodes

Acceptance: `python ecolink-graph/queries.py` prints no errors and shows correct counts.

#### S1-P0-2: API contract validation — node schema for Leila
`response.md` specifies exact fields Leila's retriever expects. Confirm they match
what `ingest.py` actually writes:

```
response.md requires:          ingest.py writes:
  Company.pain_points      ✅   pain_points (list)
  Mentor.expertise         ❌   expertise_tags  ← MISMATCH — add alias
  Mentor.success_score     ❌   past_success_score  ← MISMATCH — add alias
  Mentor.available         ❌   availability (string, not bool) ← needs mapping
```

Fix: add the following to `ingest.py`'s `create_mentor` Cypher:
```cypher
SET me.expertise      = $expertise_tags,
    me.success_score  = $past_success_score,
    me.available      = ($availability = 'available')
```
This writes both the original fields and the aliased fields Leila needs.
Do not remove the original fields — other parts of the codebase use them.

#### S1-P0-3: Add `get_success_patterns` query to `queries.py`
The retriever needs historical success patterns by industry.

```python
def get_success_patterns(industry: str, min_score: float = 7.0):
    """Return company-mentor pairs with high outcome scores for the given industry.
    Used by GraphRAG retriever as few-shot examples for the generator.
    """
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WHERE c.industry = $industry AND r.outcome_score >= $min_score
        RETURN c.name AS company, c.pain_points AS pain_points,
               c.stage AS stage,
               m.name AS mentor, m.expertise_tags AS skills,
               r.outcome_score AS score, r.feedback AS feedback
        ORDER BY r.outcome_score DESC
        LIMIT 10
    """, {"industry": industry, "min_score": min_score})
```

#### S1-P0-4: Add `get_failure_patterns` query to `queries.py`

```python
def get_failure_patterns(industry: str, max_score: float = 4.0):
    """Return company-mentor pairs with low scores — used by Critic agent."""
    return run_query("""
        MATCH (c:Company)-[r:MATCHED_WITH]->(m:Mentor)
        WHERE c.industry = $industry AND r.outcome_score <= $max_score
        RETURN c.name AS company, c.pain_points AS pain_points,
               m.name AS mentor, m.expertise_tags AS skills,
               r.outcome_score AS score, r.feedback AS feedback
        ORDER BY r.outcome_score ASC
        LIMIT 10
    """, {"industry": industry, "max_score": max_score})
```

### P1 Tasks — Platform Generalization

#### S1-P1-1: `src/config/schema.yaml`
Canonical node/edge schema. Every other component reads from this file instead
of hardcoding labels and properties.

```yaml
version: "1.0"
nodes:
  Company:
    required: [id, name, industry]
    optional: [stage, revenue, pain_points, founded_year]
  Mentor:
    required: [id, name, expertise_tags]
    optional: [industry_focus, availability, past_success_score,
               expertise, success_score, available]
  Skill:
    required: [id, name, language]
    optional: [description, performance_score, avg_execution_ms, artifact_path]
  Connector:
    required: [id, name, type]
    optional: [version, status, error_rate, endpoint, auth_required, auth_env_var]
  Flow:
    required: [id, name, status]
    optional: [description, avg_outcome_score, yaml_config]
  Server:
    required: [id, name, cpu_capacity, current_load]
    optional: [status, error_rate_history, region]
  ExecutionTrace:
    required: [id, status, timestamp]
    optional: []
  Outcome:
    required: [score, date]
    optional: []
edges:
  MATCHED_WITH:      { from: Company,        to: Mentor          }
  USES:              { from: Flow,            to: Skill           }
  READS_FROM:        { from: Flow,            to: Connector       }
  RUNS_ON:           { from: Flow,            to: Server          }
  RAN_FLOW:          { from: ExecutionTrace,  to: Flow            }
  RESULTED_IN:       { from: ExecutionTrace,  to: Outcome         }
  PROCESSED_COMPANY: { from: ExecutionTrace,  to: Company         }
  DEPRECATED_BY:     { from: Flow,            to: Flow            }
  ENROLLED_IN:       { from: Company,         to: Programme       }
```

#### S1-P1-2: `src/config/schema_validator.py`
Validates any incoming dict before it reaches Neo4j.

```python
class SchemaValidator:
    def validate_node(self, label: str, props: dict) -> None:
        # raises SchemaValidationError if required field missing
    def validate_edge(self, rel_type: str, from_label: str, to_label: str) -> None:
        # raises SchemaValidationError if edge type not defined for those labels
    @classmethod
    def load(cls, path: str = "src/config/schema.yaml") -> "SchemaValidator": ...
```

#### S1-P1-3: `src/config/meta_graph.py`
Writes schema as `:SchemaNode` meta-nodes in Neo4j so the agent can query
"what node types and properties exist?" at runtime.

```python
def push_schema_to_graph(validator: SchemaValidator) -> None:
    """Run once at startup or when schema.yaml changes."""
```

#### S1-P1-4: Refactor `ecolink-graph/ingest.py`
Replace inline Cypher property lists with a SchemaValidator-driven loop.
Validate each dict before writing. No property names hardcoded in Python strings.

#### S1-P1-5: `src/indexer/` — System Indexer subsystem
Files to build (in dependency order):

```
src/indexer/base_indexer.py       — abstract BaseIndexer, IndexedSystem dataclass
src/indexer/openapi_indexer.py    — OpenAPI spec → Connector + Skill nodes
src/indexer/python_indexer.py     — Python package AST → Skill nodes
src/indexer/db_indexer.py         — SQLAlchemy DSN → Connector nodes
src/indexer/graph_writer.py       — writes IndexedSystem to Neo4j via MetadataStore
src/indexer/runner.py             — CLI: python -m src.indexer.runner --type openapi --source ...
```

See architecture detail in the original PLATFORM_PLAN sections (Phase 1).

**Stream 1 owns:** All `src/config/`, `src/indexer/`, `ecolink-graph/`
**Does not touch:** `src/agents/`, `src/graphrag/`, `frontend/`, `src/ui/`

---

## Stream 2 — AI / GraphRAG (Leila)

### P0 Tasks — Demo Blockers

#### S2-P0-1: `src/graphrag/retriever.py`
Queries Neo4j for historical success and failure patterns for a given industry.
Returns structured context for the generator.

```python
def retrieve_context(industry: str, goal: str) -> RetrievedContext:
    """
    Returns:
      RetrievedContext(
        success_patterns: List[dict],   # from get_success_patterns()
        failure_patterns: List[dict],   # from get_failure_patterns()
        available_skills: List[dict],   # from get_best_skills()
        infra_status:     dict,         # from get_infrastructure_status()
      )
    """
```

Uses `ecolink-graph/queries.py` functions — do not write raw Cypher here.
Import: `from ecolink-graph.queries import get_success_patterns, ...`
(use `sys.path` insert or make `ecolink-graph` a package with `__init__.py`).

**Contract:** Retriever must be callable with just `(industry, goal)` — no Neo4j
driver code inside this file. All DB access goes through `queries.py`.

#### S2-P0-2: `src/graphrag/prompt_engine.py`
Builds the prompt sent to Gemini from the retrieved context.

```python
def build_planner_prompt(goal: str, context: RetrievedContext) -> str:
    """Assembles a structured prompt with:
    - Goal statement
    - Success patterns (as few-shot examples)
    - Failure patterns (as negative examples)
    - Available skills to choose from
    - Infrastructure constraints
    Returns a plain string ready for Gemini.
    """

def build_critic_prompt(proposed_yaml: str, context: RetrievedContext) -> str:
    """Builds validation prompt for the Critic node."""
```

No LLM calls inside this file — pure string construction. Testable without
any API key.

#### S2-P0-3: `src/graphrag/generator.py`
Calls Gemini with the prompt and returns a structured result.

```python
def generate_flow_proposal(goal: str, industry: str) -> FlowProposal:
    """
    1. retriever.retrieve_context(industry, goal)
    2. prompt_engine.build_planner_prompt(goal, context)
    3. Calls Gemini (gemini-2.5-flash via langchain_google_genai)
    4. Parses response into FlowProposal(flow_yaml, reasoning_trace, skills_used)
    5. Returns FlowProposal
    """
```

Uses structured output (`with_structured_output`) exactly as `nodes.py` does —
do not invent a different calling pattern.

Model: `gemini-2.5-flash` (same as `nodes.py` `_llm()` — share the factory).

#### S2-P0-4: `src/graphrag/validator.py`
Validates the generated YAML flow before it is returned to the frontend.

```python
def validate_flow_yaml(flow_yaml: str, valid_skill_ids: List[str],
                       valid_connector_ids: List[str]) -> ValidationResult:
    """
    Checks:
    1. YAML parses without error
    2. Every skill referenced exists in valid_skill_ids
    3. Every connector referenced exists in valid_connector_ids
    4. Required fields present: flow_id, steps
    Returns ValidationResult(is_valid, errors: List[str])
    """
```

No LLM calls. No Neo4j. Pure logic — fully testable.

#### S2-P0-5: `src/graphrag/main_graphrag.py`
End-to-end GraphRAG pipeline. Wires retriever → prompt engine → generator →
validator together.

```python
def run(goal: str, industry: str) -> dict:
    """Returns the JSON contract defined in response.md Contract 1:
    {
      "goal": str,
      "industry": str,
      "reasoning_trace": str,
      "proposed_flow": { "flow_id": str, "steps": [...] },
      "status": "valid" | "invalid",
      "errors": []
    }
    """

if __name__ == "__main__":
    import argparse
    # python src/graphrag/main_graphrag.py --goal "..." --industry Fintech
```

### P1 Tasks — LangGraph Integration

#### S2-P1-1: Wire GraphRAG retriever into `planner_node`
Replace the bare Cypher queries in `planner_node` with calls to
`retriever.retrieve_context()`. The planner gets richer context (success examples,
failure examples) without longer prompts.

```python
# In nodes.py planner_node — replace the two raw query_graph.invoke calls:
from src.graphrag.retriever import retrieve_context
context = retrieve_context(industry=goal_industry, goal=goal)
# pass context.success_patterns and context.failure_patterns into prompt
```

#### S2-P1-2: Wire prompt engine into `planner_node` and `critic_node`
Replace inline f-string prompts in `nodes.py` with calls to `prompt_engine`:

```python
from src.graphrag.prompt_engine import build_planner_prompt, build_critic_prompt
prompt = build_planner_prompt(goal, context)
```

This decouples prompt engineering from graph wiring — Leila owns prompts,
prompt changes don't require touching the graph topology.

#### S2-P1-3: `src/skills/skill_factory.py` + `create_skill` tool
When the agent identifies that no existing skill covers a needed capability,
it should be able to register a new one.

```
src/skills/skill_validator.py     — AST syntax + safety check (no os.system, eval, etc.)
src/skills/skill_factory.py       — validate → store → write Skill node to Neo4j
src/skills/skill_version_manager.py — DEPRECATED_BY versioning
src/skills/skill_registry.py      — list/search skills (used by Generator)
```

Add to `src/agents/tools.py`:
```python
@tool
def create_skill(name, code, language, description, input_schema, output_schema) -> str:
    """Register a new Skill node from validated code. Returns skill_id."""
```

#### S2-P1-4: `src/ml/graph_embedder.py` — GNN embeddings (P2 if time allows)
Node2Vec over the MATCHED_WITH subgraph. Writes `embedding` property to Company
and Mentor nodes. Enables structural similarity search beyond keyword matching.

```python
class GraphEmbedder:
    def train(self, driver) -> None: ...    # reads from Neo4j, writes embeddings back
    def get_embedding(self, node_id) -> np.ndarray: ...
```

#### S2-P1-5: `src/ml/reward_calculator.py` — PPO Lite (P2 if time allows)
Converts simulation output to a scalar reward signal.

```python
def calculate_reward(sim_result: dict, baseline_score: float) -> float:
    """
    +10  if sim match_score > baseline * 1.3
    -5   if latency increased > 20%
    -50  if status == 'fail'
    """
```

**Stream 2 owns:** `src/graphrag/`, `src/skills/`, `src/ml/`
**Does not touch:** `ecolink-graph/`, `frontend/`, `src/ui/`, `src/config/`

---

## Stream 3 — Frontend & API (Frontend Dev)

### P0 Tasks — Demo Blockers

#### S3-P0-1: `src/api/main.py` — FastAPI backend

Implements the API contracts from `response.md`:

```python
from fastapi import FastAPI
from src.graphrag.main_graphrag import run as graphrag_run

app = FastAPI(title="EcoLink NeuroCore API")

@app.post("/api/optimize")
def optimize(body: OptimizeRequest) -> OptimizeResponse:
    """
    Input:  { goal, industry, output_file (optional) }
    Output: Contract 1 from response.md
    """
    return graphrag_run(goal=body.goal, industry=body.industry)

@app.post("/api/agent/run")
def run_agent(body: AgentRunRequest) -> AgentRunResponse:
    """Triggers the full LangGraph pipeline. Returns thread_id for polling."""

@app.post("/api/proposals/{proposal_id}/approve")
def approve(proposal_id: str):
    """Resumes paused LangGraph thread with approved=True."""

@app.post("/api/proposals/{proposal_id}/reject")
def reject(proposal_id: str, body: RejectRequest):
    """Resumes paused LangGraph thread with approved=False."""

@app.get("/api/proposals")
def list_proposals():
    """Returns all Flow nodes with status='proposed' from Neo4j."""

@app.get("/api/graph/stats")
def graph_stats():
    """Returns ecosystem stats for the dashboard header."""

@app.get("/api/infrastructure")
def infrastructure():
    """Returns server status for the infra monitor."""
```

Run with: `uvicorn src.api.main:app --reload`

All Neo4j reads go through `ecolink-graph/queries.py`.
All agent calls go through `src/agents/graph.py`.

#### S3-P0-2: Basic UI — choose one of:

**Option A — Streamlit** (recommended: fastest, same language as backend)
```
src/ui/app.py              — Streamlit entry: sidebar + page router
src/ui/pages/optimize.py   — Goal input form → calls /api/optimize → shows output
src/ui/pages/proposals.py  — Pending proposals table + Approve/Reject buttons
src/ui/pages/infra.py      — Server load table
```

**Option B — React** (from `response.md`, if Frontend Dev prefers)
```
frontend/src/pages/Optimize.jsx     — form + result display
frontend/src/pages/Proposals.jsx    — approval dashboard
frontend/src/components/GraphView.jsx — pyvis/react-force-graph visualization
```

Both options call the same FastAPI endpoints. The team decides at kickoff.

#### S3-P0-3: Graph visualization component
Displays Company and Mentor nodes, MATCHED_WITH edges coloured by score.

If Streamlit: use `pyvis` — generate HTML and embed with `st.components.html`.
If React: use `react-force-graph` or `vis-network`.

Minimum for demo:
- Show all Company nodes (circle, sized by revenue)
- Show all Mentor nodes (square, sized by success_score)
- Show MATCHED_WITH edges coloured: green (score ≥ 7), red (score < 4)
- Click a node → show its details in a sidebar panel

#### S3-P0-4: Approve/Reject flow in UI
```
1. UI calls GET /api/proposals → shows table of pending flows
2. Admin clicks "Approve" → POST /api/proposals/{id}/approve
3. UI shows success toast → refreshes proposals list
4. In Neo4j: Flow.status changes from 'proposed' to 'active'
```

This uses the existing `activate_proposal()` function in `tools.py` via the
FastAPI endpoint — no new graph logic needed.

### P1 Tasks — Platform & Polish

#### S3-P1-1: Agent run panel
Full LangGraph pipeline triggered from the UI:
```
1. User enters goal + clicks "Run Agent"
2. POST /api/agent/run → returns { thread_id }
3. UI polls GET /api/agent/status/{thread_id}
4. When status == "awaiting_approval": show proposal details + Approve/Reject
5. When status == "complete": show final output
```

#### S3-P1-2: Dual graph view (Graph A + Graph B side by side)
Two network graphs:
- Left: Graph A (historical data — Companies, Mentors, MATCHED_WITH)
- Right: Graph B (blueprint — Flows, Skills, Connectors, Servers)
- Highlight nodes referenced in the latest agent proposal (yellow pulse)

#### S3-P1-3: Infrastructure monitor panel
Live table from `/api/infrastructure`:
- Server name, status badge (healthy/overloaded/critical), load bar, region
- Auto-refresh every 30 seconds

**Stream 3 owns:** `src/api/`, `src/ui/` (or `frontend/`)
**Does not touch:** `src/agents/`, `src/graphrag/`, `ecolink-graph/`

---

## Stream 4 — Platform & Infra (Cloud Dev)

### P0 Tasks — Demo Blockers

#### S4-P0-1: Environment setup and `requirements.txt`
Ensure all dependencies install cleanly for every team member.

Current `requirements.txt` (verified):
```
langgraph>=0.2.56
langchain-core>=0.3.0
langchain-google-genai>=2.0.0
langchain-google-vertexai>=2.0.0
neo4j>=5.27.0
pyyaml>=6.0
python-dotenv>=1.0.0
google-cloud-run>=0.10.0
pyjwt>=2.10.0
tenacity>=8.2.0
pydantic>=2.0.0
streamlit>=1.40.0
pandas>=2.0.0
```

Add for Stream 3 (API):
```
fastapi>=0.115.0
uvicorn>=0.32.0
httpx>=0.27.0
```

Add for Stream 1 (Indexer):
```
sqlalchemy>=2.0.0
google-cloud-storage>=2.0.0
```

Add for Stream 2 (ML — P2 only):
```
torch>=2.0.0
torch-geometric>=2.0.0
node2vec>=0.4.0
scikit-learn>=1.3.0
```

Manage additions via PR to `main` — others request, Cloud Dev approves.

#### S4-P0-2: `.env` template
Create `.env.example` (safe to commit — no real secrets):

```bash
# Neo4j (ecolink-graph instance)
NEO4J_URI=neo4j+s://017c3af7.databases.neo4j.io
NEO4J_USERNAME=017c3af7
NEO4J_PASSWORD=<ask team lead>
NEO4J_DATABASE=017c3af7

# Google AI
GOOGLE_API_KEY=<ask Leila>
GOOGLE_CLOUD_PROJECT=ecosystem-sandbox
GOOGLE_CLOUD_LOCATION=us-central1

# Sandbox
SANDBOX_MOCK=true
SANDBOX_GCP_REGION=us-central1
SANDBOX_JOB_NAME=ecolink-sandbox-executor
CAPABILITY_TOKEN_SECRET=change-this-in-production

# Artifact storage (Stream 1 — Phase 1)
ARTIFACT_STORE_BACKEND=local
ARTIFACT_STORE_PATH=./artifacts

# Optional: timeouts
NEO4J_CONNECTION_TIMEOUT_SECONDS=5
NEO4J_QUERY_TIMEOUT_SECONDS=10
```

Add `.env` to `.gitignore` immediately.

#### S4-P0-3: `src/config/startup.py` — pre-flight checks

```python
def run_startup_checks() -> None:
    """Run before any CLI command or API startup.
    1. verify_neo4j_connection()  (already in tools.py)
    2. Confirm GOOGLE_API_KEY is set
    3. Confirm artifact store path exists (create if local)
    4. Print one-line status: ✓ Neo4j | ✓ Gemini key | ✓ Artifacts
    Raises RuntimeError on first failure.
    """
```

Call from `main.py` at startup and from `src/api/main.py` lifespan event.

#### S4-P0-4: Run instructions in `README.md`
One-page guide every team member can follow:

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# → fill in credentials from team chat

# 3. Seed the graph
cd ecolink-graph && python ingest.py

# 4. Verify graph
python ecolink-graph/queries.py

# 5. Run GraphRAG (Leila's engine)
python src/graphrag/main_graphrag.py --goal "Optimize Fintech" --industry Fintech

# 6. Run the API
uvicorn src.api.main:app --reload

# 7. Run the UI
streamlit run src/ui/app.py
# OR: cd frontend && npm run dev

# 8. Run the full agent (LangGraph)
python main.py --goal "Improve Healthtech matching"
```

### P1 Tasks — Platform Generalization

#### S4-P1-1: `src/config/storage_config.yaml`

```yaml
graph:
  backend: neo4j
  uri_env: NEO4J_URI
  database_env: NEO4J_DATABASE
artifacts:
  backend: local           # or: gcs
  local_path: ./artifacts
  gcs_bucket_env: GCS_BUCKET
  gcs_prefix: ecolink/skills/
```

#### S4-P1-2: `src/storage/artifact_store.py`
Stores skill code and connector configs outside Neo4j.

```python
class ArtifactStore(ABC):
    def save(self, key: str, content: bytes) -> str: ...  # returns URI
    def load(self, key: str) -> bytes: ...

class LocalArtifactStore(ArtifactStore): ...   # ./artifacts/{key}
class GCSArtifactStore(ArtifactStore): ...     # gs://{bucket}/{prefix}/{key}

def from_config(config_path: str) -> ArtifactStore: ...
```

#### S4-P1-3: `src/storage/metadata_store.py`
Schema-validating wrapper around Neo4j. All writes go through here.

```python
class MetadataStore:
    def write_node(self, label: str, props: dict) -> str: ...
    def read_node(self, label: str, node_id: str) -> dict: ...
    def write_edge(self, rel_type: str, from_id: str, to_id: str,
                   props: dict = {}) -> None: ...
```

Internally calls `SchemaValidator.validate_node()` before every write.
Extracts `artifact`-type fields → `ArtifactStore.save()`, stores URI in node.

#### S4-P1-4: `src/connectors/` — Connector Factory

```
src/connectors/connector_config.py   — Pydantic models: RESTConfig, SQLConfig, CSVConfig
src/connectors/connection_tester.py  — liveness check before registration
src/connectors/connector_factory.py  — test → write Connector node via MetadataStore
```

Add to `src/agents/tools.py`:
```python
@tool
def create_connector(name, connector_type, config, description="") -> str:
    """Test + register a new Connector in Graph B. Returns connector_id."""
```

Secrets (passwords, API tokens) are stored by env var name only — never in Neo4j.

#### S4-P1-5: `src/platform_cli.py` — Unified CLI

```bash
python -m src.platform_cli index     --type openapi --source <url>
python -m src.platform_cli skill     create --name "my_skill" --file ./skill.py
python -m src.platform_cli connector create --name "crm" --type rest --base-url <url>
python -m src.platform_cli agent     run    --goal "..."
python -m src.platform_cli agent     approve --thread-id <id>
python -m src.platform_cli schema    validate
python -m src.platform_cli schema    push
```

**Stream 4 owns:** `src/config/`, `src/storage/`, `src/connectors/`, `src/platform_cli.py`, `requirements.txt`, `.env.example`, `README.md`
**Does not touch:** `src/agents/`, `src/graphrag/`, `src/ui/`, `ecolink-graph/`

---

## API Contracts (Full Reference)

### Contract 1: GraphRAG engine → Frontend (from `response.md`)

```json
POST /api/optimize
→ 200 OK
{
  "goal": "Optimize Fintech matching",
  "industry": "Fintech",
  "reasoning_trace": "Historical data shows semantic alignment between pain_points and expertise improves outcomes by 40% for Fintech...",
  "proposed_flow": {
    "flow_id": "fintech_optimized_v1",
    "steps": [
      { "skill": "semantic_similarity", "params": { "source": "company.pain_points", "target": "mentor.expertise" } },
      { "skill": "sort_by_score_desc",  "params": {} }
    ]
  },
  "status": "valid",
  "errors": []
}
```

### Contract 2: Neo4j → GraphRAG retriever (from `response.md` + fixes)

```
Node type     | Field name in query result
Company       | id, name, industry, stage, pain_points
Mentor        | id, name, expertise_tags, expertise (alias), success_score (alias), available (bool alias)
MATCHED_WITH  | outcome_score, feedback, date, programme_name
Skill         | id, name, description, performance_score
```

### Contract 3: Frontend → API

```json
POST /api/optimize        { "goal": str, "industry": str }
POST /api/agent/run       { "goal": str }
POST /api/proposals/{id}/approve   {}
POST /api/proposals/{id}/reject    { "reason": str }
GET  /api/proposals                → List[ProposalSummary]
GET  /api/graph/stats              → EcosystemStats
GET  /api/infrastructure           → List[ServerStatus]
```

### Contract 4: LangGraph → FastAPI (agent state polling)

```
GET /api/agent/status/{thread_id}
→ {
    "status": "running" | "awaiting_approval" | "complete" | "failed",
    "current_node": "planner" | "generator" | ...,
    "proposal_id": str | null,
    "proposed_flow_yaml": str | null,
    "baseline_score": float | null,
    "simulation_score": float | null
  }
```

---

## Integration Checkpoints

### Checkpoint 1 — Both days, morning start
**Goal:** Everyone connects to the same Neo4j instance and sees data.

- [ ] Stream 1: Run `python ecolink-graph/ingest.py` → no errors
- [ ] Stream 1: Run `python ecolink-graph/queries.py` → shows 30 companies, 20 mentors
- [ ] Stream 2: `from ecolink-graph import queries` works in Leila's environment
- [ ] Stream 4: `.env` distributed to all team members via WhatsApp DM

### Checkpoint 2 — Day 1, 14:00
**Goal:** GraphRAG engine works end-to-end in terminal.

- [ ] Stream 2: `python src/graphrag/main_graphrag.py --goal "Optimize Fintech" --industry Fintech` prints valid JSON
- [ ] Stream 3: FastAPI starts: `uvicorn src.api.main:app` → no import errors
- [ ] Stream 4: `python -m src.config.startup` prints ✓ for all checks

### Checkpoint 3 — Day 1, 18:00
**Goal:** Frontend calls backend and displays a real result.

- [ ] Stream 3: `POST /api/optimize` returns Contract 1 JSON
- [ ] Stream 3: UI renders the `reasoning_trace` and `proposed_flow.steps` from the API response
- [ ] Stream 1: `get_success_patterns("Fintech")` and `get_failure_patterns("Fintech")` return data

### Checkpoint 4 — Day 2, 10:00
**Goal:** Full agent pipeline + approval flow works.

- [ ] Stream 2: `python main.py --goal "Improve Healthtech matching"` reaches HumanApproval pause
- [ ] Stream 3: `/api/proposals` returns the paused proposal
- [ ] Stream 3: Clicking "Approve" in UI calls `/api/proposals/{id}/approve` → flow status changes to 'active' in Neo4j

### Checkpoint 5 — Day 2, 14:00 ← Demo prep
**Goal:** Complete user journey rehearsed.

- [ ] Full flow works: enter goal in UI → GraphRAG returns result → agent proposes flow → admin approves → Neo4j updated
- [ ] Graph visualization shows at least nodes + coloured edges
- [ ] README covers setup in under 5 minutes
- [ ] Demo script rehearsed by all team members

---

## Dependency Map

```
S1 (Graph & Data)
  │
  ├── provides Neo4j data ──► S2 (GraphRAG retriever)
  ├── provides queries.py ──► S3 (API reads from queries.py)
  └── provides schema.yaml ──► S4 (storage uses schema)

S2 (AI/GraphRAG)
  │
  ├── provides main_graphrag.py ──► S3 (API wraps it)
  └── provides skill_factory ──────► S4 (CLI exposes it)

S3 (Frontend/API)
  │
  └── provides /api/proposals ──► demo requires this

S4 (Platform/Infra)
  │
  ├── provides .env + requirements ──► everyone
  └── provides startup checks ──────► S2, S3

Build order within streams:
  S1: S1-P0-1 → S1-P0-2 → S1-P0-3+4 → S1-P1-1 → S1-P1-2 → S1-P1-3+4 → S1-P1-5
  S2: S2-P0-1 → S2-P0-2 → S2-P0-3 → S2-P0-4 → S2-P0-5 → S2-P1-1 → S2-P1-2 → ...
  S3: S3-P0-1 → S3-P0-4 → S3-P0-2 → S3-P0-3 → S3-P1-1+2+3
  S4: S4-P0-2 → S4-P0-1 → S4-P0-3 → S4-P0-4 → S4-P1-1 → S4-P1-2+3 → S4-P1-4 → S4-P1-5
```

---

## File Ownership Summary

```
ecolink-graph/
  ingest.py          ← Stream 1
  queries.py         ← Stream 1

src/
  config/
    schema.yaml              ← Stream 1 (+ Stream 4 reads)
    schema_validator.py      ← Stream 1
    meta_graph.py            ← Stream 1
    storage_config.yaml      ← Stream 4
    startup.py               ← Stream 4

  storage/
    artifact_store.py        ← Stream 4
    metadata_store.py        ← Stream 4

  indexer/
    base_indexer.py          ← Stream 1
    openapi_indexer.py       ← Stream 1
    python_indexer.py        ← Stream 1
    db_indexer.py            ← Stream 1
    graph_writer.py          ← Stream 1
    runner.py                ← Stream 1

  graphrag/
    retriever.py             ← Stream 2 (Leila)
    prompt_engine.py         ← Stream 2 (Leila)
    generator.py             ← Stream 2 (Leila)
    validator.py             ← Stream 2 (Leila)
    main_graphrag.py         ← Stream 2 (Leila)

  agents/
    tools.py        ← Stream 2 adds create_skill / create_connector tools
    nodes.py        ← Stream 2 wires GraphRAG retriever + prompt engine
    graph.py        ← no changes needed
    state.py        ← no changes needed

  skills/
    skill_validator.py       ← Stream 2
    skill_factory.py         ← Stream 2
    skill_version_manager.py ← Stream 2
    skill_registry.py        ← Stream 2

  ml/
    graph_embedder.py        ← Stream 2 (P2)
    reward_calculator.py     ← Stream 2 (P2)

  connectors/
    connector_config.py      ← Stream 4
    connection_tester.py     ← Stream 4
    connector_factory.py     ← Stream 4

  api/
    main.py                  ← Stream 3

  ui/
    app.py                   ← Stream 3
    pages/optimize.py        ← Stream 3
    pages/proposals.py       ← Stream 3
    pages/infra.py           ← Stream 3

  platform_cli.py            ← Stream 4

frontend/                    ← Stream 3 (if React chosen)

main.py                      ← no changes needed
requirements.txt             ← Stream 4
.env.example                 ← Stream 4
README.md                    ← Stream 4
```

---

## Git Workflow (from `response.md`)

```
main    ← stable, demo-ready code only — never push directly
dev     ← integration branch — merge here first, then to main

Branch naming:
  feature/[name]-[description]   e.g. feature/leila-graphrag-retriever
  fix/[name]-[description]       e.g. fix/backend-neo4j-field-mismatch
```

**Rules:**
- Never `git push origin main` directly
- Never commit `.env` (it is in `.gitignore`)
- Pull from `dev` before starting new work
- One PR = one feature. Keep it small and reviewable.
- Notify team in chat before merging to `dev`.

---

## Common Errors & Quick Fixes

| Error | Cause | Fix |
|---|---|---|
| `NEO4J_URI not set` | `.env` missing | `cp .env.example .env` and fill in credentials |
| `DatabaseNotFound: neo4j` | Missing `NEO4J_DATABASE=017c3af7` | Add that line to `.env` |
| `No nodes found` | Ingest not run | `cd ecolink-graph && python ingest.py` |
| `429 RESOURCE_EXHAUSTED` | Gemini free-tier daily limit hit | Wait for quota reset or add billing |
| `UnicodeDecodeError csv` | interactions.csv encoding | Already fixed: `encoding="latin-1"` in ingest.py |
| `YAML validation error` | Gemini generated bad YAML | Check `validator.py` error output — retry |
| `Module not found` | Missing package | `pip install -r requirements.txt` |
| `Connection refused Neo4j` | AuraDB paused | Go to console.neo4j.io and resume instance |
