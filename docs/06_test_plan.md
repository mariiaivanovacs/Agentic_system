# Test Plan

## Permission Tests

- No connected project shows onboarding/permission state.
- Graph, flow, and agent pages are gated before analysis completion.
- Approval creates or updates project metadata.

## Analyzer Tests

- Analyzer scans a local repo without mutating source files.
- Analyzer ignores `.git`, `node_modules`, virtualenvs, caches, build folders, and secret files.
- Analyzer extracts routes, functions, models, workflows, integrations, risks, and skills from a representative project.
- Repeated scans produce stable IDs.

## Graph Tests

- Every analyzer-created node has `project_id` and `scan_id`.
- Software graph relationships are written.
- Repeated graph writes update existing nodes instead of duplicating them.

## UI Tests

- Five pages render in states: no project, approved but not scanned, scan complete, scan failed.
- URL ingestion is not the first-run path.
- Website crawling appears only as optional supporting evidence.

## Agent Tests

- Planner retrieves codebase facts.
- Critic rejects recommendations without graph evidence.
- Sandbox logs project-scoped traces.
- Approval flow displays recommended JSON actions.
