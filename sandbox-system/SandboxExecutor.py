import sys
import json
from google.cloud import run_v2

def run_isolated_sandbox(project_id, location, job_name, sample_snapshot, flow_name):
    # Initialize the official v2 Cloud Run Jobs Engine Client
    client = run_v2.JobsClient()
    
    # Define the target path inside Google Cloud
    job_path = f"projects/{project_id}/locations/{location}/jobs/{job_name}"
    
    # Bundle our isolated snapshot data into a string format
    snapshot_string = json.dumps(sample_snapshot)
    
    # Override configuration parameters specifically for this run execution
    request = run_v2.RunJobRequest(
        name=job_path,
        overrides={
            "container_overrides": [
                {
                    "env": [
                        {"name": "SNAPSHOT_DATA", "value": snapshot_string},
                        {"name": "PROPOSED_FLOW", "value": flow_name}
                    ]
                }
            ]
        }
    )
    
    print(f"Sending activation request to Cloud Run Job: {job_name}...")
    
    try:
        operation = client.run_job(request=request)
        print("Job triggered successfully. Waiting for execution loop to terminate...")
        
        # Wait for execution to finish
        response = operation.result()
        print("Execution complete. Container spin-down sequence executed cleanly.")
        return response
    except Exception as e:
        print(f"CRITICAL FAULT: Failed to execute or monitor cloud job. Details:\n{e}")
        sys.exit(1)

if __name__ == "__main__":
    # Put your real GCP project ID here
    GCP_PROJECT_ID = "sandbox-ecosystem-id" 
    GCP_LOCATION = "asia-southeast1" 
    GCP_JOB_NAME = "ecosystem-sandbox-job"

    # Mock database data to pass through the isolation shield
    mock_snapshot_data = {
        "companies": [{"id": "C-01", "name": "Nexus AI"}, {"id": "C-02", "name": "Etech Finance"}],
        "mentors": [{"id": "M-99", "name": "Dr. Kuan Studio"}, {"id": "M-88", "name": "Darveen Ventures"}]
    }
    
    run_isolated_sandbox(GCP_PROJECT_ID, GCP_LOCATION, GCP_JOB_NAME, mock_snapshot_data, "semantic_similarity_v2")