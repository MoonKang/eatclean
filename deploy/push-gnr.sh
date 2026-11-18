#!/usr/bin/env bash
# Upload the local GNR table (data/gnr.db) to the private staging bucket and
# rebuild staging. Production is not touched.
#
#   PROJECT_ID=my-project bash deploy/push-gnr.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-east1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-inspection-staging}"

cd "$(dirname "$0")/.."
[[ -f data/gnr.db ]] || { echo "data/gnr.db not found" >&2; exit 1; }

# Geocode new addresses locally first, so the cloud job has little to do.
python3 collector/build_staging.py >/dev/null
gcloud storage cp data/gnr.db "gs://${STAGING_BUCKET}/gnr.db" --project "$PROJECT_ID"
gcloud run jobs execute inspection-collector-staging --region "$REGION" --project "$PROJECT_ID" --wait
echo "Staging rebuilt. View it: python3 deploy/staging_proxy.py --project $PROJECT_ID"
