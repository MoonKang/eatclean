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
set -euo pipefail

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
python3 collector/export.py

# Publish only after every step succeeded.
gcloud storage rsync web "gs://${SITE_BUCKET}" \
  --recursive \
  --delete-unmatched-destination-objects \
  --cache-control="public, max-age=300"

echo "Site published to https://storage.googleapis.com/${SITE_BUCKET}/index.html"
