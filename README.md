# Georgia restaurant inspection map

A static map of food service inspection scores, built from the Georgia
Department of Public Health portal (https://ga.healthinspections.us/stateofgeorgia/).

The portal does not cover every county. Gwinnett, Newton and Rockdale publish
through a separate site that blocks automated access, so they are not included.

## How it works

1. `collector/collect.py` reads the portal's facility list at a low rate and stores it in `data/inspections.db` (SQLite).
2. `collector/geocode.py` adds coordinates with the free US Census batch geocoder.
3. `collector/export.py` writes `web/data/facilities.geojson`.
4. `web/` is a static page (MapLibre + OpenFreeMap tiles) that loads that file.

Everything uses the Python 3 standard library; there is nothing to install.

## Running it

```bash
python3 collector/collect.py --full   # first run: walk the whole list (hours at the default 2s delay)
python3 collector/collect.py          # later runs: stops when it reaches stored pages
python3 collector/collect_history.py  # past inspections; one request per facility whose latest date changed
python3 collector/geocode.py
python3 collector/export.py           # also writes web/data/history/*.json
python3 serve.py                      # http://localhost:8765
```

The collector waits at least one second between requests and stops
immediately if the portal answers 401, 403 or 429.

## Deploying to Google Cloud

The site is served from a public Cloud Storage bucket. A Cloud Run Job runs the
collector every morning (Cloud Scheduler), keeps the SQLite file in a private
bucket, and republishes the site when it finishes.

Prerequisites: the `gcloud` CLI, `gcloud auth login`, and a project with billing enabled.

```bash
PROJECT_ID=my-project bash deploy/setup.sh
```

The script is safe to run again; it also redeploys the job after code changes.
It seeds the cloud database from `data/inspections.db` if the bucket is empty.

```bash
gcloud run jobs execute inspection-collector --region us-east1                                          # run now
gcloud run jobs execute inspection-collector --region us-east1 --update-env-vars COLLECT_ARGS=--full    # full backfill
```

If there is no database anywhere yet, add `ALLOW_EMPTY_START=1` to
`--update-env-vars` for the first run only.

## CI/CD (GitHub Actions)

- **CI** (`.github/workflows/ci.yml`) runs on pull requests and non-main branches:
  unit tests on Python 3.9 and 3.12, ShellCheck, a JavaScript syntax check,
  and a build and smoke test of the collector container.
- **Deploy** (`.github/workflows/deploy.yml`) runs on every push to `main`: it runs CI,
  then rebuilds the Cloud Run Job and publishes the web app's HTML, JS and CSS.
  It never runs the collector; the daily schedule does that.

Deploys are skipped until the repo is connected to Google Cloud. After `deploy/setup.sh`:

```bash
PROJECT_ID=my-project bash deploy/github-setup.sh
```

This uses Workload Identity Federation, so no Google keys are stored in GitHub.
Only workflow runs from this repo's `main` branch can deploy.

Run the tests locally:

```bash
python3 -m unittest discover -s tests -v
```
