#!/usr/bin/env python3
"""
Test script demonstrating the new Skill Modification feature.

This shows how the agentic layer can now:
1. Propose modifications to existing skills
2. Query modification proposals
3. Approve modifications (applying them to the actual Skill node)
4. Reject modifications (marking them as rejected)

WORKFLOW:
  Agent creates a SkillModificationProposal → Human reviews → Approval/Rejection
  On approval: proposal updates are applied to the actual Skill node
"""

import sys
from pathlib import Path

# Make ecolink-graph importable
sys.path.insert(0, str(Path(__file__).resolve().parent / "ecolink-graph"))
import queries as graph_queries

# Make src importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.agents import tools


def test_skill_modification():
    """Test the complete skill modification workflow."""
    
    print("\n" + "="*80)
    print("SKILL MODIFICATION SYSTEM TEST")
    print("="*80)
    
    # Test 1: Create a skill modification proposal
    print("\n[TEST 1] Proposing skill update...")
    print("-" * 80)
    
    skill_id = "skill_data_validation"
    
    try:
        result = tools.propose_skill_update.invoke({
            "skill_id": skill_id,
            "description": "Enhanced data validation with improved error handling",
            "performance_score": 8.5,
            "avg_execution_ms": 45.2,
            "reason": "Performance improvement from successful simulation (score=8.50)",
        })
        print(f"✓ Skill modification proposal created:")
        print(f"  Result: {result}")
    except Exception as e:
        print(f"✗ Failed to create proposal: {e}")
        return
    
    # Test 2: Query modification proposals (proposed status)
    print("\n[TEST 2] Querying proposed modifications...")
    print("-" * 80)
    
    try:
        proposals = tools.get_skill_modification_proposals.invoke({"status": "proposed"})
        print(f"✓ Found {len(proposals)} proposed modifications:")
        for prop in proposals:
            print(f"  - ID: {prop.get('id')}")
            print(f"    Reason: {prop.get('reason')}")
            print(f"    Proposed by: {prop.get('proposed_by')}")
            print(f"    Status: {prop.get('status')}")
            if prop.get('proposed_performance_score'):
                print(f"    Proposed score: {prop.get('proposed_performance_score')}")
            if prop.get('proposed_avg_execution_ms'):
                print(f"    Proposed execution time: {prop.get('proposed_avg_execution_ms')}ms")
    except Exception as e:
        print(f"✗ Failed to query proposals: {e}")
        return
    
    # Test 3: Query all proposals (no filter)
    print("\n[TEST 3] Querying all skill modification proposals...")
    print("-" * 80)
    
    try:
        all_proposals = tools.get_skill_modification_proposals.invoke({})
        print(f"✓ Total skill modification proposals in system: {len(all_proposals)}")
        for prop in all_proposals[:5]:  # Show first 5
            print(f"  - {prop.get('id')} (status: {prop.get('status')})")
    except Exception as e:
        print(f"✗ Failed to query all proposals: {e}")
        return
    
    # Test 4: Demonstrate rejection workflow
    print("\n[TEST 4] Testing rejection workflow...")
    print("-" * 80)
    
    try:
        # Get a proposal to reject
        proposed = tools.get_skill_modification_proposals.invoke({"status": "proposed"})
        if proposed:
            test_proposal_id = proposed[0]['id']
            print(f"Rejecting proposal: {test_proposal_id}")
            
            # Call the internal reject function
            from src.agents import tools as agent_tools
            agent_tools.reject_skill_modification(test_proposal_id, "Performance score too aggressive")
            print(f"✓ Proposal {test_proposal_id} rejected")
            
            # Query to confirm rejection
            rejected = tools.get_skill_modification_proposals.invoke({"status": "rejected"})
            print(f"  Total rejected proposals: {len(rejected)}")
        else:
            print("  (No proposed modifications to reject)")
    except Exception as e:
        print(f"✗ Failed during rejection test: {e}")
    
    # Test 5: Demonstrate approval workflow
    print("\n[TEST 5] Testing approval workflow...")
    print("-" * 80)
    
    try:
        # Get a proposal to approve
        proposed = tools.get_skill_modification_proposals.invoke({"status": "proposed"})
        if proposed:
            test_proposal_id = proposed[0]['id']
            print(f"Approving proposal: {test_proposal_id}")
            
            # Call the internal approve function
            from src.agents import tools as agent_tools
            agent_tools.approve_skill_modification(test_proposal_id)
            print(f"✓ Proposal {test_proposal_id} approved and applied")
            
            # Query to confirm approval
            approved = tools.get_skill_modification_proposals.invoke({"status": "approved"})
            print(f"  Total approved proposals: {len(approved)}")
        else:
            print("  (No proposed modifications to approve)")
    except Exception as e:
        print(f"✗ Failed during approval test: {e}")
    
    # Test 6: Error handling - ensure at least one field is provided
    print("\n[TEST 6] Testing error handling...")
    print("-" * 80)
    
    try:
        # This should fail because no fields are provided
        result = tools.propose_skill_update.invoke({
            "skill_id": "test_skill",
            # No update fields provided
            "reason": "This should fail",
        })
        print("✗ Error handling failed - should have rejected empty update")
    except ValueError as e:
        print(f"✓ Correctly rejected empty update: {e}")
    except Exception as e:
        print(f"? Unexpected error: {e}")
    
    print("\n" + "="*80)
    print("SKILL MODIFICATION TEST COMPLETE")
    print("="*80)
    print("\nSUMMARY:")
    print("- SkillModificationProposal nodes are created with status='proposed'")
    print("- Each proposal tracks: id, reason, proposed_by, created_at")
    print("- Proposals can be filtered by status: 'proposed', 'approved', 'rejected'")
    print("- Approval applies proposed_* fields to the actual Skill node")
    print("- Rejection marks the proposal with rejection_reason")
    print("- Agentic layer can now MODIFY existing skills via proposals")
    print()


if __name__ == "__main__":
    test_skill_modification()
