#!/usr/bin/env bash
# One collector run inside Cloud Run:
#   download the SQLite file -> collect -> geocode -> export -> publish the site.
#
# Environment:
#   DATA_BUCKET        private bucket holding inspections.db        (required)
#   SITE_BUCKET        public bucket the website is served from     (required)
#   COLLECT_ARGS       extra flags for collect.py, e.g. "--full"    (optional)
#   HISTORY_ARGS       extra flags for collect_history.py           (optional)
#   ALLOW_EMPTY_START  set to 1 to start with no database (first run only)
#
# Staging (MODE=staging) instead copies the production database read-only, adds
# the hand-entered GNR table, and publishes to a private staging bucket:
#   PROD_DATA_BUCKET     production bucket with inspections.db (only read)
#   STAGING_BUCKET       private bucket holding gnr.db
#   STAGING_SITE_BUCKET  private bucket the staging site is served from
set -euo pipefail

if [[ "${MODE:-production}" == "staging" ]]; then
  : "${PROD_DATA_BUCKET:?PROD_DATA_BUCKET is not set}"
  : "${STAGING_BUCKET:?STAGING_BUCKET is not set}"
  : "${STAGING_SITE_BUCKET:?STAGING_SITE_BUCKET is not set}"
  mkdir -p /app/data
  gcloud storage cp "gs://${PROD_DATA_BUCKET}/inspections.db" /app/data/inspections.db
  gcloud storage cp "gs://${STAGING_BUCKET}/gnr.db" /app/data/gnr.db
  python3 collector/build_staging.py
  # Nothing is uploaded back: production's database and gnr.db stay as they were.
  gcloud storage rsync web "gs://${STAGING_SITE_BUCKET}" \
    --recursive \
    --delete-unmatched-destination-objects \
    --cache-control="no-store"
  echo "Staging site published to the private bucket ${STAGING_SITE_BUCKET}."
  exit 0
fi

: "${DATA_BUCKET:?DATA_BUCKET is not set}"
: "${SITE_BUCKET:?SITE_BUCKET is not set}"

DB=/app/data/inspections.db
REMOTE="gs://${DATA_BUCKET}/inspections.db"
mkdir -p /app/data

if gcloud storage ls "$REMOTE" >/dev/null 2>&1; then
  gcloud storage cp "$REMOTE" "$DB"
elif [[ "${ALLOW_EMPTY_START:-0}" == "1" ]]; then
  echo "No database in the bucket; starting empty because ALLOW_EMPTY_START=1."
else
  # Refuse to continue: starting empty here would later overwrite the stored database.
  echo "ERROR: could not find $REMOTE. Set ALLOW_EMPTY_START=1 only if this is the first run." >&2
  exit 1
fi

# Save whatever was collected even if a later step fails, so a long backfill
# is not lost. The bucket keeps old versions, so a bad upload can be undone.
save_db() {
  if [[ -f "$DB" ]]; then
    echo "Uploading database..."
    gcloud storage cp "$DB" "$REMOTE"
  fi
}
trap save_db EXIT

# COLLECT_ARGS / HISTORY_ARGS are intentionally unquoted so several flags can be passed.
# shellcheck disable=SC2086
python3 collector/collect.py ${COLLECT_ARGS:-}
# shellcheck disable=SC2086
python3 collector/collect_history.py ${HISTORY_ARGS:-}
python3 collector/geocode.py
# Restaurant categories; refreshed at most weekly and never fails the run.
python3 collector/osm.py
python3 collector/export.py

# Publish only after every step succeeded.
# Code files by rsync; map data separately below (rsync can't store it compressed).
gcloud storage rsync web "gs://${SITE_BUCKET}" \
  --recursive \
  --exclude="^data/" \
  --delete-unmatched-destination-objects \
  --cache-control="public, max-age=300"
# Map data stored gzip-compressed (Content-Encoding: gzip): facilities.geojson
# drops from ~5.6 MB to ~1 MB. Browsers decompress it transparently.
gcloud storage cp -r web/data "gs://${SITE_BUCKET}/" \
  --gzip-local=json,geojson \
  --cache-control="public, max-age=300"

echo "Site published to https://storage.googleapis.com/${SITE_BUCKET}/index.html"
