#!/usr/bin/env bash
# Deploy HireIQ to Cloud Run.
#
# One container: FastAPI serves the API and the SPA at the same origin, so there is
# no second port to misconfigure. Secrets come from Secret Manager, never the image.
set -euo pipefail

PROJECT="${GCP_PROJECT:?set GCP_PROJECT}"
REGION="${GCP_REGION:-asia-south1}"
SERVICE="${SERVICE:-hireiq}"
# Cloud SQL. Unset it to fall back to the container-local SQLite file, which is fine
# for a throwaway demo and loses every row on each deploy.
SQL_INSTANCE="${SQL_INSTANCE:-$PROJECT:$REGION:hireiq-db}"

echo "==> deploying $SERVICE to $PROJECT / $REGION"

# --min-instances 1  keeps the in-memory InterviewRuntime registry warm
# --max-instances 1  DEMO CONSTRAINT: live sessions hold in-process state and the SSE
#                    broadcast is in-process. Multi-instance needs the Redis pub/sub
#                    swap behind RuntimeRegistry/EventBus — the API contract is unchanged.
# --timeout 3600     a panel interview can run 40 minutes over one WebSocket
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --session-affinity \
  --timeout 3600 \
  --memory 1Gi \
  --cpu 1 \
  ${SQL_INSTANCE:+--add-cloudsql-instances "$SQL_INSTANCE"} \
  --set-secrets "JWT_SECRET=hireiq-jwt-secret:latest,\
EMPLOYER_JWT_SECRET=hireiq-employer-jwt-secret:latest,\
GEMINI_API_KEY=hireiq-gemini-key:latest,\
AGORA_APP_CERTIFICATE=hireiq-agora-cert:latest,\
AGORA_CUSTOMER_ID=hireiq-agora-customer-id:latest,\
AGORA_CUSTOMER_SECRET=hireiq-agora-customer-secret:latest,\
DIGEST_TOKEN=hireiq-digest-token:latest,\
DATABASE_URL=hireiq-database-url:latest" \
  --set-env-vars "AGORA_APP_ID=${AGORA_APP_ID:?set AGORA_APP_ID},\
GEMINI_MODEL=gemini-3.8-flash,\
GEMINI_LIVE_MODEL=gemini-2.5-flash-native-audio-preview-12-2025,\
AGORA_TTS_VENDOR=openai,\
VOICE_PROVIDER=auto,\
INTERVIEW_TURN_TTL_DAYS=60"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --project "$PROJECT" --format='value(status.url)')"
echo "==> deployed: $URL"
echo "==> health:"
curl -fsS "$URL/api/health" && echo
