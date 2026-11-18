#!/usr/bin/env bash
# Email alerts when a collector run fails (production or staging).
# A run fails when the state portal blocks us, a step crashes, or it times out.
# Cloud Monitoring alerting on these metrics is free.
#
#   PROJECT_ID=my-project ALERT_EMAIL=you@example.com bash deploy/alerts-setup.sh
#
# Safe to re-run: it reuses the email channel and replaces the policy.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
ALERT_EMAIL="${ALERT_EMAIL:?Set ALERT_EMAIL to the address that should get alerts}"
API="https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}"
POLICY_NAME="Inspection collector run failed"

TOKEN="$(gcloud auth print-access-token)"
call() {  # method path [json-body]
  local args=(-sS --fail-with-body -X "$1" "${API}$2"
              -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json")
  if [[ $# -ge 3 ]]; then args+=(-d "$3"); fi
  curl "${args[@]}"
}
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "== Email channel for ${ALERT_EMAIL}"
CHANNEL="$(call GET /notificationChannels | python3 -c "
import json, sys
for c in json.load(sys.stdin).get('notificationChannels', []):
    if c.get('type') == 'email' and c.get('labels', {}).get('email_address') == sys.argv[1]:
        print(c['name']); break
" "$ALERT_EMAIL")"
if [[ -z "$CHANNEL" ]]; then
  CHANNEL="$(call POST /notificationChannels "$(python3 -c "
import json, sys
print(json.dumps({'type': 'email', 'displayName': 'Inspection map alerts',
                  'labels': {'email_address': sys.argv[1]}}))" "$ALERT_EMAIL")" | json "d['name']")"
fi
echo "   $CHANNEL"

echo "== Alert policy: ${POLICY_NAME}"
# Remove an earlier copy so re-running doesn't create duplicates.
call GET /alertPolicies | python3 -c "
import json, sys
for p in json.load(sys.stdin).get('alertPolicies', []):
    if p.get('displayName') == sys.argv[1]:
        print(p['name'])
" "$POLICY_NAME" | while read -r old; do
  curl -sS -X DELETE "https://monitoring.googleapis.com/v3/${old}" -H "Authorization: Bearer ${TOKEN}" >/dev/null
done

call POST /alertPolicies "$(python3 -c "
import json, sys
print(json.dumps({
  'displayName': sys.argv[1],
  'documentation': {'mimeType': 'text/markdown', 'content':
    'A collector run failed. See the job\'s executions and logs in Cloud Run '
    '(Jobs > inspection-collector or inspection-collector-staging). If the log says '
    '\"STOPPED: the portal refused the request\", the state portal is blocking us; '
    'do not retry right away.'},
  'combiner': 'OR',
  'conditions': [{
    'displayName': 'A Cloud Run job execution failed',
    'conditionThreshold': {
      'filter': 'resource.type = \"cloud_run_job\" AND '
                'metric.type = \"run.googleapis.com/job/completed_execution_count\" AND '
                'metric.labels.result = \"failed\"',
      'aggregations': [{'alignmentPeriod': '300s', 'perSeriesAligner': 'ALIGN_SUM',
                        'crossSeriesReducer': 'REDUCE_SUM', 'groupByFields': ['resource.labels.job_name']}],
      'comparison': 'COMPARISON_GT', 'thresholdValue': 0, 'duration': '0s',
      'trigger': {'count': 1}}}],
  'alertStrategy': {'autoClose': '86400s'},
  'notificationChannels': [sys.argv[2]],
}))" "$POLICY_NAME" "$CHANNEL")" | json "'   created ' + d['name']"

echo "Done. Google sends a confirmation email to ${ALERT_EMAIL} only if the address is new to the project."
