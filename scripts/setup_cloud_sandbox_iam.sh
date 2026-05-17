#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${SANDBOX_GCP_REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
JOB="${SANDBOX_JOB_NAME:-ecolink-sandbox-executor}"
BUCKET="${SANDBOX_SOURCE_BUCKET:?Set SANDBOX_SOURCE_BUCKET}"
KEYRING="${CAPABILITY_KMS_KEYRING:-ecolink-sandbox}"
KEY="${CAPABILITY_KMS_KEY:-capability-jwt}"

RUNNER="sandbox-job-runner@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER="sandbox-job-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create sandbox-job-runner \
  --project="${PROJECT_ID}" \
  --display-name="Sandbox Cloud Run job runtime" || true

gcloud iam service-accounts create sandbox-job-invoker \
  --project="${PROJECT_ID}" \
  --display-name="Sandbox Cloud Run job invoker" || true

gcloud run jobs update "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --service-account="${RUNNER}"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${RUNNER}" \
  --role="roles/storage.objectViewer"

gcloud run jobs add-iam-policy-binding "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${INVOKER}" \
  --role="roles/run.invoker"

gcloud run jobs add-iam-policy-binding "${JOB}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${INVOKER}" \
  --role="roles/run.developer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INVOKER}" \
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INVOKER}" \
  --role="roles/run.viewer"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${INVOKER}" \
  --role="roles/storage.objectUser"

gcloud kms keys add-iam-policy-binding "${KEY}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --keyring="${KEYRING}" \
  --member="serviceAccount:${INVOKER}" \
  --role="roles/cloudkms.signerVerifier"

if [[ -n "${LOCAL_DEV_USER_EMAIL:-}" ]]; then
  gcloud iam service-accounts add-iam-policy-binding "${INVOKER}" \
    --project="${PROJECT_ID}" \
    --member="user:${LOCAL_DEV_USER_EMAIL}" \
    --role="roles/iam.serviceAccountTokenCreator"
fi

cat <<EOF
Cloud sandbox IAM configured.

Runner:  ${RUNNER}
Invoker: ${INVOKER}

Set SANDBOX_INVOKER_SERVICE_ACCOUNT=${INVOKER}
Set CAPABILITY_KMS_KEY_VERSION to the active KMS key version for ${KEY}.
EOF
