# Georgia restaurant inspection map

A static map of food service inspection scores, built from the Georgia
Department of Public Health portal (https://ga.healthinspections.us/stateofgeorgia/).

The portal does not cover every county. Gwinnett, Newton and Rockdale publish
through GNR Public Health's own site, which does not allow automated access, so
they are shaded on the public map. Records copied from that site by hand are
kept in a private **staging** environment (see below) and are not published.

## What the map does

- Pins coloured by grade (A ≥ 90, B ≥ 80, C ≥ 70, U below 70); clusters when zoomed out.
- Filters: grade (click a grade to show only it), month of the last inspection,
  category, and a toggle for places not inspected in 18+ months (probably closed).
- Search by name, by category ("korean", "pizza"), or by pasting a Google Maps link.
- Popups: score, change since the previous inspection, category, a Google Maps
  search link, a Share button (`?r=<facility id>` links open that restaurant).
- "More history": every past inspection with its violations (repeats flagged)
  and a link to the official report.
- "Near me": the closest places to your location, sortable by distance or score.
- Phones: the panel folds behind a menu button.

## How the data is built

Everything uses the Python 3 standard library; there is nothing to install.

1. `collector/collect.py` reads the portal's facility list at a low rate into
   `data/inspections.db` (SQLite). It waits at least a second between requests
   and stops immediately on 401, 403 or 429.
2. `collector/collect_history.py` fetches past inspections, only for facilities
   whose latest inspection changed.
3. `collector/geocode.py` adds coordinates with the free US Census batch geocoder.
   Addresses it can't place get one more try each: cleaned up (suite numbers
   dropped), a single-address Census lookup for ambiguous matches, then
   OpenStreetMap's Nominatim (building-level matches only, one request per
   second). Ones that still fail are marked `Not_Found` and not retried.
4. `collector/osm.py` downloads named food places in Georgia from OpenStreetMap
   (at most weekly; several Overpass servers are tried) for restaurant categories.
5. `collector/export.py` writes `web/data/`: `facilities.geojson`, `counties.geojson`,
   `meta.json` and `history/<n>.json`. Categories come from OpenStreetMap (same
   name within 200 m) or from the name itself ("TAQUERIA ..." → Mexican, chains,
   schools); counties are shaded when under half of their listed facilities have
   an inspection.
6. `web/` is a static page (MapLibre + OpenFreeMap tiles) that loads those files.

```bash
python3 collector/collect.py --full   # first run: the whole list (hours at the default 2s delay)
python3 collector/collect.py          # later runs: stops when it reaches stored pages
python3 collector/collect_history.py
python3 collector/geocode.py
python3 collector/osm.py
python3 collector/export.py
python3 serve.py                      # http://localhost:8765
```

Other scripts: `collector/fetch_counties.py` refreshes the county outlines
(only when boundaries change).

## Production (Google Cloud)

- **Site**: a public Cloud Storage bucket. Map data is stored gzip-compressed.
- **Collector**: a Cloud Run Job (`inspection-collector`) run every morning at
  6:00 New York time by Cloud Scheduler. It keeps the SQLite file in a private,
  versioned bucket and republishes the site when it finishes.

Prerequisites: the `gcloud` CLI, `gcloud auth login`, and a project with billing enabled.

```bash
PROJECT_ID=my-project bash deploy/setup.sh                                   # buckets, job, schedule
gcloud run jobs execute inspection-collector --region us-east1               # run now
gcloud run jobs execute inspection-collector --region us-east1 \
  --update-env-vars COLLECT_ARGS=--full                                      # full backfill (this run only)
PROJECT_ID=my-project ALERT_EMAIL=you@example.com bash deploy/alerts-setup.sh   # email when a run fails
```

`setup.sh` is safe to run again and seeds the cloud database from
`data/inspections.db` if the bucket is empty. With no database anywhere yet,
add `ALLOW_EMPTY_START=1` to `--update-env-vars` for the first run only.

## Staging (private)

Staging is production's data plus the hand-entered GNR records, visible only to
signed-in project members.

- `collector/import_gnr.py add paste.txt` parses text copied from the GNR results
  list into its own table in `data/gnr.db` (git-ignored; production never reads it).
- `collector/build_staging.py` builds `data/staging.db` from a copy of the
  production database plus that table, then exports it. Locally:
  `python3 collector/build_staging.py && python3 serve.py`.
- In the cloud, `inspection-collector-staging` (Cloud Run Job, `MODE=staging`)
  does the same every morning at 9:00 from a private bucket and publishes to a
  private site bucket, served by a sign-in-only Cloud Run service.

```bash
PROJECT_ID=my-project IMAGE=<collector image> bash deploy/staging-setup.sh   # one-time setup
PROJECT_ID=my-project bash deploy/push-gnr.sh                                # upload gnr.db and rebuild staging
python3 deploy/staging_proxy.py --project my-project                         # view at http://localhost:8080
```

`staging_proxy.py` forwards your browser to the private site with your gcloud
sign-in (it replaces `gcloud run services proxy`, which some machines block).

## CI/CD (GitHub Actions)

- **CI** (`.github/workflows/ci.yml`) runs on pull requests and non-main branches:
  unit tests on Python 3.11 and 3.14, ShellCheck, a JavaScript syntax check,
  and a build and smoke test of the collector container.
- **Deploy** (`.github/workflows/deploy.yml`) runs on every push to `main`: CI, then
  it builds the collector image, points the production and staging jobs at it, and
  publishes the web app's HTML, JS and CSS to the production site right away.
  New map data comes with the next collector run; staging picks up the new web
  code at its next run.

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

## Data sources and credits

- Inspection records: Georgia Department of Public Health.
- Addresses to coordinates: US Census Bureau geocoder; OpenStreetMap Nominatim.
- County outlines: US Census Bureau TIGERweb.
- Categories and map tiles: © OpenStreetMap contributors (ODbL), OpenFreeMap.
- Page-view counts: Cloudflare Web Analytics (no cookies).
