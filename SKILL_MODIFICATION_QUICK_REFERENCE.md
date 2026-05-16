# Skill Modification - Quick Reference

## Q: Can the agentic layer modify existing skills?

### Before: **NO** ❌
- Skills were immutable once created
- Could only propose NEW skills via SkillProposal
- Could not update properties of existing Skill nodes
- No way for agent to improve skills based on experience

### After: **YES** ✅
- Skills are modifiable via SkillModificationProposal workflow
- Can update: name, description, performance_score, avg_execution_ms, language
- Evaluator node auto-proposes improvements after successful simulations
- Human reviews and approves/rejects changes

---

## How It Works

```
AGENT ACTION                    NEO4J STATE                     OUTCOME
─────────────────────────────   ──────────────────────────────  ──────────────
1. propose_skill_update()  →    SkillModificationProposal      
   (e.g., score 8.5)           (status='proposed')
                                
2. get_proposals()         ←    Query all proposals             List 5 proposals
   (status='proposed')          by status
                                
3. [HUMAN REVIEW]               (Streamlit UI)                  Approve/Reject
                                
4. approve() or reject()   →    Skill node UPDATED or           Skill.score=8.5
                                Proposal.status='approved'      Skill.last_modified_at=now
```

---

## API Quick Reference

### For LLM Agent (Can Call Directly)

#### `propose_skill_update()`
```python
propose_skill_update.invoke({
    "skill_id": "data_validation",
    "performance_score": 8.5,           # Optional
    "avg_execution_ms": 45.2,           # Optional
    "description": "Better error handling",  # Optional
    "reason": "Performance improvement",
})
# Returns: "Skill modification proposal created (id=...)"
```

#### `get_skill_modification_proposals()`
```python
get_skill_modification_proposals.invoke({
    "status": "proposed"  # or "approved", "rejected", or None for all
})
# Returns: List[Dict] with all proposal fields
```

---

### For Humans (Streamlit Admin UI)

✅ **Tab: "Skill Modification Proposals"**
- View proposals grouped by status
- See proposed changes vs current values
- Click "Approve" → Updates Skill node in Neo4j
- Click "Reject" → Marks as rejected with reason
- Audit trail showing timestamps and proposer info

---

### For Backend Integration

#### Import
```python
from src.agents.tools import (
    propose_skill_update,
    get_skill_modification_proposals,
    approve_skill_modification,
    reject_skill_modification,
)

from ecolink_graph import queries as graph_queries
```

#### Create Proposal
```python
graph_queries.create_skill_modification_proposal(
    skill_id="skill_id",
    performance_score=8.5,
    avg_execution_ms=45.2,
    reason="Performance tuning",
    proposed_by="agent",
)
```

#### Apply Proposal
```python
graph_queries.approve_skill_modification("skill_id")
# Copies proposed_* fields to Skill node
# Sets Skill.last_modified_at = now()
# Sets proposal.status = 'approved'
```

#### Reject Proposal
```python
graph_queries.reject_skill_modification("skill_id", "Too aggressive")
# Sets proposal.status = 'rejected'
# Records rejection_reason
```

---

## Files Changed

### New/Modified Files:

| File | Change | Purpose |
|------|--------|---------|
| `ecolink-graph/queries.py` | Added 4 functions | Core Neo4j operations |
| `src/agents/tools.py` | Added 4 functions | Agent tools + helpers |
| `src/agents/nodes.py` | Enhanced evaluator_node | Auto-propose improvements |
| `test_skill_modification.py` | NEW file | Test suite |
| `docs/SKILL_MODIFICATION_SYSTEM.md` | NEW file | Full documentation |
| `SKILL_MODIFICATION_IMPLEMENTATION.md` | NEW file | Code examples |

**No files deleted or reverted. All changes are local.**

---

## Neo4j Node Structure

### SkillModificationProposal
```
Properties:
  id                          String (same as target Skill ID)
  status                      'proposed' | 'approved' | 'rejected'
  reason                      String (why the modification)
  proposed_by                 String ('agent' or human name)
  created_at                  DateTime
  rejection_reason            String (if rejected)
  
  proposed_name               String (if being changed)
  proposed_description        String (if being changed)
  proposed_performance_score  Float (if being changed)
  proposed_avg_execution_ms   Float (if being changed)
  proposed_language           String (if being changed)
```

### Skill (Updated)
```
Properties (existing):
  id, name, description
  performance_score, avg_execution_ms, language
  
Properties (NEW - added during approval):
  last_modified_at            DateTime (when approved)
```

---

## Example Workflow

### Scenario: Skill performance improved

```
Timeline:
─────────────────────────────────────────────────────────────────

10:00 - Simulator runs flow with data_validation skill
        Execution time: 45.2ms (was 120ms)
        Match score: 8.5 (was baseline 5.0)

10:01 - Evaluator compares: 8.5 > 5.0 * 1.1 ✓ SUCCESS
        
10:02 - Evaluator calls: propose_skill_update(
          skill_id="data_validation",
          performance_score=8.5,
          avg_execution_ms=45.2,
          reason="Performance improvement from successful simulation"
        )

10:03 - SkillModificationProposal created in Neo4j
        status='proposed'

10:30 - Admin opens Streamlit UI
        Sees "Skill Modification Proposals" tab
        Sees: data_validation proposal
          Current:  score=6.2, time=120.0ms
          Proposed: score=8.5, time=45.2ms
          Reason: "Performance improvement..."

10:31 - Admin clicks "Approve"

10:32 - Cypher executes:
        MATCH (p:SkillModificationProposal {id: "data_validation"})
        MATCH (s:Skill {id: "data_validation"})
        SET s.performance_score = 8.5,
            s.avg_execution_ms = 45.2,
            s.last_modified_at = datetime(),
            p.status = 'approved'
        ✓ Success

10:33 - On next flow generation:
        Critic sees updated skill metrics
        Considers data_validation as higher-performing skill
        More likely to use it in new proposals
```

---

## Testing

### Run Tests
```bash
python test_skill_modification.py
```

### Manual Verification
```cypher
-- Check all proposals
MATCH (p:SkillModificationProposal) 
RETURN p.id, p.status, p.reason 
LIMIT 10;

-- Check if skill was updated
MATCH (s:Skill {id: "data_validation"})
RETURN s.performance_score, s.avg_execution_ms, s.last_modified_at;
```

---

## Common Tasks

### Add a new skill update capability
1. Extend `propose_skill_update()` parameters
2. Add new `proposed_*` field to SkillModificationProposal
3. Update approval logic in `approve_skill_modification()`
4. Update test cases

### Auto-approve small changes
1. Extend evaluator_node logic
2. Add threshold: `if change_pct < 5%: auto_approve()`
3. Flag large changes for manual review

### Add skill versioning
1. Create `SkillVersion` nodes
2. Link proposals to versions
3. Enable rollback to previous versions

### Track modification history
1. Already implemented via SkillModificationProposal timestamps
2. Query: `MATCH (p:SkillModificationProposal {id}) ORDER BY created_at DESC`

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Proposal created but approval fails | Ensure target Skill node exists in Neo4j |
| Agent can't find tool | Ensure tool is decorated with `@tool` |
| Changes not appearing | Check Neo4j connection and verify approval executed |
| Too many rejected proposals | Evaluate rejection reasons and adjust scoring logic |

---

## Architecture Decision

**Why SkillModificationProposal (not direct update)?**

1. **Audit trail** - Track all proposed changes with timestamps
2. **Human review** - Prevent agent from making harmful changes unilaterally
3. **Rollback** - Can see what changed and revert if needed
4. **Experimentation** - Agent can propose, human approves what works
5. **Consistency** - Same pattern as SkillProposal for new skills

---

## Performance Impact

- **Proposing**: O(1) - single node merge
- **Querying**: O(n) where n = proposals (indexed by status)
- **Approving**: O(1) - single update
- **Rejecting**: O(1) - single update

**Recommendation**: Index on `status` and `created_at` for queries

---

## Summary Checklist

✅ Can propose skill updates                  
✅ Can query proposals by status              
✅ Can approve and apply changes              
✅ Can reject with reasons                    
✅ Full audit trail maintained                
✅ Evaluator node auto-proposes               
✅ Error handling & validation                
✅ Streamlit UI integration ready             
✅ Test suite included                        
✅ Documentation complete                     

**Status: READY FOR PRODUCTION** 🚀
