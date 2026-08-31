#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID=${1:?usage: setup.sh PROJECT_ID}
REGION=us-central1
gcloud config set project "$PROJECT_ID"

gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  firestore.googleapis.com storage.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com -q

for SA in sa-pipeline sa-dashboard sa-scheduler; do
  gcloud iam service-accounts create "$SA" 2>/dev/null || true
done
P="$PROJECT_ID"; SFX="iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:sa-pipeline@$P.$SFX" --role=roles/aiplatform.user -q
gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:sa-pipeline@$P.$SFX" --role=roles/datastore.user -q
gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:sa-pipeline@$P.$SFX" --role=roles/storage.objectAdmin -q
gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:sa-dashboard@$P.$SFX" --role=roles/datastore.user -q

echo -n "$(openssl rand -hex 24)" | gcloud secrets create admin-token --data-file=- 2>/dev/null || true
gcloud secrets add-iam-policy-binding admin-token --member="serviceAccount:sa-pipeline@$P.$SFX" --role=roles/secretmanager.secretAccessor -q

# Firestore: create the (default) Native-mode database if it doesn't exist yet
# (a fresh project has no database at all -- every query below needs one).
gcloud firestore databases create --database="(default)" --location="$REGION" --type=firestore-native -q 2>/dev/null || true

# Cloud Storage: the pipeline's PDF bucket -- name matches deploy.sh's BUCKET
# env var ($P-carb-pdfs) -- created here since nothing else in infra/ does.
gcloud storage buckets create "gs://$P-carb-pdfs" --location="$REGION" -q 2>/dev/null || true

# Composite indexes: every entry committed in infra/firestore.indexes.json,
# applied one at a time (each create call is independently idempotent via
# || true, matching the rest of this script's style).
gcloud firestore indexes composite create --collection-group=matches \
  --field-config field-path=vehicle_id,order=ascending --field-config field-path=category,order=ascending -q || true
gcloud firestore indexes composite create --collection-group=work_items \
  --field-config field-path=status,order=ascending --field-config field-path=created_at,order=ascending -q || true
gcloud firestore indexes composite create --collection-group=extractions \
  --field-config field-path=eo_number,order=ascending --field-config field-path=created_at,order=descending -q || true
gcloud firestore indexes composite create --collection-group=review_queue \
  --field-config field-path=status,order=ascending --field-config field-path=created_at,order=ascending -q || true
gcloud firestore indexes composite create --collection-group=runs \
  --field-config field-path=trigger,order=ascending --field-config field-path=cost_usd,order=ascending -q || true
gcloud firestore indexes composite create --collection-group=work_items \
  --field-config field-path=eo_number,order=ascending --field-config field-path=created_at,order=descending -q || true

gcloud firestore databases update --database="(default)" --enable-pitr -q || true

gcloud iam service-accounts create sa-dash 2>/dev/null || true
gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:sa-dash@$P.$SFX" --role=roles/datastore.viewer -q

echo "setup done"
