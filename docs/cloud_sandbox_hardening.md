# Cloud Sandbox Hardening

This repo now treats Cloud Run Jobs as the default execution boundary for UI
sandbox runs. The orchestrator invokes the job with a short-lived RS256
capability JWT, and the sandbox container refuses to execute a flow until that
token is validated.

## Service Accounts

Use two dedicated identities instead of user ADC or the default compute service
account:

```bash
PROJECT_ID="${GOOGLE_CLOUD_PROJECT}"
REGION="${SANDBOX_GCP_REGION:-us-central1}"
JOB="${SANDBOX_JOB_NAME:-ecolink-sandbox-executor}"
BUCKET="${SANDBOX_SOURCE_BUCKET}"

gcloud iam service-accounts create sandbox-job-runner \
  --project="${PROJECT_ID}" \
  --display-name="Sandbox Cloud Run job runtime"

gcloud iam service-accounts create sandbox-job-invoker \
  --project="${PROJECT_ID}" \
  --display-name="Sandbox Cloud Run job invoker"

gcloud run jobs update "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --service-account="sandbox-job-runner@${PROJECT_ID}.iam.gserviceaccount.com"
```

Grant the runner only read access to source bundles:

```bash
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:sandbox-job-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

Grant the invoker only the permissions needed to upload bundles, sign capability
tokens, invoke the job, and read job logs:

```bash
gcloud run jobs add-iam-policy-binding "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud run jobs add-iam-policy-binding "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.developer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.viewer"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectUser"

gcloud kms keys add-iam-policy-binding capability-jwt \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --keyring="ecolink-sandbox" \
  --member="serviceAccount:sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/cloudkms.signerVerifier"
```

For local development, impersonate the invoker rather than invoking as a user:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  "sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="${PROJECT_ID}" \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/iam.serviceAccountTokenCreator"

gcloud auth application-default login \
  --impersonate-service-account="sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
```

## Cloud Run Job Environment

Set these on the Cloud Run Job:

```bash
gcloud run jobs update "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --set-env-vars="CAPABILITY_TOKEN_AUDIENCE=ecolink-sandbox-job" \
  --set-env-vars="CAPABILITY_JWT_PUBLIC_KEY=$(awk 'NF {sub(/\r/, \"\"); printf \"%s\\\\n\",$0;}' capability-jwt-public.pem)"
```

The orchestrator sends per-run overrides:

- `CAPABILITY_TOKEN`
- `SNAPSHOT_DATA`
- `PROPOSED_FLOW`
- `PROPOSED_FLOW_YAML`
- `PROJECT_ID`
- `RUN_ID`
- `SOURCE_BUNDLE_GCS_URI`

## Capability Contract

The sandbox validates:

- token signature and `aud`
- `exp` and `iat`
- `flow_id` against the submitted flow YAML
- `project_id` against `PROJECT_ID`
- `run_id` against `RUN_ID`
- every flow skill against `allowed_skills`
- every connector reference against `allowed_connectors`

Skill aliases are canonicalized before authorization, so
`skill_score_calculator` and `score_calculator` authorize as
`score_by_expertise_depth`.
