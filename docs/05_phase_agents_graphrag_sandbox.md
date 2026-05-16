# Phase 5: Agents, GraphRAG, Sandbox

## Objective

Make agent reasoning depend on real connected-codebase facts.

## Agent Changes

- Planner retrieves project routes, services, functions, models, workflows, integrations, risks, and skills.
- Critic rejects proposals that are not grounded in graph evidence.
- Simulator uses project-scoped snapshots.
- Evaluator emits JSON recommended actions.

## GraphRAG Retrieval Targets

- Functions
- Routes
- Services
- Models
- Workflows
- Integrations
- Risks
- Sandbox traces
- Approved/rejected proposals

## Recommended Action Shape

```json
{
  "action_type": "create_skill|modify_workflow|add_validation|add_observability|flag_risk|request_admin_approval",
  "project_id": "project id",
  "target_id": "graph node id",
  "title": "short action label",
  "reason": "graph-grounded reason",
  "payload": {}
}
```

## First Tasks

1. Extend GraphRAG retriever to include software graph nodes.
2. Update planner prompt sections for project architecture.
3. Update critic prompt to require project evidence.
4. Stamp sandbox traces with `project_id`.
5. Show recommended JSON actions in the approvals flow.
