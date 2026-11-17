#!/usr/bin/env python3
"""Geocode facilities that have no coordinates, using the free US Census batch geocoder.

    python3 collector/geocode.py
    python3 collector/geocode.py --retry-failed   # try No_Match / Tie rows again
"""
import argparse
import csv
import io
import sys
import time
import urllib.error
import urllib.request
import uuid

import db

ENDPOINT = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
USER_AGENT = "ga-inspection-map-collector/0.1"
BATCH_SIZE = 1000  # the service allows 10,000; smaller batches fail less and return faster


def post_batch(rows, timeout=600):
    """rows: list of (id, street, city, state, zip). Returns {id: (status, lat, lon)}."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    boundary = uuid.uuid4().hex
    parts = [
        '--%s\r\nContent-Disposition: form-data; name="benchmark"\r\n\r\nPublic_AR_Current\r\n' % boundary,
        '--%s\r\nContent-Disposition: form-data; name="addressFile"; filename="addresses.csv"\r\n'
        'Content-Type: text/csv\r\n\r\n%s\r\n' % (boundary, buf.getvalue()),
        '--%s--\r\n' % boundary,
    ]
    req = urllib.request.Request(
        ENDPOINT,
        data="".join(parts).encode("utf-8"),
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary, "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", "replace")

    out = {}
    for rec in csv.reader(io.StringIO(text)):
        if len(rec) < 3:
            continue
        status = rec[2].strip() or "No_Match"
        lat = lon = None
        if status == "Match" and len(rec) > 5 and "," in rec[5]:
            lon_s, lat_s = rec[5].split(",", 1)
            try:
                lon, lat = float(lon_s), float(lat_s)
            except ValueError:
                status = "No_Match"
        out[rec[0].strip()] = (status, lat, lon)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    conn = db.connect(args.db)
    where = "lat IS NULL AND street IS NOT NULL AND zip IS NOT NULL"
    if not args.retry_failed:
        where += " AND geocode_status IS NULL"
    todo = conn.execute("SELECT * FROM facilities WHERE %s ORDER BY id" % where).fetchall()
    print("%d facilities to geocode" % len(todo))

    matched = 0
    for start in range(0, len(todo), BATCH_SIZE):
        chunk = todo[start:start + BATCH_SIZE]
        # The Census service keys results by the first column, so use a simple row number.
        keyed = {str(i): row for i, row in enumerate(chunk)}
        rows = [(k, r["street"], r["city"] or "", r["state"] or "GA", r["zip"]) for k, r in keyed.items()]
        try:
            results = post_batch(rows)
        except (urllib.error.URLError, TimeoutError) as e:
            print("batch starting at %d failed: %s" % (start, e), file=sys.stderr)
            sys.exit(1)
        for k, row in keyed.items():
            status, lat, lon = results.get(k, ("No_Match", None, None))
            conn.execute(
                "UPDATE facilities SET lat = ?, lon = ?, geocode_status = ?, geocode_input = ? WHERE id = ?",
                (lat, lon, status, db.address_key(row), row["id"]),
            )
            matched += status == "Match"
        conn.commit()
        print("  %d/%d done, %d matched so far" % (min(start + BATCH_SIZE, len(todo)), len(todo), matched))
        time.sleep(1)

    print("done: %d of %d matched" % (matched, len(todo)))


if __name__ == "__main__":
    main()
