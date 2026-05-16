Here is the comprehensive Markdown file designed to be fed directly into an AI coding assistant (like Cursor, Copilot Workspace, or a custom LangChain agent) to build **Flow 2: The Agentic Layer**.

This file contains all necessary context, architectural constraints, schema definitions, and step-by-step implementation instructions.

***

# 🤖 Flow 2: The Agentic Layer Implementation Plan

## 1. Project Context & Goal
**Project Name:** EcoLink NeuroCore
**Current Phase:** Phase 2 - Building the Agentic Layer (The Brain)
**Objective:** Build a multi-agent system using **LangGraph** and **Vertex AI (Gemini 1.5 Pro)** that can:
1.  **Observe:** Query the Dual-Graph Database (Neo4j) to understand historical performance (Graph A) and current system capabilities (Graph B).
2.  **Analyze:** Identify inefficiencies, errors, or optimization opportunities.
3.  **Propose:** Generate new `Flow` definitions (YAML) or update existing ones.
4.  **Simulate:** Trigger the Secure Sandbox to test proposals against historical data.
5.  **Learn:** Update the Graph with execution traces and feedback.

**Tech Stack:**
*   **Language:** Python 3.11+
*   **Framework:** LangGraph (for stateful multi-agent orchestration)
*   **LLM:** Google Vertex AI (Gemini 1.5 Pro)
*   **Database:** Neo4j AuraDB (via `neo4j` driver)
*   **Sandbox Interface:** Google Cloud Run Jobs API (or Docker SDK for local dev)
*   **State Management:** Redis (for LangGraph checkpointing)

---

## 2. Architectural Constraints & Rules

### 🚫 Strict Constraints
1.  **No Direct Code Execution:** The Agent must NEVER execute raw Python/Java code directly. It must only generate YAML/JSON configurations that are passed to the **Sandbox Executor**.
2.  **Read-Only Graph Access:** The Agent’s query tools must use read-only Cypher queries unless explicitly performing a "Proposal Commit" via a specific approved tool.
3.  **Capability-Based Security:** All sandbox triggers must include a `capability_token` (JWT) that restricts which Connectors/Skills can be used.
4.  **Immutable History:** Historical data in Graph A is never modified. Only new `Execution_Trace` nodes are added.

### ✅ Design Patterns
*   **ReAct Pattern:** The Agent should use Reasoning + Acting (Tool Use) loops.
*   **Human-in-the-Loop:** High-impact changes (e.g., deleting a Connector) must pause the graph and wait for human approval via the UI.
*   **Self-Correction:** If a Sandbox simulation fails, the Agent must analyze the error log and propose a fix before giving up.

---

## 3. Data Schema Reference (Neo4j)

The Agent must understand these node types and relationships to query effectively.

### Graph A: Historical Data (Memory)
```cypher
// Nodes
(:Company {id: str, industry: str, stage: str, pain_points: list})
(:Mentor {id: str, expertise: list, availability: str})
(:Outcome {score: float, feedback: str, date: datetime})

// Relationships
(:Company)-[:MATCHED_WITH {program: str, date: datetime}]->(:Mentor)
(:Mentor)-[:PRODUCED]->(:Outcome)
```

### Graph B: Functional Blueprint (System)
```cypher
// Nodes
(:Connector {id: str, type: str, version: str, language: str})
(:Skill {id: str, name: str, input_schema: json, output_schema: json})
(:Flow {id: str, status: str, yaml_config: str}) // status: 'active', 'deprecated', 'proposed'
(:Server {id: str, load: float, error_rate: float})

// Relationships
(:Flow)-[:USES_SKILL]->(:Skill)
(:Flow)-[:USES_CONNECTOR]->(:Connector)
(:Flow)-[:RUNS_ON]->(:Server)
```

### The Bridge: Execution Traces
```cypher
// Node
(:ExecutionTrace {id: str, start_time: datetime, end_time: datetime, status: str, error_log: str})

// Relationships
(:ExecutionTrace)-[:RAN_FLOW]->(:Flow)
(:ExecutionTrace)-[:PROCESSED_COMPANY]->(:Company)
(:ExecutionTrace)-[:RESULTED_IN]->(:Outcome)
(:ExecutionTrace)-[:USED_SERVER]->(:Server)
```

---

## 4. Agent Tools Definition

Implement these functions as LangGraph tools. Each tool must have a clear docstring for the LLM.

### Tool 1: `query_graph(cypher_query: str) -> List[Dict]`
*   **Description:** Executes a read-only Cypher query against Neo4j.
*   **Usage:** Use this to find historical patterns (Graph A) or inspect system components (Graph B).
*   **Example:** `"MATCH (c:Company)-[:MATCHED_WITH]->(m:Mentor) WHERE c.industry = 'Fintech' RETURN c.id, m.id, outcome.score"`

### Tool 2: `simulate_flow(flow_yaml: str, dataset_snapshot_id: str) -> Dict`
*   **Description:** Sends a proposed flow configuration to the Secure Sandbox.
*   **Input:**
    *   `flow_yaml`: The YAML definition of the new flow.
    *   `dataset_snapshot_id`: ID of the historical data snapshot to test against.
*   **Output:** JSON containing `status` (success/fail), `metrics` (latency, match_score), and `error_log` if failed.
*   **Constraint:** This tool automatically generates a capability token for the sandbox.

### Tool 3: `get_infrastructure_status() -> Dict`
*   **Description:** Returns current load and error rates of available servers from Graph B.
*   **Usage:** Use this to ensure proposed flows don't overload specific servers.

### Tool 4: `propose_change(change_type: str, details: Dict) -> str`
*   **Description:** Saves a proposed change to the Graph as a 'proposed' node.
*   **Input:**
    *   `change_type`: 'new_flow', 'update_connector', 'deprecate_skill'.
    *   `details`: The YAML/JSON content of the change.
*   **Output:** The ID of the proposed node (e.g., `flow_proposal_99`).
*   **Note:** This does NOT activate the change. It waits for Human Approval.

---

## 5. LangGraph State & Workflow

### State Schema
```python
from typing import TypedDict, Annotated, Sequence
import operator

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    goal: str  # e.g., "Optimize Fintech matching"
    current_hypothesis: str
    simulation_results: List[Dict]
    proposed_flow_yaml: str
    human_approval_required: bool
    final_output: str
```

### Workflow Steps (The "Brain" Loop)

1.  **Planner Node:**
    *   Receives `goal`.
    *   Calls `query_graph` to analyze historical failures in Graph A.
    *   Calls `query_graph` to inspect current Flows in Graph B.
    *   Identifies a bottleneck (e.g., "Flow_V1 uses `random_sort` which has low scores").
    *   Formulates a hypothesis: "Replacing `random_sort` with `semantic_similarity` will improve scores."

2.  **Generator Node:**
    *   Generates a new `flow_yaml` based on the hypothesis.
    *   Ensures the YAML references valid `Skill` and `Connector` IDs from Graph B.

3.  **Critic Node (Self-Correction):**
    *   Reviews the `flow_yaml` for syntax errors or invalid references.
    *   Checks `get_infrastructure_status` to ensure resource availability.
    *   If invalid, loops back to Generator.

4.  **Simulator Node:**
    *   Calls `simulate_flow` with the new YAML.
    *   Receives metrics and logs.

5.  **Evaluator Node:**
    *   Compares simulation metrics against historical baseline.
    *   If **Success**: Calls `propose_change` to save to Graph. Sets `human_approval_required = True`.
    *   If **Failure**: Analyzes `error_log`. Updates `current_hypothesis` to try a different approach. Loops back to Generator.

6.  **Human Approval Node (Interrupt):**
    *   Pauses execution. Waits for external UI signal to approve/reject.
    *   If Approved: Marks Flow as 'active' in Graph B.
    *   If Rejected: Logs reason in Graph A as negative feedback.

---

## 6. Implementation Steps for AI Assistant

Please implement the following files in order:

### Step 1: `src/agents/tools.py`
*   Implement the 4 tools defined above.
*   Use `neo4j.Driver` for graph queries.
*   Use `google.cloud.run_v2.JobsClient` for sandbox triggering (mock this for local dev if needed).

### Step 2: `src/agents/state.py`
*   Define the `AgentState` TypedDict.

### Step 3: `src/agents/nodes.py`
*   Implement `planner_node`, `generator_node`, `critic_node`, `simulator_node`, `evaluator_node`.
*   Use `vertexai.generative_models.GenerativeModel` for LLM calls.
*   Ensure each node updates the `AgentState` correctly.

### Step 4: `src/agents/graph.py`
*   Build the LangGraph `StateGraph`.
*   Add edges: Planner -> Generator -> Critic -> Simulator -> Evaluator.
*   Add conditional edges: Evaluator -> Generator (if fail), Evaluator -> HumanApproval (if success).
*   Compile the graph.

### Step 5: `src/main.py`
*   Create a simple CLI entry point:
    ```python
    python main.py --goal "Improve match quality for Healthtech startups"
    ```
*   Print the final `proposed_flow_yaml` and `simulation_results`.

---

## 7. Sample Data for Testing

Use this sample data to verify the Agent's logic:

**Graph A (History):**
*   Company C1 (Fintech) matched with Mentor M1 via Flow_V1 -> Score: 2.0 (Low)
*   Company C2 (Fintech) matched with Mentor M2 via Flow_V2 -> Score: 8.5 (High)

**Graph B (System):**
*   Flow_V1 uses Skill: `random_sort`
*   Flow_V2 uses Skill: `semantic_similarity`

**Expected Agent Behavior:**
1.  Query finds Flow_V1 has low scores for Fintech.
2.  Query finds Flow_V2 has high scores.
3.  Hypothesis: "Replace `random_sort` with `semantic_similarity` in Flow_V1."
4.  Generate new YAML for Flow_V1_Beta.
5.  Simulate Flow_V1_Beta.
6.  Propose change.

---

## 8. Error Handling & Logging

*   **LLM Errors:** If Gemini returns invalid JSON/YAML, the Critic Node must catch it and request a retry.
*   **Sandbox Timeouts:** If `simulate_flow` takes > 300s, treat as failure with error "Timeout".
*   **Graph Connection Errors:** Retry Neo4j connections 3 times before failing.

---

## 9. Next Steps After Implementation

1.  Run `pytest tests/test_agent_tools.py` to verify tool connectivity.
2.  Run `python main.py` with a simple goal.
3.  Check Neo4j Browser to see if `ExecutionTrace` nodes are created.
4.  Integrate with the Streamlit UI (Flow 5) to handle the `human_approval_required` interrupt.