# Skill Modification System - Implementation Guide

## Overview

The agentic layer now has **full capability to modify existing skills** through a proposal-based workflow. This addresses the previous gap where skills were immutable once created.

## Previous State vs. New State

### Before (Immutable Skills)
- ✗ Could only CREATE new skills (via SkillProposal)
- ✗ Could not UPDATE existing skill properties
- ✗ No versioning or evolution tracking
- ✗ Performance improvements couldn't be reflected in the skill database

### After (Modifiable Skills)
- ✓ Can PROPOSE modifications to existing skills
- ✓ Can UPDATE: name, description, performance_score, avg_execution_ms, language
- ✓ Full audit trail via SkillModificationProposal nodes
- ✓ Agent can suggest improvements based on simulation results
- ✓ Human-in-the-loop approval process

---

## Architecture

### Neo4j Node Types

#### **Skill** (Immutable Core)
```
Node labels: [:Skill]
Properties:
  - id (string, unique)
  - name (string)
  - description (string)
  - performance_score (float, 0-10)
  - avg_execution_ms (float)
  - language (string)
  - last_modified_at (datetime)  [NEW - added during approval]
```

#### **SkillModificationProposal** (NEW)
```
Node labels: [:SkillModificationProposal]
Properties:
  - id (string, same as the target Skill ID)
  - reason (string)
  - proposed_by (string) - "agent" or human name
  - status (string) - 'proposed', 'approved', 'rejected'
  - created_at (datetime)
  - rejection_reason (string) [if rejected]
  
  Proposed fields (only set if being changed):
  - proposed_name (string)
  - proposed_description (string)
  - proposed_performance_score (float)
  - proposed_avg_execution_ms (float)
  - proposed_language (string)
```

---

## API Reference

### Tool: `propose_skill_update()`

**Location**: `src/agents/tools.py` (marked with `@tool` decorator - available to LLM agents)

**Signature**:
```python
@tool
def propose_skill_update(
    skill_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    performance_score: Optional[float] = None,
    avg_execution_ms: Optional[float] = None,
    language: Optional[str] = None,
    reason: str = "Performance tuning",
) -> str:
```

**Parameters**:
- `skill_id`: ID of the existing Skill to modify
- `name`: New skill name (optional)
- `description`: New skill description (optional)
- `performance_score`: Updated score 0-10 (optional)
- `avg_execution_ms`: Updated execution time in milliseconds (optional)
- `language`: Programming language/runtime (optional)
- `reason`: Why the modification is needed (e.g., "Better performance observed")

**Returns**: String describing the created proposal ID

**Raises**: 
- `ValueError` if all update fields are None (at least one must be provided)

**Example**:
```python
from src.agents.tools import propose_skill_update

# Propose performance update after successful simulation
result = propose_skill_update.invoke({
    "skill_id": "skill_data_validation",
    "performance_score": 8.5,
    "avg_execution_ms": 45.2,
    "reason": "Performance improvement from simulation score=8.50",
})
# Returns: "Skill modification proposal created (id=skill_data_validation, reason=...)"
```

---

### Tool: `get_skill_modification_proposals()`

**Location**: `src/agents/tools.py` (marked with `@tool` decorator)

**Signature**:
```python
@tool
def get_skill_modification_proposals(
    status: Optional[str] = None,
) -> List[Dict]:
```

**Parameters**:
- `status`: Filter by status - 'proposed', 'approved', or 'rejected' (None = all)

**Returns**: List of dicts with all proposal fields

**Example**:
```python
# Get all pending modifications
pending = get_skill_modification_proposals.invoke({"status": "proposed"})
for proposal in pending:
    print(f"{proposal['id']}: {proposal['reason']}")
```

---

### Internal Functions (Non-Tool)

#### `approve_skill_modification(skill_id)`
**Location**: `src/agents/tools.py`

Applies a SkillModificationProposal to the actual Skill node:
- Copies all `proposed_*` fields to actual properties
- Sets `last_modified_at = now()`
- Marks proposal status as 'approved'

Called by: Streamlit admin UI, evaluator_node on auto-approval

```python
from src.agents.tools import approve_skill_modification
approve_skill_modification("skill_data_validation")
```

---

#### `reject_skill_modification(skill_id, reason)`
**Location**: `src/agents/tools.py`

Rejects a modification proposal:
- Sets proposal status to 'rejected'
- Records the rejection_reason

Called by: Streamlit admin UI, or programmatic rejection logic

```python
from src.agents.tools import reject_skill_modification
reject_skill_modification("skill_data_validation", "Score too aggressive")
```

---

### Graph Queries (Lower-level)

#### In `ecolink-graph/queries.py`:

- **`create_skill_modification_proposal(...)`** - Creates a new SkillModificationProposal node
- **`get_skill_modification_proposals(status)`** - Queries proposals by status
- **`approve_skill_modification(skill_id)`** - Applies proposal to Skill and marks approved
- **`reject_skill_modification(skill_id, reason)`** - Rejects proposal

---

## Integration Points

### 1. Evaluator Node (src/agents/nodes.py)

**When**: After successful simulation
**What**: Automatically proposes skill updates

```python
# In evaluator_node, after checking if decision == "success":

for skill_id in skills_used:
    propose_skill_update.invoke({
        "skill_id": skill_id,
        "performance_score": min(10.0, 5.0 + (sim_score / 2.0)),
        "avg_execution_ms": exec_time_ms,
        "reason": f"Performance improvement from successful simulation",
    })
```

**Benefit**: Agent learns and suggests skill improvements without human intervention

---

### 2. Streamlit Admin UI (streamlit_app.py)

**Tab**: "Skill Modification Proposals"

**Features**:
- View all proposals grouped by status (proposed/approved/rejected)
- See proposed changes side-by-side with current values
- Approve multiple proposals with one click
- Reject with custom reason
- Audit trail with timestamps and proposer info

---

## Workflow Example

### Scenario: Improving "data_validation" Skill

```
1. PROPOSAL PHASE (Agent)
   ├─ Evaluator runs successful simulation with data_validation skill
   ├─ Simulation score: 8.5, execution time: 45.2ms
   └─ Agent calls: propose_skill_update(
         skill_id="data_validation",
         performance_score=8.5,
         avg_execution_ms=45.2,
         reason="Improved after optimization"
      )
      
2. PROPOSAL CREATED IN NEO4J
   └─ SkillModificationProposal node created:
      {
        id: "data_validation",
        status: "proposed",
        proposed_performance_score: 8.5,
        proposed_avg_execution_ms: 45.2,
        reason: "Improved after optimization",
        proposed_by: "agent",
        created_at: datetime()
      }

3. HUMAN REVIEW (Streamlit UI)
   ├─ Admin sees modification in "Skill Modification Proposals" tab
   ├─ Views changes:
   │   Current:  performance_score=6.2, avg_execution_ms=120.0
   │   Proposed: performance_score=8.5, avg_execution_ms=45.2
   └─ Clicks "Approve" button

4. APPROVAL APPLIED (Backend)
   ├─ Cypher query merges proposed fields to Skill node:
   │   MATCH (p:SkillModificationProposal {id: "data_validation"})
   │   MATCH (s:Skill {id: "data_validation"})
   │   SET s.performance_score = 8.5,
   │       s.avg_execution_ms = 45.2,
   │       s.last_modified_at = datetime(),
   │       p.status = 'approved'
   └─ Success

5. ACTIVATION
   └─ On next flow generation, Critic sees updated skill metrics
      and can make better decisions about using this skill
```

---

## Benefits Over Manual Updates

| Aspect | Before | After |
|--------|--------|-------|
| **Modification** | Not possible | Via SkillModificationProposal |
| **Auditability** | N/A | Full history of proposed changes |
| **Learning** | Agent can't improve skills | Agent auto-suggests improvements |
| **Performance** | Stale metrics | Skills self-improve via simulation feedback |
| **Risk** | N/A | Human approval gates risky changes |
| **Versioning** | N/A | Timestamp of `last_modified_at` |

---

## Error Handling

### Case 1: No Update Fields Provided
```python
try:
    propose_skill_update.invoke({
        "skill_id": "test_skill",
        # Missing all update fields!
        "reason": "Test"
    })
except ValueError as e:
    print(e)  # "At least one of name, description, ... must be provided"
```

### Case 2: Skill Doesn't Exist
- Approval will fail silently (Cypher MATCH won't find the Skill)
- Admin sees error in logs
- Proposal remains with status 'approved' but no changes applied
- **Recommendation**: Pre-validate skill_id exists before proposing

### Case 3: Neo4j Connection Error
- Tools have built-in retry logic (3 attempts)
- Logs warning and returns empty result
- Can retry manually

---

## Performance Considerations

### Query Performance
- **Creating proposal**: O(1) - single node merge
- **Query proposals**: O(n) where n = number of proposals (indexed by status)
- **Approving**: O(1) - direct node updates
- **Rejecting**: O(1) - direct node updates

### Recommendations
- Index on `SkillModificationProposal.status` and `created_at` (run `create_vector_indexes()`)
- Archive old rejected proposals periodically
- Cache query results for proposals in Streamlit

---

## Neo4j Schema

### Create indexes for performance (run once):
```cypher
CREATE INDEX ON :SkillModificationProposal(status);
CREATE INDEX ON :SkillModificationProposal(created_at);
CREATE INDEX ON :Skill(last_modified_at);
```

### Cypher for testing:
```cypher
-- See all proposed modifications
MATCH (p:SkillModificationProposal {status: 'proposed'})
RETURN p.id, p.reason, p.proposed_by, p.created_at;

-- See modification history for a skill
MATCH (p:SkillModificationProposal {id: "skill_id"})
RETURN p.status, p.reason, p.created_at ORDER BY p.created_at DESC;

-- See current state of a skill
MATCH (s:Skill {id: "skill_id"})
RETURN s.name, s.performance_score, s.avg_execution_ms, s.last_modified_at;
```

---

## Testing

Run the test suite:
```bash
python test_skill_modification.py
```

This test demonstrates:
1. Creating modification proposals
2. Querying proposals by status
3. Approving modifications (applying to Skill)
4. Rejecting modifications
5. Error handling (missing fields)

---

## Future Enhancements

Potential additions to this system:

1. **Rollback Capability**
   - Keep version history of Skill nodes
   - Allow reverting to previous performance metrics

2. **Auto-Approval Thresholds**
   - Auto-approve small improvements (e.g., < 5% change)
   - Flag large changes for manual review

3. **Skill Deprecation**
   - Add `deprecated_at` field
   - Create `SkillDeprecationProposal` for retiring skills

4. **Skill Versioning**
   - Track skill_version on Skill nodes
   - Create `SkillVersion` relationship to track history

5. **Performance Alerting**
   - Alert if a skill's metrics degrade significantly
   - Trigger automatic rollback in critical cases

---

## Summary

✅ **Agentic Layer NOW SUPPORTS skill modification**

The system now provides:
- **Proposal creation**: Agent or human can suggest changes
- **Query interface**: View all proposals with filtering
- **Approval workflow**: Human review and acceptance
- **Rejection workflow**: Mark proposals as rejected with reasons
- **Auto-learning**: Evaluator proposes improvements after successful simulations
- **Audit trail**: Complete history of changes and who made them

This transforms the skill system from **immutable** to **evolvable**, allowing the agentic layer to continuously improve through experience.
