# Agentic Layer Fix Plan

The core problem is that the codebase analyzer and the agent decision loop are two disconnected brains. These tasks wire them together, in dependency order.

---

## Task 1 — Extend `AgentState` with project graph fields
**File:** `src/agents/state.py`

Add fields the new agent brain needs to carry:
```python
software_nodes: List[Dict]       # Project -> File -> Route -> Function -> DataStore
project_id: Optional[str]        # scopes all queries to one indexed project
recommended_actions: List[Dict]  # Generator output (replaces proposed_flow_yaml as primary)
critic_evidence_ids: List[str]   # graph node IDs the Critic accepted as grounding
```
`proposed_flow_yaml` stays for backward compatibility but becomes a secondary field.

---

## Task 2 — Planner retrieves project codebase graph first
**File:** `src/agents/nodes.py:188-257`

Right now `retrieve_context()` runs, then the semantic query hardcodes `"mentor matching"` at line 199. Fix:

1. Before calling `retrieve_context()`, query `Project`, `File`, `Route`, `Function`, `DataStore` nodes scoped to `state["app_id"]`.
2. Replace the semantic query string from `"{industry} {goal} mentor matching"` → `"{goal} {app_name}"` so it searches project-relevant skills, not EcoLink matching skills.
3. Pass `software_nodes` into the prompt and into the returned state dict.
4. Update `build_agent_planner_prompt()` to include a `== Codebase Evidence ==` section above the failure/success patterns section.

---

## Task 3 — Generator outputs `RecommendedAction` schema, not only YAML
**File:** `src/agents/nodes.py:281+`

Introduce a Pydantic output model:
```python
class RecommendedAction(BaseModel):
    action_type: str  # create_skill | modify_workflow | add_validation | add_observability | flag_risk | request_admin_approval
    target_node_id: str        # the graph node this action applies to
    evidence_node_ids: List[str]  # project graph nodes that justify this action
    description: str
    flow_yaml: Optional[str]   # only populated if action_type == modify_workflow

class GeneratorOutput(BaseModel):
    recommended_actions: List[RecommendedAction]
    hypothesis_tested: str
```

Update `generator_node()` to use `_structured_invoke(_llm(), prompt, GeneratorOutput)` and write `recommended_actions` into state. Keep emitting `proposed_flow_yaml` for backward compat (take it from the first `modify_workflow` action, if any).

---

## Task 4 — Critic rejects recommendations without project graph evidence
**File:** `src/agents/nodes.py:430-557`

Add a deterministic pre-LLM check after the existing `local_issues` block:

```python
# For each recommended_action, assert evidence_node_ids are real graph nodes
for action in state.get("recommended_actions", []):
    for nid in action.get("evidence_node_ids", []):
        result = query_graph.invoke({"cypher_query": f"MATCH (n) WHERE elementId(n) = '{nid}' RETURN n LIMIT 1"})
        if not result:
            local_issues.append(f"Action '{action['action_type']}' cites non-existent node: {nid}")
```

Also add a check: if `recommended_actions` is empty and `proposed_flow_yaml` is non-empty, require at least one `evidence_node_ids` in the critic LLM prompt — if none are given, `is_valid = False`.

---

## Task 5 — Scope Simulator snapshot to `app_id`
**File:** `src/agents/nodes.py:573-576`

Replace the hardcoded `"snapshot_2025_q4"` with a project-scoped snapshot ID:

```python
app_id = state.get("app_id") or ""
snapshot_id = f"snapshot_{app_id}" if app_id else "snapshot_2025_q4"
result: Dict = simulate_flow.invoke({
    "flow_yaml": flow_yaml,
    "dataset_snapshot_id": snapshot_id,
})
```

Then in `src/agents/tools.py` inside `_build_snapshot()`, filter the Cypher queries by `app_id` when it is set. Also add a secrets blocklist there: never copy fields named `password`, `secret`, `token`, `key`, `credential` into the snapshot dict.

---

## Task 6 — Make Evaluator deterministic, LLM advisory only
**File:** `src/agents/nodes.py:678-746`

Right now the LLM both reasons and decides. Instead:

1. Let the LLM produce `reason` and `updated_hypothesis` only — strip `decision` from `EvaluatorOutput`.
2. After the LLM call, compute `decision` deterministically:
```python
sim_score = latest.get("metrics", {}).get("match_score", 0.0)
sim_status = latest.get("status", "fail")
decision = (
    "success"
    if sim_status == "success" and sim_score > baseline_score * IMPROVEMENT_THRESHOLD
    else "failure"
)
```
3. Log both the LLM's reason and the deterministic result so they are visible in Live Agent Comms.

---

## Task 7 — Structured retry feedback from Critic and Evaluator
**Files:** `src/agents/nodes.py:486-493` and `src/agents/nodes.py:730-744`

Replace the freetext `critic_feedback` and `updated_hypothesis` strings passed back to Generator with a typed dict in state:

```python
retry_context: Optional[Dict]  # add to AgentState
# shape: {invalid_skills, failed_metric, required_change, forbidden_pattern, evidence_node_ids}
```

Critic sets:
```python
"retry_context": {"invalid_skills": unknown_skills, "required_change": "Use only graph-grounded evidence_node_ids"}
```

Evaluator sets:
```python
"retry_context": {"failed_metric": {"match_score": sim_score, "threshold": baseline_score * IMPROVEMENT_THRESHOLD}}
```

Generator reads `state.get("retry_context", {})` and includes it as a structured block in its prompt.

---

## Task 8 — Unify human approval to one path
**Files:** `src/agents/nodes.py:720-730` and `main.py:241-293`

The LangGraph `interrupt()` at `nodes.py:727` is the canonical path. The CLI resume in `main.py` bypasses it and calls `activate_proposal()` directly.

Fix: in `run_resume()` in `main.py`, resume the LangGraph thread through the checkpointer instead of calling the tool directly:

```python
graph.invoke(
    {"human_approved": approved, "rejection_reason": reason},
    config={"configurable": {"thread_id": thread_id}},
)
```

Remove the direct `activate_proposal()` / `reject_proposal()` calls from `run_resume()` — let `human_approval_node()` handle those. Add `human_approved` and `rejection_reason` to `AgentState`.

---

## Task 9 — Deduplicate event emission
**Files:** `src/agents/nodes.py` and `main.py` (streaming section)

The main.py streaming loop emits `publish_event()` for each `node_update` AND each node already calls `_emit_node_event()` internally — those are duplicates. Fix: remove the generic per-node publish from main.py's streaming loop, rely solely on the per-node `_emit_node_event()` calls inside nodes.py. Only let main.py emit the `run_start` and `run_end` envelope events.

---

## Task 10 — Surface decision evidence in Live Agent Comms UI

Each event payload already carries data. Add a collapsible "Evidence" section to each agent message card in the UI that shows:
- `graphrag.failure_patterns` count and list (from Planner event)
- `evidence_node_ids` (from Critic event)
- `failed_metric` (from Evaluator retry event)
- `decision` + deterministic score comparison (from Evaluator success event)

---

## Execution Order

| Priority | Task | Why first |
|---|---|---|
| 1 | Task 1 — AgentState fields | All other tasks depend on it |
| 2 | Task 6 — Deterministic Evaluator | Stops silent LLM math errors immediately |
| 3 | Task 2 — Planner codebase-first | Core brain swap, enables Tasks 3+4 |
| 4 | Task 3 — Generator action schema | Required for Critic evidence check |
| 5 | Task 4 — Critic evidence check | Closes the grounding gap |
| 6 | Task 7 — Structured retry | Makes retries non-shallow |
| 7 | Task 5 — Scoped snapshot | Data correctness |
| 8 | Task 8 — Unified approval | Consistency, no functional regression |
| 9 | Task 9 — Deduplicate events | Polish |
| 10 | Task 10 — UI evidence panel | Observable, last because it reads from fixed events |

Tasks 1, 2, 3, 4, 6 form the minimum viable "codebase-first brain."
