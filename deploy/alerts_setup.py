#!/usr/bin/env python3
"""Email alerts when a collector run fails (production or staging).

A run fails when the state portal blocks us, a step crashes, or it times out.
Cloud Monitoring alerting on these metrics is free.

    python3 deploy/alerts_setup.py --project my-project --email you@example.com

Safe to re-run: it reuses the email channel and replaces the policy.
Uses your gcloud sign-in (gcloud auth print-access-token).
"""
import argparse
import json
import subprocess
import urllib.error
import urllib.request

POLICY_NAME = "Inspection collector run failed"
DOC = ("A collector run failed. See the job's executions and logs in Cloud Run "
       "(Jobs > inspection-collector or inspection-collector-staging). If the log says "
       "\"STOPPED: the portal refused the request\", the state portal is blocking us; "
       "don't retry right away.")


def api(token, method, url, body=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raise SystemExit("%s %s failed: %s %s" % (method, url, e.code, e.read().decode()[:500]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--email", required=True)
    args = ap.parse_args()

    token = subprocess.run(["gcloud", "auth", "print-access-token"], check=True,
                           capture_output=True, text=True).stdout.strip()
    base = "https://monitoring.googleapis.com/v3/projects/%s" % args.project

    channels = api(token, "GET", base + "/notificationChannels").get("notificationChannels", [])
    channel = next((c["name"] for c in channels if c.get("type") == "email"
                    and c.get("labels", {}).get("email_address") == args.email), None)
    if not channel:
        channel = api(token, "POST", base + "/notificationChannels", {
            "type": "email", "displayName": "Inspection map alerts",
            "labels": {"email_address": args.email}})["name"]
    print("email channel:", channel)

    for p in api(token, "GET", base + "/alertPolicies").get("alertPolicies", []):
        if p.get("displayName") == POLICY_NAME:
            api(token, "DELETE", "https://monitoring.googleapis.com/v3/" + p["name"])

    policy = api(token, "POST", base + "/alertPolicies", {
        "displayName": POLICY_NAME,
        "documentation": {"mimeType": "text/markdown", "content": DOC},
        "combiner": "OR",
        "conditions": [{
            "displayName": "A Cloud Run job execution failed",
            "conditionThreshold": {
                "filter": ('resource.type = "cloud_run_job" AND '
                           'metric.type = "run.googleapis.com/job/completed_execution_count" AND '
                           'metric.labels.result = "failed"'),
                "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_SUM",
                                  "crossSeriesReducer": "REDUCE_SUM",
                                  "groupByFields": ["resource.labels.job_name"]}],
                "comparison": "COMPARISON_GT", "thresholdValue": 0, "duration": "0s",
                "trigger": {"count": 1},
            },
        }],
        "alertStrategy": {"autoClose": "86400s"},
        "notificationChannels": [channel],
    })
    print("alert policy:", policy["name"])


if __name__ == "__main__":
    main()
