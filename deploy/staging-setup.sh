#!/usr/bin/env bash
# Private staging environment: production data plus the hand-entered GNR table.
# Run after setup.sh (and github-setup.sh, if CI deploys are wanted):
#
#   PROJECT_ID=my-project IMAGE=<collector image> bash deploy/staging-setup.sh
#
# It creates:
#   - a private bucket for gnr.db and a private bucket for the staging site
#     (public access is blocked on both)
#   - a service account for the staging job that can READ the production
#     database but cannot change it or the public site
#   - a Cloud Run Job that builds staging (MODE=staging) and a daily trigger
#   - a Cloud Run service that serves the staging site to signed-in project
#     members only; view it with:
#       gcloud run services proxy inspection-map-staging --region us-east1
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
IMAGE="${IMAGE:?Set IMAGE to a collector image that has staging mode}"
REGION="${REGION:-us-east1}"
PROD_DATA_BUCKET="${PROD_DATA_BUCKET:-${PROJECT_ID}-inspection-data}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-inspection-staging}"
STAGING_SITE_BUCKET="${STAGING_SITE_BUCKET:-${PROJECT_ID}-inspection-staging-site}"
SCHEDULE="${SCHEDULE:-0 9 * * *}"          # daily at 09:00, after the production run
TIME_ZONE="${TIME_ZONE:-America/New_York}"

JOB="inspection-collector-staging"
SERVICE="inspection-map-staging"
SA="inspection-staging@${PROJECT_ID}.iam.gserviceaccount.com"
VIEWER_SA="staging-viewer@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="github-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."
gcloud config set project "$PROJECT_ID"

retry() {
  local n
  for n in 1 2 3 4 5 6; do
    if "$@"; then return 0; fi
    echo "  (attempt $n failed; retrying in $((n * 5))s)" >&2
    sleep $((n * 5))
  done
  "$@"
}

echo "== Private buckets"
for b in "$STAGING_BUCKET" "$STAGING_SITE_BUCKET"; do
  gcloud storage buckets describe "gs://$b" >/dev/null 2>&1 ||
    gcloud storage buckets create "gs://$b" --location="$REGION" --uniform-bucket-level-access
  gcloud storage buckets update "gs://$b" --public-access-prevention >/dev/null
done
# Keep old copies of gnr.db, so an upload by mistake can be undone.
gcloud storage buckets update "gs://$STAGING_BUCKET" --versioning --lifecycle-file=deploy/lifecycle.json >/dev/null

echo "== Service accounts"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create inspection-staging --display-name="Inspection map staging job"
gcloud iam service-accounts describe "$VIEWER_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create staging-viewer --display-name="Inspection map staging site"
# Staging job: read-only on production data; full access to its own buckets only.
retry gcloud storage buckets add-iam-policy-binding "gs://$PROD_DATA_BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.objectViewer >/dev/null
retry gcloud storage buckets add-iam-policy-binding "gs://$STAGING_BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.objectViewer >/dev/null
retry gcloud storage buckets add-iam-policy-binding "gs://$STAGING_SITE_BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
retry gcloud storage buckets add-iam-policy-binding "gs://$STAGING_SITE_BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.legacyBucketReader >/dev/null
# Staging site: read-only on the staging site bucket.
retry gcloud storage buckets add-iam-policy-binding "gs://$STAGING_SITE_BUCKET" \
  --member="serviceAccount:$VIEWER_SA" --role=roles/storage.objectViewer >/dev/null

echo "== GNR table"
if gcloud storage ls "gs://$STAGING_BUCKET/gnr.db" >/dev/null 2>&1; then
  echo "Bucket already has gnr.db; leaving it alone (update it with deploy/push-gnr.sh)."
elif [[ -f data/gnr.db ]]; then
  gcloud storage cp data/gnr.db "gs://$STAGING_BUCKET/gnr.db"
else
  echo "No local data/gnr.db to upload; the staging job will fail until one is uploaded."
fi

echo "== Staging job"
gcloud run jobs deploy "$JOB" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SA" \
  --set-env-vars "MODE=staging,PROD_DATA_BUCKET=${PROD_DATA_BUCKET},STAGING_BUCKET=${STAGING_BUCKET},STAGING_SITE_BUCKET=${STAGING_SITE_BUCKET}" \
  --memory 2Gi \
  --task-timeout 2h \
  --max-retries 0

retry gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
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

echo "== Staging site (sign-in required)"
gcloud run deploy "$SERVICE" \
  --image docker.io/library/nginx:1.27-alpine \
  --region "$REGION" \
  --port 80 \
  --service-account "$VIEWER_SA" \
  --no-allow-unauthenticated \
  --execution-environment gen2 \
  --add-volume "name=site,type=cloud-storage,bucket=${STAGING_SITE_BUCKET},readonly=true" \
  --add-volume-mount "volume=site,mount-path=/usr/share/nginx/html" \
  --min-instances 0 \
  --max-instances 1

echo "== CI deploys"
if gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1; then
  # Lets the GitHub deploy workflow point the staging job at each new image.
  retry gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
    --member="serviceAccount:$DEPLOY_SA" --role=roles/run.developer >/dev/null
  retry gcloud iam service-accounts add-iam-policy-binding "$SA" \
    --member="serviceAccount:$DEPLOY_SA" --role=roles/iam.serviceAccountUser >/dev/null
else
  echo "No $DEPLOY_SA; skipping (run github-setup.sh first for CI deploys)."
fi

cat <<EOF

Done.
  Build staging now:  gcloud run jobs execute $JOB --region $REGION --wait
  View staging:       python3 deploy/staging_proxy.py --project $PROJECT_ID
                      then open http://localhost:8080
  Add pastes:         python3 collector/import_gnr.py add paste.txt
                      bash deploy/push-gnr.sh
EOF
