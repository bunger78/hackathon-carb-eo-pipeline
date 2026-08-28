#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID=${1:?usage: deploy.sh PROJECT_ID}
REGION=us-central1
P="$PROJECT_ID"; SFX="iam.gserviceaccount.com"
# Service name changed carb-pipeline -> carb-api during the 2026-08-27 /healthz
# investigation (see ERRORS.md; root cause was the GFE-reserved /healthz path,
# not the name). Keeping carb-api — the scheduler already targets it.
SERVICE=carb-api

gcloud run deploy $SERVICE --source pipeline --region $REGION \
  --service-account "sa-pipeline@$P.$SFX" --no-allow-unauthenticated \
  --memory 1Gi --timeout 3600 --max-instances 2 \
  --set-env-vars "PROJECT_ID=$P,REGION=$REGION,BUCKET=$P-carb-pdfs,MODEL_ID=${MODEL_ID:-gemini-3.7-flash},GENAI_LOCATION=global,PRICE_IN=0.75,PRICE_OUT=3.75" \
  --set-secrets "ADMIN_TOKEN=admin-token:latest"

URL=$(gcloud run services describe $SERVICE --region $REGION --format='value(status.url)')
gcloud run services add-iam-policy-binding $SERVICE --region $REGION \
  --member="serviceAccount:sa-scheduler@$P.$SFX" --role=roles/run.invoker -q

gcloud scheduler jobs create http carb-daily --schedule="0 6 * * *" \
  --time-zone="America/Los_Angeles" --uri="$URL/run" --http-method=POST \
  --attempt-deadline=1800s \
  --oidc-service-account-email="sa-scheduler@$P.$SFX" --location=$REGION 2>/dev/null \
  || gcloud scheduler jobs update http carb-daily --schedule="0 6 * * *" \
     --uri="$URL/run" --location=$REGION \
     --oidc-service-account-email="sa-scheduler@$P.$SFX" --oidc-token-audience="$URL"
# --oidc-token-audience on update is load-bearing: audience is baked at create
# time and a URI-only update leaves it stale -> IAM rejects every trigger.
echo "deployed: $URL — scheduler carb-daily is LIVE (leave it on: run history is the async evidence)"

gcloud run deploy carb-dash --source dashboard --region $REGION \
  --service-account "sa-dash@$P.$SFX" --allow-unauthenticated \
  --memory 512Mi --set-env-vars "PROJECT_ID=$P,PIPELINE_URL=$URL,HOST=0.0.0.0"
gcloud run services add-iam-policy-binding $SERVICE --region $REGION \
  --member="serviceAccount:sa-dash@$P.$SFX" --role=roles/run.invoker -q
