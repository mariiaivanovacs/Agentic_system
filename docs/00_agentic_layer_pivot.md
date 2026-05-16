# Agentic Layer Pivot

## Goal

EcoLink NeuroCore should behave as an agentic analysis layer that attaches to an existing software project, asks permission to inspect the codebase, extracts real architecture facts, and then uses those facts for graph analysis, GraphRAG retrieval, sandbox simulation, and admin-approved recommendations.

## Product Shift

The current app has useful foundations, but its primary path is still website/URL ingestion. The target product is codebase-first:

1. A user connects a project repository.
2. The app asks for analysis permission.
3. The analyzer reads backend/source files with secret-safe ignore rules.
4. Extracted entities, workflows, services, routes, models, integrations, skills, and risks are written to Neo4j.
5. The graph, agents, sandbox, and recommendation UI become available only after analysis is complete.

## Target Flow

```text
Connected Codebase
  -> Permission Gate
  -> Codebase Analyzer
  -> Project Software Graph in Neo4j
  -> GraphRAG Retrieval
  -> Planner / Generator / Critic / Simulator / Evaluator
  -> Sandbox Run
  -> Admin Approval
  -> Recommended JSON Actions
```

## Key Principles

- Real connected-project data is the default source of truth.
- Synthetic/demo data must be explicit demo mode only.
- Website crawling is optional supporting evidence, not the main connection mechanism.
- Every analyzer-created graph record must be project-scoped.
- The UI must not expose graph/agent controls before the project is approved and analyzed.
