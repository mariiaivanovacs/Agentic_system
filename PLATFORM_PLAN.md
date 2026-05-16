# EcoLink NeuroCore — Platform Generalization Plan

## Context

The current system is a **domain-specific implementation** hardwired to the EcoLink
mentor-matching use case. Skills, connectors, and flows are static JSON files. The
graph schema is implicit inside `ingest.py`. There is no way to point the system at
a different IT solution and have it index, represent, and operate on that system.

This plan describes what is **missing** and the **exact tasks** required to turn the
system into a **generic, pluggable platform** that can be dropped onto any existing
software and autonomously build a graph representation of it, register new skills,
and configure its own storage — without touching production data.

Sandbox creation is **explicitly deferred** to a future phase and is not part of
this plan.

---

## Gap Analysis — What Is Missing

| Capability | Current state | Gap |
|---|---|---|
| Index any external IT system | ❌ Not possible — data is hardcoded JSON | Full indexer subsystem |
| Configurable graph schema | ❌ Schema is implicit in `ingest.py` Cypher strings | Schema registry + validator |
| Metadata storage configuration | ❌ Only Neo4j, no artifact store for code | Storage config layer |
| Create new skills dynamically | ❌ Skills are static entries in `skills.json` | Skill factory + code store |
| Create new connectors dynamically | ❌ Connectors are static entries in `connectors.json` | Connector factory |
| Agent awareness of schema | ❌ Agent hardcodes node/edge names in Cypher | Schema-aware query generation |
| Unified platform CLI | ❌ Only `main.py --goal` exists | `platform_cli.py` with subcommands |

---

## Architecture of the New Platform Layer

```
External IT Solution
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  INDEXER LAYER  (Phase 1)                           │
│  OpenAPIIndexer │ PythonIndexer │ DBIndexer         │
│        └──────────────┬─────────────┘               │
│               GraphWriter                           │
└──────────────────┬──────────────────────────────────┘
                   │ writes validated nodes
┌──────────────────▼──────────────────────────────────┐
│  SCHEMA REGISTRY  (Phase 2)                         │
│  schema.yaml → SchemaValidator → MetaGraph in Neo4j │
└──────────────────┬──────────────────────────────────┘
                   │ governs all reads/writes
┌──────────────────▼──────────────────────────────────┐
│  METADATA STORAGE CONFIG  (Phase 3)                 │
│  storage_config.yaml                                │
│  ArtifactStore (local FS / GCS)  ←── skill code    │
│  MetadataStore (Neo4j wrapper)   ←── graph nodes   │
└──────────────────┬──────────────────────────────────┘
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
┌──────────────┐     ┌──────────────────┐
│ SKILL FACTORY│     │ CONNECTOR FACTORY│
│  (Phase 4)   │     │   (Phase 5)      │
└──────┬───────┘     └────────┬─────────┘
       │                      │
       └──────────┬───────────┘
                  ▼
         Existing LangGraph Agent
         (queries schema-aware graph,
          proposes new skills/connectors)
```

---

## Phase 1 — System Indexer

**Goal:** Accept a pointer to any external IT solution and populate Graph B with a
structured representation of its components.

**Why first:** Everything else in the platform depends on having data in the graph.
The indexer is the entry point for any new integration.

### Tasks

#### 1.1 — `src/indexer/base_indexer.py`
Define the abstract contract all indexers implement.

```
Class:  BaseIndexer(ABC)
Method: discover() -> IndexedSystem
        Returns a dataclass containing:
          - connectors: List[ConnectorSpec]  (data sources found)
          - skills:     List[SkillSpec]      (operations/functions found)
          - flows:      List[FlowSpec]       (pipelines/workflows found)
          - metadata:   Dict                 (source system info)
```

No I/O, no Neo4j — pure discovery. Allows unit testing without a database.

#### 1.2 — `src/indexer/openapi_indexer.py`
Reads an OpenAPI 3.x / Swagger 2.x spec (URL or local file) and extracts:
- Each `path + method` → one **Connector** node (`type=rest`, `endpoint`, `method`, `auth_required`)
- Each `operationId` → one **Skill** node (`input_schema`, `output_schema` from the spec's request/response bodies)
- Groups of related paths → one candidate **Flow** node

```
Usage:  OpenAPIIndexer(source="https://api.example.com/openapi.json")
        OpenAPIIndexer(source="./specs/my_api.yaml")
```

Dependency: `httpx`, `pyyaml`, `jsonschema`

#### 1.3 — `src/indexer/python_indexer.py`
Reads a Python package directory and extracts:
- Each public function with a docstring → one **Skill** node
  (`language=python`, `input_schema` from type hints, `description` from docstring)
- Each module-level class with `__call__` → one **Connector** node
- Each file ending in `_flow.py` or `_pipeline.py` → one candidate **Flow** node

Uses Python's built-in `ast` module — zero external dependencies beyond what
is already installed.

```
Usage:  PythonIndexer(source="/path/to/my_package")
```

#### 1.4 — `src/indexer/db_indexer.py`
Reads a database via a SQLAlchemy DSN and extracts:
- Each table/view → one **Connector** node (`type=sql`, `table`, `column_schema`)
- Stored procedures (where supported) → **Skill** nodes
- Foreign key relationships → **Flow** edge hints

Connection string is read from env var `INDEX_DB_DSN` — never hardcoded.

```
Usage:  DBIndexer(source="postgresql://user:pass@host:5432/mydb")
```

#### 1.5 — `src/indexer/graph_writer.py`
Takes the `IndexedSystem` output from any indexer and writes it to Neo4j via
the **MetadataStore** (Phase 3). This is the only file that knows about Neo4j.

Responsibilities:
- Deduplicates nodes (MERGE on `id`)
- Validates each node against the **Schema Registry** (Phase 2) before writing
- Creates `DEPRECATED_BY` edges if a node with the same name already exists at
  a different version
- Writes a `IndexRun` meta-node recording source, timestamp, and node counts

#### 1.6 — `src/indexer/runner.py`
CLI entry point for the indexer subsystem.

```bash
python -m src.indexer.runner --type openapi --source https://api.example.com/openapi.json
python -m src.indexer.runner --type python  --source ./my_package
python -m src.indexer.runner --type db      --source postgresql://...
```

Prints a summary table: nodes discovered, nodes written, nodes skipped (duplicates).

**Deliverable:** Running the indexer against any OpenAPI spec, Python package,
or SQL database populates Graph B with real nodes that the existing LangGraph
agent can immediately query and propose flows against.

---

## Phase 2 — Schema Registry

**Goal:** Make the graph schema explicit, versioned, and queryable — so both
the indexer and the agent operate against a defined contract, not implicit Cypher.

**Why second:** The indexer (Phase 1) needs the schema to validate what it writes.
The agent needs the schema to generate correct Cypher.

### Tasks

#### 2.1 — `src/config/schema.yaml`
The single source of truth for all node and edge types in the graph.

```yaml
version: "1.0"

nodes:
  Company:
    required: [id, name, industry]
    optional: [stage, revenue, pain_points, founded_year]
  Mentor:
    required: [id, name, expertise_tags]
    optional: [industry_focus, availability, past_success_score, years_experience]
  Skill:
    required: [id, name, language]
    optional: [description, performance_score, avg_execution_ms, artifact_path]
  Connector:
    required: [id, name, type]
    optional: [version, status, error_rate, endpoint, auth_required]
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
  MATCHED_WITH:   { from: Company,        to: Mentor         }
  USES:           { from: Flow,           to: Skill          }
  READS_FROM:     { from: Flow,           to: Connector      }
  RUNS_ON:        { from: Flow,           to: Server         }
  RAN_FLOW:       { from: ExecutionTrace, to: Flow           }
  RESULTED_IN:    { from: ExecutionTrace, to: Outcome        }
  PROCESSED_COMPANY: { from: ExecutionTrace, to: Company     }
  DEPRECATED_BY:  { from: Flow,           to: Flow           }
  ENROLLED_IN:    { from: Company,        to: Programme      }
```

This file is the contract. All other code reads from it — nothing hardcodes
node labels or property names inline.

#### 2.2 — `src/config/schema_validator.py`
Validates any incoming dict against the schema before it reaches Neo4j.

```
Class:  SchemaValidator
Method: validate_node(label: str, props: dict) -> None
        Raises SchemaValidationError with field name if required field missing.
Method: validate_edge(rel_type: str, from_label: str, to_label: str) -> None
        Raises SchemaValidationError if the edge type is not defined for those labels.
Method: load(path: str = "src/config/schema.yaml") -> SchemaValidator
```

#### 2.3 — `src/config/meta_graph.py`
Writes the schema itself into Neo4j as `:SchemaNode` and `:SchemaEdge` meta-nodes,
so the LangGraph agent can query "what node types exist and what properties do they
have?" without reading a YAML file.

```cypher
// Written by meta_graph.py on startup
(:SchemaNode {label: "Skill", required: ["id","name","language"], version: "1.0"})
(:SchemaEdge {type: "USES", from: "Flow", to: "Skill"})
```

The Planner agent gains a new startup query:
```cypher
MATCH (s:SchemaNode) RETURN s.label, s.required, s.optional
```
This makes the agent self-aware of the schema it is operating on.

#### 2.4 — Refactor `ecolink-graph/ingest.py`
Replace all hardcoded `SET co.name = $name, co.industry = $industry ...` blocks
with a loop driven by `schema.yaml`:

```python
validator = SchemaValidator.load()
validator.validate_node("Company", company_dict)
# then write — no property list needs to be maintained by hand
```

**Deliverable:** `schema.yaml` is the single source of truth. Adding a new node
type requires editing one file, not hunting through Cypher strings across multiple
Python files.

---

## Phase 3 — Metadata Storage Configuration

**Goal:** Separate where graph metadata goes (Neo4j) from where code artifacts go
(file system in dev, GCS in prod), with a single config that controls both.

**Why third:** The Skill Factory (Phase 4) needs to store executable code somewhere.
That somewhere must be configured before the factory is built.

### Tasks

#### 3.1 — `src/config/storage_config.yaml`
Declares storage backends per artifact type.

```yaml
graph:
  backend: neo4j                    # always Neo4j for relationships
  uri_env: NEO4J_URI
  database_env: NEO4J_DATABASE

artifacts:
  backend: local                    # or: gcs
  local_path: ./artifacts           # used when backend=local
  gcs_bucket_env: GCS_BUCKET       # used when backend=gcs
  gcs_prefix: ecolink/skills/

metadata_cache:
  enabled: false                    # set true to cache Neo4j reads in Redis
  redis_url_env: REDIS_URL
```

#### 3.2 — `src/storage/artifact_store.py`
Stores and retrieves binary artifacts (skill code, connector configs) with a
content-addressed key derived from `sha256(content)`.

```
Class:  ArtifactStore(ABC)
  Method: save(key: str, content: bytes) -> str   (returns storage URI)
  Method: load(key: str) -> bytes

Class:  LocalArtifactStore(ArtifactStore)
  - stores files under ./artifacts/{key}

Class:  GCSArtifactStore(ArtifactStore)
  - stores blobs in gs://{bucket}/{prefix}/{key}
  - uses Application Default Credentials (no API keys)

Factory: ArtifactStore.from_config(storage_config.yaml) -> ArtifactStore
```

#### 3.3 — `src/storage/metadata_store.py`
Thin wrapper around the Neo4j driver that enforces schema validation on every
write and routes artifact-heavy properties (like `code`) to the ArtifactStore
instead of storing them inline in the graph.

```
Class:  MetadataStore
  Method: write_node(label: str, props: dict) -> str   (returns node id)
          - calls SchemaValidator.validate_node()
          - extracts any "artifact" fields → ArtifactStore.save()
          - stores the returned URI as artifact_path on the node
          - writes to Neo4j

  Method: read_node(label: str, node_id: str) -> dict
  Method: write_edge(rel_type: str, from_id: str, to_id: str, props: dict)
```

#### 3.4 — Update `.env` and `requirements.txt`
Add:
```
ARTIFACT_STORE_BACKEND=local
ARTIFACT_STORE_PATH=./artifacts
```
Add to `requirements.txt`: `google-cloud-storage>=2.0` (optional, only needed
for GCS backend).

**Deliverable:** All graph writes go through `MetadataStore`. Code artifacts are
stored outside Neo4j in a configurable location. Switching from local dev to GCS
in production is a one-line `.env` change.

---

## Phase 4 — Skill Factory

**Goal:** Enable the system (and the agent) to create new Skills dynamically from
code, validate them, store the executable, and register them in Graph B — without
touching any JSON file.

**Why fourth:** Depends on ArtifactStore (Phase 3) for code storage and
SchemaValidator (Phase 2) for node validation.

### Tasks

#### 4.1 — `src/skills/skill_validator.py`
Validates skill code before it is stored. Two-stage check:

**Stage 1 — Syntax:**
```python
ast.parse(code)   # raises SyntaxError on bad Python
```

**Stage 2 — Safety (AST walk):**
Rejects code containing any of:
- `os.system`, `subprocess`, `__import__`, `eval`, `exec`
- `open(` with write modes (`"w"`, `"a"`, `"wb"`)
- Network calls (`socket`, `requests.post`, `urllib`)

Raises `SkillValidationError` with the offending line number and reason.

Java support: runs `javac` in a subprocess (if available) for syntax check only.

#### 4.2 — `src/skills/skill_factory.py`
Creates a Skill from code and registers it in the graph.

```
Class:  SkillFactory
Method: create(
          name:          str,
          code:          str,
          language:      str,          # "python" | "java"
          description:   str,
          input_schema:  dict,         # JSON Schema
          output_schema: dict,
          tags:          List[str] = []
        ) -> str                       # returns skill_id

Internally:
  1. SkillValidator.validate(code, language)
  2. skill_id = f"skill_{slugify(name)}_{sha256(code)[:8]}"
  3. artifact_path = ArtifactStore.save(skill_id, code.encode())
  4. MetadataStore.write_node("Skill", {
       "id": skill_id, "name": name, "language": language,
       "description": description, "artifact_path": artifact_path,
       "input_schema": json.dumps(input_schema),
       "output_schema": json.dumps(output_schema),
       "performance_score": 0.0,   # updated after first sandbox run
       "avg_execution_ms": 0,
     })
  5. return skill_id
```

#### 4.3 — `src/skills/skill_version_manager.py`
Handles versioning when a skill is updated.

```
Method: create_new_version(old_skill_id, new_code, ...) -> str
        - calls SkillFactory.create() to get new_skill_id
        - writes DEPRECATED_BY edge: (old_skill)-[:DEPRECATED_BY]->(new_skill)
        - sets old node status = "deprecated"
        - returns new_skill_id

Method: get_active_version(skill_name: str) -> dict
        - queries graph for Skill by name with no outgoing DEPRECATED_BY edge
```

#### 4.4 — `src/skills/skill_registry.py`
Read-only listing and lookup. Used by the agent's Generator node to find valid
skills to reference in proposed flows.

```
Method: list_skills(language=None, min_score=None, tags=None) -> List[dict]
Method: get_skill(skill_id: str) -> dict
Method: search_skills(query: str) -> List[dict]
        (full-text search on name + description via Neo4j CONTAINS)
```

#### 4.5 — Add `create_skill` agent tool to `src/agents/tools.py`
Exposes SkillFactory to the LangGraph agent so it can propose AND register new
skills in a single step.

```python
@tool
def create_skill(name: str, code: str, language: str,
                 description: str, input_schema: dict, output_schema: dict) -> str:
    """Register a new Skill node in Graph B from validated code.
    Returns the new skill_id. The skill is immediately available for use in
    proposed flows. Only call this after confirming no existing skill covers
    this capability (use query_graph first to check).
    """
```

**Deliverable:** The agent (or a human via CLI) can call `create_skill` and
the new skill is immediately queryable in Graph B, its code is stored safely in
the artifact store, and future Generator nodes can reference it by ID.

---

## Phase 5 — Connector Factory

**Goal:** Register new data connectors dynamically — test the connection, store
the config, and create a Connector node in Graph B.

**Why fifth:** Depends on MetadataStore (Phase 3). Mirrors Phase 4 for connectors.

### Tasks

#### 5.1 — `src/connectors/connector_config.py`
Pydantic models defining the config shape for each connector type.

```python
class RESTConnectorConfig(BaseModel):
    base_url: HttpUrl
    auth_header: Optional[str]    # header name only — value from env
    auth_env_var: Optional[str]   # env var holding the token

class SQLConnectorConfig(BaseModel):
    dsn_env_var: str              # env var holding the DSN — never inline
    pool_size: int = 5
    timeout_seconds: int = 10

class CSVConnectorConfig(BaseModel):
    path: str                     # relative path or GCS URI
    delimiter: str = ","
    encoding: str = "utf-8"

class GraphQLConnectorConfig(BaseModel):
    endpoint: HttpUrl
    auth_env_var: Optional[str]
```

Secrets (API keys, passwords) are **never stored in the graph**. Only the env var
name is stored. The actual value stays in the environment or GCP Secret Manager.

#### 5.2 — `src/connectors/connection_tester.py`
Lightweight liveness check run before registering a connector.

```
Method: test(connector_type: str, config: dict) -> TestResult
  REST:     HEAD {base_url} — expects 2xx or 4xx (not connection error)
  SQL:      SELECT 1 with 5-second timeout
  CSV:      file exists and is readable (or GCS object HEAD)
  GraphQL:  POST {endpoint} with introspection query
```

Raises `ConnectorTestError` with details on failure.

#### 5.3 — `src/connectors/connector_factory.py`
Creates a Connector node after testing the connection.

```
Class:  ConnectorFactory
Method: create(
          name:           str,
          connector_type: str,    # "rest" | "sql" | "csv" | "graphql"
          config:         dict,
          description:    str     = ""
        ) -> str                  # returns connector_id

Internally:
  1. Parse config into the appropriate Pydantic model (validates fields)
  2. ConnectionTester.test(connector_type, config) — fail fast if unreachable
  3. connector_id = f"conn_{slugify(name)}_v1"
  4. MetadataStore.write_node("Connector", {
       "id": connector_id, "name": name, "type": connector_type,
       "description": description,
       "status": "active", "error_rate": 0.0,
       "version": "1.0",
       # auth secrets stored by env var name only:
       "auth_env_var": config.get("auth_env_var")
     })
  5. return connector_id
```

#### 5.4 — Add `create_connector` agent tool to `src/agents/tools.py`
Exposes ConnectorFactory to the agent, symmetric to `create_skill`.

```python
@tool
def create_connector(name: str, connector_type: str,
                     config: dict, description: str = "") -> str:
    """Register a new Connector node in Graph B after testing the connection.
    Returns the new connector_id. Only call this when no existing connector
    covers the required data source (check query_graph first).
    """
```

**Deliverable:** The agent (or a human via CLI) can register a new REST API,
SQL database, or CSV source in Graph B with a tested connection, ready for
use in proposed flows.

---

## Phase 6 — Platform CLI

**Goal:** A single unified command-line interface that replaces running individual
scripts and makes the platform operable without knowing internal file paths.

### Tasks

#### 6.1 — `src/platform_cli.py`
Single entry point with subcommands.

```bash
# Index an external system into Graph B
python -m src.platform_cli index --type openapi --source https://api.example.com/openapi.json
python -m src.platform_cli index --type python  --source ./my_service
python -m src.platform_cli index --type db      --source $DATABASE_URL

# Register a new skill from a file
python -m src.platform_cli skill create --name "filter_by_stage" --file ./skills/filter_stage.py

# Register a new connector
python -m src.platform_cli connector create --name "crm_api" --type rest --base-url https://crm.example.com

# List what's in the graph
python -m src.platform_cli skill list
python -m src.platform_cli connector list
python -m src.platform_cli flow list

# Run the agent
python -m src.platform_cli agent run --goal "Improve match quality for Healthtech"
python -m src.platform_cli agent approve --thread-id abc123
python -m src.platform_cli agent reject  --thread-id abc123 --reason "Too risky"

# Validate graph against schema
python -m src.platform_cli schema validate
python -m src.platform_cli schema push      # writes meta-nodes to Neo4j
```

#### 6.2 — `src/config/startup.py`
Runs on every CLI invocation before any other code:
1. `verify_neo4j_connection()` (already in tools.py)
2. Load and validate `schema.yaml`
3. Confirm `artifacts/` directory exists (or GCS bucket is reachable)
4. Print a one-line status: `✓ Neo4j connected | ✓ Schema v1.0 | ✓ Artifact store ready`

**Deliverable:** `python -m src.platform_cli --help` is the single entry point
for the entire platform. No one needs to know which script to run.

---

## Dependency Map

```
Phase 2 (Schema Registry)
  └──► Phase 1 (Indexer)       — needs schema to validate what it writes
  └──► Phase 3 (Storage)       — needs schema for MetadataStore validation
         └──► Phase 4 (Skills)  — needs ArtifactStore + MetadataStore
         └──► Phase 5 (Connectors) — needs MetadataStore
               └──► Phase 6 (CLI) — wraps all phases
```

Build order: **2 → 3 → 1 + 4 + 5 (parallel) → 6**

---

## File Inventory (all new files)

```
src/
├── config/
│   ├── schema.yaml              # node/edge schema (Phase 2)
│   ├── schema_validator.py      # validates dicts against schema (Phase 2)
│   ├── meta_graph.py            # writes schema as Neo4j meta-nodes (Phase 2)
│   ├── storage_config.yaml      # storage backend config (Phase 3)
│   └── startup.py               # pre-flight checks (Phase 6)
│
├── storage/
│   ├── artifact_store.py        # local + GCS artifact backends (Phase 3)
│   └── metadata_store.py        # schema-validating Neo4j wrapper (Phase 3)
│
├── indexer/
│   ├── base_indexer.py          # abstract BaseIndexer (Phase 1)
│   ├── openapi_indexer.py       # OpenAPI/Swagger → Graph B (Phase 1)
│   ├── python_indexer.py        # Python package AST → Graph B (Phase 1)
│   ├── db_indexer.py            # SQL schema → Graph B (Phase 1)
│   ├── graph_writer.py          # IndexedSystem → MetadataStore → Neo4j (Phase 1)
│   └── runner.py                # CLI entry point for indexing (Phase 1)
│
├── skills/
│   ├── skill_validator.py       # syntax + safety check (Phase 4)
│   ├── skill_factory.py         # validate + store + register skill (Phase 4)
│   ├── skill_version_manager.py # DEPRECATED_BY versioning (Phase 4)
│   └── skill_registry.py        # read-only listing + search (Phase 4)
│
├── connectors/
│   ├── connector_config.py      # Pydantic models per connector type (Phase 5)
│   ├── connection_tester.py     # liveness check before registration (Phase 5)
│   └── connector_factory.py     # test + register connector node (Phase 5)
│
├── agents/
│   ├── tools.py                 # ADD: create_skill, create_connector tools
│   └── ... (existing)
│
└── platform_cli.py              # unified CLI (Phase 6)
```

**Modified existing files:**

| File | Change |
|---|---|
| `ecolink-graph/ingest.py` | Replace hardcoded Cypher property lists with SchemaValidator loop |
| `ecolink-graph/queries.py` | No change — already the right abstraction |
| `src/agents/tools.py` | Add `create_skill` and `create_connector` tools |
| `src/agents/nodes.py` | Add startup schema query so Planner knows valid node types |
| `requirements.txt` | Add `pydantic`, `httpx`, `sqlalchemy`, `google-cloud-storage` |

---

## What This Enables

After these phases are complete, to plug the system into a new IT solution:

```bash
# 1. Point the indexer at the target system (one command)
python -m src.platform_cli index --type openapi --source https://new-system.com/openapi.json

# 2. Check what was found
python -m src.platform_cli skill list
python -m src.platform_cli connector list

# 3. Run the agent against the new system's graph
python -m src.platform_cli agent run --goal "Identify underperforming API endpoints and propose optimized flows"

# 4. Agent proposes a new skill if none exists
#    (calls create_skill tool internally)

# 5. Human approves
python -m src.platform_cli agent approve --thread-id <id>
```

The graph now represents the external system. The agent can reason about it,
propose improvements, and register new capabilities — all without any hardcoded
domain knowledge.
