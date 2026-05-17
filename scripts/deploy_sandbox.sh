#!/usr/bin/env bash
# Creates the Cloud Run Job for the EcoLink sandbox.
# Run this ONCE after setup_cloud_sandbox_iam.sh and before the first CI build.
# To update an existing job's image, use gcloud run jobs update or the Cloud Build trigger.
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${SANDBOX_GCP_REGION:-us-central1}"
JOB="${SANDBOX_JOB_NAME:-ecolink-sandbox-executor}"
IMAGE="${SANDBOX_IMAGE:-gcr.io/${PROJECT_ID}/ecolink-sandbox:latest}"
RUNNER="sandbox-job-runner@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Creating Cloud Run Job '${JOB}' in ${REGION} (project=${PROJECT_ID})..."
echo "  Image:  ${IMAGE}"
echo "  Runner: ${RUNNER}"

gcloud run jobs create "${JOB}" \
  --project="${PROJECT_ID}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --service-account="${RUNNER}" \
  --set-env-vars="CAPABILITY_TOKEN_AUDIENCE=ecolink-sandbox-job" \
  --set-secrets="CAPABILITY_JWT_PUBLIC_KEY=capability-jwt-public-pem:latest" \
  --max-retries=0 \
  --task-timeout=300s \
  --memory=512Mi \
  --cpu=1

cat <<EOF

Cloud Run Job '${JOB}' created.

Next steps:
  1. Push an image:  docker build -t ${IMAGE} sandbox-system/ && docker push ${IMAGE}
  2. Or trigger a Cloud Build run that will build, push, and update the job automatically.
  3. Set SANDBOX_JOB_NAME=${JOB} and SANDBOX_GCP_REGION=${REGION} in your orchestrator env.

To update the job image after a new build:
  gcloud run jobs update ${JOB} --image <new-image-uri> --region ${REGION} --project ${PROJECT_ID}
EOF
