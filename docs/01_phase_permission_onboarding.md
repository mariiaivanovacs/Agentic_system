# Phase 1: Permission-First Project Onboarding

## Objective

Replace first-run dashboard behavior with a permission-first project connection flow.

## Required Behavior

- If no project exists, show a `Project Review` page with a connection form.
- Ask permission before analyzing code.
- Explain what is read: source files, routes, services, models, workflows, integrations.
- Explain what is excluded: secrets, credentials, dependency folders, build artifacts, caches.
- Store project metadata in Neo4j.
- Disable graph, flow, and agent pages until analysis is complete.

## Project States

- `not_connected`
- `permission_required`
- `approved`
- `analysis_running`
- `analysis_complete`
- `analysis_failed`

## Stored Project Fields

- `project_id`
- `name`
- `repo_path`
- `permission_status`
- `analysis_status`
- `created_at`
- `updated_at`
- `last_scan_id`

## First Tasks

1. Add `Project Review` to the Streamlit sidebar.
2. Create project connection form for local repository path.
3. Add `Approve Analysis` action.
4. Store/update `Project` node in Neo4j.
5. Gate all deeper pages until the project has `analysis_status = analysis_complete`.
