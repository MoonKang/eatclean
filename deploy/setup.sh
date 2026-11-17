#!/usr/bin/env bash
# One-time (and repeatable) GCP setup. Run from the project root after
# `gcloud auth login`, with billing enabled on the project:
#
#   PROJECT_ID=my-project bash deploy/setup.sh
#
# It creates:
#   - a private bucket for the SQLite database (versioned, old versions kept 14 days)
#   - a PUBLIC bucket that serves the website
#   - a service account the collector runs as
#   - a Cloud Run Job that runs the collector
#   - a Cloud Scheduler trigger that runs the job every morning
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID, e.g. PROJECT_ID=my-project bash deploy/setup.sh}"
REGION="${REGION:-us-east1}"
SITE_BUCKET="${SITE_BUCKET:-${PROJECT_ID}-inspection-map}"
DATA_BUCKET="${DATA_BUCKET:-${PROJECT_ID}-inspection-data}"
SCHEDULE="${SCHEDULE:-0 6 * * *}"          # daily at 06:00
TIME_ZONE="${TIME_ZONE:-America/New_York}"

JOB="inspection-collector"
SA_NAME="inspection-collector"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."
gcloud config set project "$PROJECT_ID"

echo "== Enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

echo "== Buckets"
for b in "$DATA_BUCKET" "$SITE_BUCKET"; do
  gcloud storage buckets describe "gs://$b" >/dev/null 2>&1 ||
    gcloud storage buckets create "gs://$b" --location="$REGION" --uniform-bucket-level-access
done
gcloud storage buckets update "gs://$DATA_BUCKET" --versioning --lifecycle-file=deploy/lifecycle.json
gcloud storage buckets update "gs://$SITE_BUCKET" --web-main-page-suffix=index.html
# Makes every object in the site bucket readable by anyone on the internet.
gcloud storage buckets add-iam-policy-binding "gs://$SITE_BUCKET" \
  --member=allUsers --role=roles/storage.objectViewer >/dev/null

echo "== Service account"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$SA_NAME" --display-name="Inspection map collector"
for b in "$DATA_BUCKET" "$SITE_BUCKET"; do
  gcloud storage buckets add-iam-policy-binding "gs://$b" \
    --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
done

echo "== Database"
if gcloud storage ls "gs://$DATA_BUCKET/inspections.db" >/dev/null 2>&1; then
  echo "Bucket already has a database; leaving it alone."
elif [[ -f data/inspections.db ]]; then
  echo "Seeding the bucket with the local database."
  gcloud storage cp data/inspections.db "gs://$DATA_BUCKET/inspections.db"
else
  echo "No local database to seed. Start the first run with ALLOW_EMPTY_START=1 (see README)."
fi

echo "== Cloud Run Job (builds the container with Cloud Build)"
gcloud run jobs deploy "$JOB" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --set-env-vars "DATA_BUCKET=${DATA_BUCKET},SITE_BUCKET=${SITE_BUCKET}" \
  --memory 2Gi \
  --task-timeout 24h \
  --max-retries 0

echo "== Daily schedule"
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
  --member="serviceAccount:$SA" --role=roles/run.invoker >/dev/null
RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "${JOB}-daily" --location "$REGION" >/dev/null 2>&1; then
  VERB=update
else
  VERB=create
fi
gcloud scheduler jobs "$VERB" http "${JOB}-daily" \
  --location "$REGION" \
  --schedule "$SCHEDULE" \
  --time-zone "$TIME_ZONE" \
  --uri "$RUN_URI" \
  --http-method POST \
  --oauth-service-account-email "$SA"

cat <<EOF

Done.
  Run it now:       gcloud run jobs execute $JOB --region $REGION
  Full backfill:    gcloud run jobs execute $JOB --region $REGION --update-env-vars COLLECT_ARGS=--full
  Site (after run): https://storage.googleapis.com/${SITE_BUCKET}/index.html
EOF
