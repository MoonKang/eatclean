#!/usr/bin/env python3
"""Geocode facilities that have no coordinates.

First the free US Census batch geocoder. Addresses it can't place ("No_Match",
or "Tie" when several places fit) then go through a fallback, once each:
  1. the same Census batch again with the address cleaned up (suite numbers
     dropped, "HWY" spelled out),
  2. a Census single-address lookup for ties, taking the first candidate when
     all candidates are within 1 km of each other,
  3. OpenStreetMap's Nominatim, accepting only building-level matches inside
     Georgia; at most one request per second, per its usage policy, and each
     address is asked only once (results are stored).
Addresses that still fail are marked Not_Found and not tried again.

    python3 collector/geocode.py
    python3 collector/geocode.py --retry-failed   # try No_Match / Tie rows again
    python3 collector/geocode.py --no-fallback    # Census batch only
"""
import argparse
import csv
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import db
from counties import CountyIndex

ENDPOINT = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
USER_AGENT = "ga-inspection-map-collector/0.1"
BATCH_SIZE = 1000  # the service allows 10,000; smaller batches fail less and return faster
ONELINE = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "ga-inspection-map/0.1 (restaurant inspection map; one-time address lookups)"
NOMINATIM_MIN_RANK = 28   # 30 = building/house; 26 = only the street (could be a mile off)


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


def clean_street(street):
    """'4993 RUSSELL PKWY STE 300' -> '4993 RUSSELL PKWY'; 'US HWY 17' -> 'US HIGHWAY 17'."""
    s = (street or "").upper()
    s = re.sub(r"\s*(,|\b(STE|SUITE|UNIT|APT|BLDG|BUILDING|SPACE|SPC|RM|ROOM|LOT)\b|#).*$", "", s)
    s = re.sub(r"\bHWY\b", "HIGHWAY", s)
    s = re.sub(r"\bGA-(\d+)", r"STATE HIGHWAY \1", s)
    return " ".join(s.split())


def _get_json(url, params, headers, timeout=30):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _metres(a, b):
    x = (b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(x, b[1] - a[1]) * 111320


def census_oneline(street, city, zip_code):
    """(lat, lon) when Census's candidates agree to within 1 km, else None."""
    out = _get_json(ONELINE, {"address": "%s, %s, GA %s" % (street, city, zip_code),
                              "benchmark": "Public_AR_Current", "format": "json"},
                    {"User-Agent": USER_AGENT})
    pts = [(m["coordinates"]["x"], m["coordinates"]["y"]) for m in out["result"]["addressMatches"]]
    if pts and all(_metres(pts[0], p) <= 1000 for p in pts):
        return pts[0][1], pts[0][0]
    return None


def nominatim(street, city, zip_code, index):
    """(lat, lon) for a building-level match inside Georgia, else None."""
    out = _get_json(NOMINATIM, {"street": street, "city": city, "state": "Georgia", "postalcode": zip_code,
                                "countrycodes": "us", "format": "jsonv2", "limit": 1},
                    {"User-Agent": NOMINATIM_UA})
    if not out or (out[0].get("place_rank") or 0) < NOMINATIM_MIN_RANK:
        return None
    lat, lon = float(out[0]["lat"]), float(out[0]["lon"])
    return (lat, lon) if index.lookup(lon, lat) else None


def fallback(conn, food_only=True, limit=5000):
    """Try addresses Census couldn't place, once each. Returns how many were placed."""
    where = ("lat IS NULL AND street IS NOT NULL AND zip IS NOT NULL"
             " AND geocode_status IN ('No_Match', 'Tie')")
    if food_only:
        where += " AND permit_type = 'Food Service'"
    todo = conn.execute("SELECT * FROM facilities WHERE %s ORDER BY id LIMIT %d" % (where, limit)).fetchall()
    if not todo:
        return 0
    print("fallback: %d addresses Census couldn't place" % len(todo))

    def place(row, lat, lon, status):
        conn.execute("UPDATE facilities SET lat = ?, lon = ?, geocode_status = ? WHERE id = ?",
                     (lat, lon, status, row["id"]))

    # 1. Cleaned-up addresses through the Census batch again.
    rest, placed = [], 0
    for start in range(0, len(todo), BATCH_SIZE):
        chunk = todo[start:start + BATCH_SIZE]
        rows = [(str(i), clean_street(r["street"]), r["city"] or "", "GA", r["zip"]) for i, r in enumerate(chunk)]
        try:
            res = post_batch(rows)
        except (urllib.error.URLError, TimeoutError) as e:
            print("  cleaned batch failed (%s); skipping that step" % e, file=sys.stderr)
            res = {}
        for i, r in enumerate(chunk):
            status, lat, lon = res.get(str(i), ("No_Match", None, None))
            if status == "Match":
                place(r, lat, lon, "Match_cleaned")
                placed += 1
            else:
                rest.append((r, status == "Tie" or r["geocode_status"] == "Tie"))
        conn.commit()
    print("  cleaned addresses: %d placed" % placed)

    # 2. Ties: one address at a time, accepting candidates that agree.
    index = CountyIndex()
    still, n2 = [], 0
    for r, tie in rest:
        got = None
        if tie:
            try:
                got = census_oneline(clean_street(r["street"]), r["city"] or "", r["zip"])
            except Exception as e:  # one failed lookup shouldn't stop the rest
                print("  census lookup failed for %s: %s" % (r["id"], e), file=sys.stderr)
            time.sleep(0.3)
        if got:
            place(r, got[0], got[1], "Tie_resolved")
            n2 += 1
        else:
            still.append(r)
    conn.commit()
    print("  ties resolved: %d" % n2)

    # 3. Nominatim, at most one request per second.
    n3, errors_in_a_row = 0, 0
    for k, r in enumerate(still):
        try:
            got = nominatim(clean_street(r["street"]), r["city"] or "", r["zip"], index)
            errors_in_a_row = 0
        except Exception as e:
            errors_in_a_row += 1
            print("  nominatim failed for %s: %s" % (r["id"], e), file=sys.stderr)
            if errors_in_a_row >= 5:
                print("  nominatim keeps failing; leaving the rest for the next run", file=sys.stderr)
                break
            time.sleep(5)
            continue   # left as No_Match, so it is tried again next run
        if got:
            place(r, got[0], got[1], "Nominatim")
            n3 += 1
        else:
            conn.execute("UPDATE facilities SET geocode_status = 'Not_Found' WHERE id = ?", (r["id"],))
        if k % 50 == 0:
            conn.commit()
            if k:
                print("  nominatim: %d/%d asked, %d placed" % (k, len(still), n3))
        time.sleep(1.1)
    conn.commit()
    print("  nominatim: %d placed" % n3)
    return placed + n2 + n3


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--no-fallback", action="store_true")
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
    if not args.no_fallback:
        print("fallback placed %d more" % fallback(conn))


if __name__ == "__main__":
    main()
