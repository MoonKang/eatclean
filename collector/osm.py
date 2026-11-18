#!/usr/bin/env python3
"""Restaurant categories from OpenStreetMap (© OpenStreetMap contributors, ODbL).

Fetches named food places in Georgia (restaurants, fast food, cafes, bars ...)
with their cuisine tags from the public Overpass API, stores them in the
osm_places table, and matches them to inspected facilities by name and distance.

    python3 collector/osm.py                    # refresh if older than 7 days
    python3 collector/osm.py --max-age-days 0   # refresh now

One query per refresh, at most weekly, to keep load on the free service low.
"""
import argparse
import datetime as dt
import difflib
import json
import math
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

import db

OVERPASS = "https://overpass-api.de/api/interpreter"
AMENITIES = "restaurant|fast_food|cafe|bar|pub|ice_cream|food_court|biergarten"
QUERY = """[out:json][timeout:300];
area["ISO3166-2"="US-GA"][admin_level=4]->.ga;
nwr(area.ga)["amenity"~"^(%s)$"]["name"];
out center tags;""" % AMENITIES

SCHEMA = """
CREATE TABLE IF NOT EXISTS osm_places (
    osm_id   TEXT PRIMARY KEY,   -- node/123, way/456
    name     TEXT NOT NULL,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    amenity  TEXT,
    cuisine  TEXT
);
CREATE TABLE IF NOT EXISTS osm_meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Shown when a place has no cuisine tag; a plain "restaurant" says nothing new.
AMENITY_LABELS = {"fast_food": "Fast food", "cafe": "Cafe", "bar": "Bar", "pub": "Pub",
                  "ice_cream": "Ice cream", "food_court": "Food court", "biergarten": "Beer garden"}
MAX_METRES = 200      # geocoded addresses are often off by a building or two
MIN_NAME_SCORE = 1.0

STOP = {"the", "restaurant", "restaurants", "inc", "llc", "and", "of", "at", "bar", "grill",
        "cafe", "co", "company", "kitchen"}


def words(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", " and ")
    s = re.sub(r"['\u2019]", "", s)  # McDonald's -> mcdonalds, not "mcdonald" + "s"
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", s).split() if w not in STOP and len(w) > 1]


def name_score(a, b):
    """1 if the names agree, else 0.

    They agree when every word of the shorter name is in the longer one
    ("PIZZA HUT #4765" / "Pizza Hut"), or when they are spelled almost the
    same ("Teriyaki Madness" / "Teryaki Madness", "Taphouse" / "Tap House").
    Sharing only some words is not enough: "JD's Taco" is not "Taco Bell".
    """
    wa, wb = set(words(a)), set(words(b))
    if not wa or not wb:
        return 0.0
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if short <= long_:
        return 1.0
    ja, jb = "".join(sorted(wa)), "".join(sorted(wb))
    ra = "".join(words(a)); rb = "".join(words(b))
    if max(difflib.SequenceMatcher(None, ra, rb).ratio(), difflib.SequenceMatcher(None, ja, jb).ratio()) >= 0.85:
        return 1.0
    return 0.0


def metres(lat1, lon1, lat2, lon2):
    x = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(x, lat2 - lat1) * 111320


def label(amenity, cuisine):
    """'korean;barbecue' -> 'Korean, Barbecue'; falls back to the kind of place."""
    if cuisine:
        parts = [p.strip().replace("_", " ") for p in re.split(r"[;,]", cuisine) if p.strip()]
        if parts:
            return ", ".join(p[:1].upper() + p[1:] for p in parts[:2])
    return AMENITY_LABELS.get(amenity)


def fetch():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(OVERPASS, data=body,
                                 headers={"User-Agent": "ga-inspection-map/0.1 (weekly; restaurant categories)"})
    with urllib.request.urlopen(req, timeout=400) as resp:
        return json.loads(resp.read().decode("utf-8"))["elements"]


def store(conn, elements, now):
    conn.executescript(SCHEMA)
    rows = []
    for e in elements:
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        tags = e.get("tags", {})
        if lat is None or lon is None or not tags.get("name"):
            continue
        rows.append(("%s/%s" % (e["type"], e["id"]), tags["name"], lat, lon,
                     tags.get("amenity"), tags.get("cuisine")))
    if not rows:
        raise RuntimeError("Overpass returned no places; keeping the previous ones")
    conn.execute("DELETE FROM osm_places")
    conn.executemany("INSERT OR REPLACE INTO osm_places VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.execute("INSERT OR REPLACE INTO osm_meta VALUES ('fetched', ?)", (now,))
    conn.commit()
    return len(rows)


def categories(conn, facilities):
    """{facility_id: label} for facilities (id, name, lat, lon) matched to an OSM place."""
    conn.executescript(SCHEMA)
    grid = {}
    for p in conn.execute("SELECT name, lat, lon, amenity, cuisine FROM osm_places"):
        grid.setdefault((round(p[1], 2), round(p[2], 2)), []).append(p)
    out = {}
    for fid, name, lat, lon in facilities:
        best = None
        cell = (round(lat, 2), round(lon, 2))
        for dy in (-0.01, 0, 0.01):
            for dx in (-0.01, 0, 0.01):
                for p in grid.get((round(cell[0] + dy, 2), round(cell[1] + dx, 2)), ()):
                    d = metres(lat, lon, p[1], p[2])
                    if d > MAX_METRES:
                        continue
                    s = name_score(name, p[0])
                    if s >= MIN_NAME_SCORE and (best is None or (s, -d) > (best[0], -best[1])):
                        best = (s, d, p)
        if best:
            text = label(best[2][3], best[2][4])
            if text:
                out[fid] = text
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--max-age-days", type=float, default=7)
    args = ap.parse_args()
    conn = db.connect(args.db)
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM osm_meta WHERE key = 'fetched'").fetchone()
    now = dt.datetime.now(dt.timezone.utc)
    if row and now - dt.datetime.fromisoformat(row[0].replace("Z", "+00:00")) < dt.timedelta(days=args.max_age_days):
        print("OpenStreetMap places are from %s; not refreshing" % row[0])
        return
    try:
        n = store(conn, fetch(), now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    except Exception as e:  # categories are optional; never fail the run over them
        print("OpenStreetMap refresh failed (%s); keeping the previous places" % e, file=sys.stderr)
        return
    print("stored %d OpenStreetMap food places" % n)


if __name__ == "__main__":
    main()
