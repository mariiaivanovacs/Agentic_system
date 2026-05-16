# Phase 4: UI Refactor

## Objective

Reframe the Streamlit app around connected software projects.

## Pages

1. `Project Review`
2. `Graph Display`
3. `Real-Time Agents`
4. `Flows`
5. `Agentic Architecture`

## Page Behavior

- `Project Review` owns connection, permission, scan status, and codebase summary.
- `Graph Display` shows software architecture, database/workflow graph, and agentic layer graph.
- `Real-Time Agents` shows static topology and live communication.
- `Flows` shows detected workflows, backend/API/database pipelines, sandbox runs, and approvals.
- `Agentic Architecture` shows skills, artifacts, primitives, GraphRAG, and run-agent controls.

## First Tasks

1. Rename `Database Review` to `Project Review`.
2. Rename `Web & Database Flows` to `Flows`.
3. Replace URL-first ingest UI with codebase analysis.
4. Keep website ingestion as optional supporting evidence after analysis.
5. Disable non-review pages until analysis completes.
