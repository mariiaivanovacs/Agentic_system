Now I have everything I need. Here is the full audit and phased plan.

Audit Verdict: Demo-Ready Only
The pipeline architecture is genuinely good. Real Neo4j reads/writes work end-to-end, the 6-node LangGraph loop is complete, and the human approval interrupt is real. But several things your analysis called "missing" are exactly the gaps that prevent this from being sellable as a general agentic layer. Here is what the code actually shows.

What the Code Actually Confirms
Implemented and real
Component	Evidence
Neo4j reads in agents	tools.py:89 — _run_read_cypher with 3-retry tenacity
Neo4j writes	tools.py:99 — _run_write_cypher, used by propose/activate/reject
Planner queries real graph	nodes.py:196-227 — two Cypher queries before LLM call
Critic validates vs real graph	nodes.py:442-482 — skill IDs from Neo4j, not hardcoded
Local sandbox subprocess	tools.py:286-368 — sandbox_task.py via subprocess with DATA_STREAM protocol
Web indexer — real crawl	web_indexer.py — full BFS crawler, writes WebSite/WebPage/WebEntity to Neo4j
Source-code extraction	web_indexer.py:367-468 — React routes (App.tsx), Clarity contracts (.clar), campaign data
Retry + fallback in snapshot	tools.py:177-229 — tries Neo4j, falls back to 2-item sample only on failure
Realtime event bus	event_bus.py + server.py (FastAPI) confirmed in import tree
Human approval interrupt	nodes.py:727 — interrupt({...})
Broken or missing
Component	Status	Evidence
Cloud Run result	Broken	tools.py:411 — match_score: 0.0 hardcoded, logs never fetched
Graph visualization in Streamlit	Missing	graph_ecolink.html exists but not embedded (no components.html call found in first 100 lines)
CustomerApplicationProfile	Not implemented	No such model in state.py, schema.yaml, or any page
Pipeline discovery graph	Not implemented	Indexer extracts entities but never connects them into Route→API→Tool chains
Skill registry / SkillProposal	Not implemented	Generator proposes skills in YAML but no SkillProposal Neo4j node, no registry query
Per-app graph namespace	Not implemented	No app_id property on nodes, all apps share one graph
Data isolation policy	Not implemented	Snapshot is unscoped Neo4j query, no secrets exclusion contract
GraphRAG (vector retrieval)	Not implemented	All retrieval is Cypher-only; no embeddings, no similarity search
OpenAPI/Python/DB indexers	Stubs	Classes exist, discover() implemented for OpenAPI, but graph_writer.py write path untested; Python and DB indexers are thin wrappers
Skill creation lifecycle	Not implemented	No SkillProposal node, no approval gate for new skills
Phased Implementation Plan
Phase 1 — Fix the Two Demo-Blocking Issues (1–2 days)
1a. Embed graph visualization in Streamlit

streamlit_app.py — Graph View page needs:


# In the "Graph View" page section
html_path = ROOT / "graph_ecolink.html"
if html_path.exists():
    components.html(html_path.read_text(), height=600, scrolling=True)
Acceptance: Graph View page renders the pyvis graph without opening a separate file.

1b. Fix Cloud Run result parsing

src/agents/tools.py:407-415 — _cloud_run_sandbox returns match_score: 0.0 always because Cloud Run logs are not fetched. Two options — pick one:

Poll Cloud Logging for the DATA_STREAM_START/END output after the operation completes
Or, as a safer short-term fix, add a clear error_log that says "Cloud Run mode does not yet return metrics — use local mode" and route callers back to _local_sandbox
Acceptance: SANDBOX_MODE=cloudrun either returns real metrics or returns an explicit actionable error, never a silent match_score: 0.0.

Phase 2 — CustomerApplicationProfile (2–3 days)
2a. Add profile to state

src/agents/state.py — add:


app_id: str                    # "" means system-default (the seeded EcoLink graph)
app_name: str
source_type: str               # "codebase" | "website" | "api" | "database"
source_paths: List[str]
base_urls: List[str]
last_indexed_at: str
2b. Write profile to Neo4j on ingest

src/indexer/web_indexer.py:496-497 — after _write_website_node, also MERGE an AppProfile node:


MERGE (ap:AppProfile {app_id: $app_id})
SET ap.app_name = $app_name, ap.source_type = 'website',
    ap.base_url = $start_url, ap.last_indexed_at = datetime()
MERGE (ap)-[:HAS_WEBSITE]->(w)
2c. Add "Connected App" Streamlit page

streamlit_app.py — new sidebar item. Shows: app_id, name, source type, last indexed timestamp, entity counts (WebPage, WebEntity by type), quick re-index button.

Acceptance: after running web indexer, the Connected App page shows the profile pulled from Neo4j.

Phase 3 — Pipeline Discovery Graph (3–4 days)
The current indexer extracts nodes but never connects them into a traversable pipeline chain. The missing step is a graph-building pass after crawl.

3a. Add pipeline linker to web_indexer

src/indexer/web_indexer.py — after writing all entities, run a Cypher pass:


// Connect Route → ContractMethod if method name appears in route path or component
MATCH (r:WebEntity {entity_type: 'Route'}), (m:WebEntity {entity_type: 'ContractMethod'})
WHERE r.name CONTAINS toLower(m.name)
MERGE (r)-[:CALLS]->(m)
Similarly link WebSite → Route → Feature.

3b. Add pipeline materializer node

New file src/indexer/pipeline_builder.py — after full crawl + entity linking, detect chains and write Pipeline nodes:


CREATE (p:Pipeline {
  id: $pipeline_id,
  name: $name,
  entrypoint: $route,
  app_id: $app_id,
  steps: $steps_json,
  discovered_at: datetime()
})
3c. Pipeline Explorer page in Streamlit

Shows: pipeline name, entrypoint route, steps list, tools/skills referenced, risk flag (if contract method involved). Acceptance: ingest fundraising app → Pipeline Explorer shows at least 2 pipelines.

Phase 4 — Skill Registry + Skill Proposals (2–3 days)
4a. Add SkillProposal node type

ecolink-graph/queries.py — add create_skill_proposal(skill_id, name, purpose, input_schema, output_schema, proposed_by) that writes:


CREATE (:SkillProposal {
  id: $skill_id, name: $name, purpose: $purpose,
  status: 'proposed', proposed_by: $proposed_by,
  input_schema: $input_schema, output_schema: $output_schema,
  created_at: datetime()
})
4b. Critic rejects unknown skills

src/agents/nodes.py:462-465 — unknown_skills detection is already there. Strengthen: also check SkillProposal nodes (status='approved') as a secondary valid-skills source:


MATCH (s:SkillProposal {status: 'approved'}) RETURN s.id AS id
4c. Skill Registry page in Streamlit

Shows: active skills (from Skill nodes), proposed skills (from SkillProposal nodes), approve/reject buttons. Acceptance: admin can approve a skill proposal in the UI and it becomes usable by the Generator in the next run.

Phase 5 — Data Isolation (2–3 days)
This is the most important gap for a sellable product.

5a. Add app_id to all indexed nodes

src/indexer/web_indexer.py — every _write_entity, _write_page_node call adds app_id: $domain as a property. This scopes all data to the source application.

5b. Scope sandbox snapshot to app_id

src/agents/tools.py:177-229 — _build_snapshot currently queries all Company and Mentor nodes with no filter. Add app_id parameter and filter Cypher by it when set:


MATCH (c:Company) WHERE c.app_id = $app_id OR $app_id = ''
5c. Exclude secrets from snapshot

Add explicit property blocklist in _build_snapshot: never copy fields named password, secret, token, key, credential into the snapshot dict.

5d. Data Isolation page in Streamlit

Shows: per-app node counts, isolation policy (app_id scoped: yes/no), last snapshot size, whether any snapshot contained cross-app data. Acceptance: two different apps indexed show separate node sets in Neo4j and separate snapshots in simulation.

Phase 6 — GraphRAG Module (3–5 days)
This is the highest-value missing piece given the name of the project.

6a. Add embedding generation on ingest

src/indexer/web_indexer.py or a new src/indexer/embedder.py — after writing entities, generate text embeddings (Gemini text-embedding-004 or OpenAI) for description fields. Store as embedding property (Neo4j 5+ vector index).

6b. Add vector index to Neo4j


CREATE VECTOR INDEX skill_embedding IF NOT EXISTS
FOR (s:Skill) ON (s.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}
6c. Add query_graph_semantic tool

src/agents/tools.py — new @tool that takes a natural language query, embeds it, and does db.index.vector.queryNodes(...) to return semantically similar skills/flows.

6d. Use in Planner

src/agents/nodes.py:185-300 — Planner currently uses only Cypher. After getting failure_patterns, also call query_graph_semantic to find skills that match the pain point text semantically, not just by ID.

Acceptance: querying "payment processing friction" returns Skill nodes related to payment even if the word "payment" is not in their ID.

Phase 7 — Tests and Acceptance (2–3 days)
Missing tests to write:

Test	File	What to assert
Real web ingest	test_integration.py	crawl localhost:5173, assert ≥3 Route nodes in Neo4j
Profile creation	test_integration.py	after ingest, AppProfile node exists with correct app_id
Pipeline discovery	test_integration.py	Pipeline node exists with ≥1 step after linker runs
Critic rejects unknown skill	test_agents.py (new)	YAML with skill: fake_skill_xyz → critic_passed: False
Snapshot isolation	test_sandbox.py	snapshot for app_id=A contains no nodes from app_id=B
Skill proposal lifecycle	test_integration.py	create SkillProposal → approve → appears in valid_skills query
Cloud Run error surfaced	test_sandbox.py	SANDBOX_MODE=cloudrun without env vars returns status: fail with message, not silent 0.0
Priority Order for Next Session
If you have limited time before a demo:

Phase 1 — embed the graph viz + fix Cloud Run silent failure (visual impact, 1 day)
Phase 2 — CustomerApplicationProfile (shows the "sellable layer" concept, 1-2 days)
Phase 4 — Skill Registry page (highest impression for AI-native judges, 1 day)
Phase 3 — Pipeline Explorer (technically impressive, 2-3 days)
Phase 5 — Data Isolation (required for enterprise credibility, 2 days)
Phase 6 — GraphRAG (highest effort, highest payoff long-term)
