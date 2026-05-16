import os
import json
import random

def run_simulation():
    print("--- SANDBOX SYSTEM: INITIALIZING ISOLATION ---")
    
    # Extract data injected by the orchestrator via Environment Variables
    input_data_str = os.getenv("SNAPSHOT_DATA", "{}")
    proposed_flow = os.getenv("PROPOSED_FLOW", "unnamed_flow")
    
    # DEBUG PRINT: Let's see exactly what text arrived inside the container
    print(f"DEBUG RECEIVED DATA: {input_data_str}")
    
    try:
        snapshot = json.loads(input_data_str)
    except json.JSONDecodeError:
        print("ERROR: Injected snapshot data is not valid JSON.")
        return

    companies = snapshot.get("companies", [])
    mentors = snapshot.get("mentors", [])
    
    print(f"STATUS: Loaded {len(companies)} companies and {len(mentors)} mentors.")
    print(f"STATUS: Executing logic pipeline -> {proposed_flow}")

    traces = []
    for company in companies:
        selected_mentor = random.choice(mentors) if mentors else None
        if selected_mentor:
            # Simulate an optimization calculation score
            simulated_score = round(random.uniform(6.5, 9.8), 2)
            traces.append({
                "company_id": company.get("id"),
                "mentor_id": selected_mentor.get("id"),
                "flow_used": proposed_flow,
                "simulated_outcome_score": simulated_score,
                "status": "SIMULATION_SUCCESS"
            })

    # Rule: Cloud Run instances are read-only except for the /tmp directory
    output_path = "/tmp/sandbox_trace.json"
    with open(output_path, "w") as f:
        json.dump(traces, f, indent=4)
        
    print("--- SIMULATION PAYLOAD GENERATED ---")
    print("DATA_STREAM_START")
    print(json.dumps(traces))
    print("DATA_STREAM_END")

if __name__ == "__main__":
    run_simulation()