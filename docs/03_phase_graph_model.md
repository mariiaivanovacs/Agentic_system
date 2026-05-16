# Phase 3: Software Graph Model

## Objective

Make the Neo4j graph project-first while retaining website nodes as optional supporting evidence.

## Required Node Metadata

Every analyzer-created software node must include:

- `project_id`
- `scan_id`
- `source_path`
- `confidence`
- `created_at`

## Main Node Labels

- `Project`
- `Repository`
- `File`
- `Module`
- `Route`
- `Service`
- `Function`
- `DatabaseModel`
- `Entity`
- `Workflow`
- `Integration`
- `Artifact`
- `Risk`

## Main Relationships

- `PROJECT_HAS_REPOSITORY`
- `REPOSITORY_HAS_FILE`
- `FILE_DEFINES_FUNCTION`
- `FILE_DEFINES_ROUTE`
- `FILE_DEFINES_MODEL`
- `FILE_USES_INTEGRATION`
- `WORKFLOW_USES_ROUTE`
- `WORKFLOW_TOUCHES_ENTITY`
- `SKILL_DERIVED_FROM_FUNCTION`
- `RISK_FOUND_IN`

## First Tasks

1. Extend `src/config/schema.yaml`.
2. Update `GraphWriter` for code nodes and relationships.
3. Use stable IDs and `MERGE` for idempotent scans.
4. Add tests for repeated analyzer runs.
