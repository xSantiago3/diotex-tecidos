#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-diotex-tecidos}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-diotex-tecidos-api}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "Building image ${IMAGE}"
gcloud builds submit --project "${PROJECT_ID}" --tag "${IMAGE}" .

echo "Deploying ${SERVICE_NAME} to Cloud Run in ${REGION}"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},DEFAULT_MODEL=gemini-2.5-flash" \
  --port 8080
