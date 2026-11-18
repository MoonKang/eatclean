#!/usr/bin/env python3
"""Write the geocoded facilities to web/data/facilities.geojson for the map.

    python3 collector/export.py               # food service only
    python3 collector/export.py --all-types   # include pools and hotels
"""
import argparse
import base64
import datetime as dt
import json
import os
import shutil
import zlib

import db

import osm
from counties import CountyIndex
from import_gnr import ID_PREFIX as GNR_PREFIX, SOURCE_URL as GNR_URL
from portal import SITE

OUT_DIR = os.path.join(db.ROOT, "web", "data")
HISTORY_DIR = os.path.join(OUT_DIR, "history")
BUCKETS = 1000  # history is split into this many files so the page fetches only a small one


def bucket(facility_id):
    """Which history file a facility lives in. The map reads this from the 'b' property."""
    if facility_id.startswith(GNR_PREFIX):
        return zlib.crc32(facility_id.encode("utf-8")) % BUCKETS
    try:
        return int(base64.b64decode(facility_id).decode("ascii")) % BUCKETS
    except (ValueError, UnicodeDecodeError):
        return zlib.crc32(facility_id.encode("utf-8")) % BUCKETS


def export_history(conn, facility_ids):
    """Write web/data/history/<bucket>.json, each {facility_id: [inspections, newest first]}."""
    shards = {}
    count = 0
    rows = conn.execute("SELECT * FROM inspections ORDER BY facility_id, date DESC, inspection_id DESC")
    for r in rows:
        if r["facility_id"] not in facility_ids:
            continue
        entry = {
            "date": r["date"],
            "purpose": r["purpose"],
            "score": r["score"],
            "report": "%s/%s" % (SITE, r["report_path"]) if r["report_path"] else None,
            "violations": [
                {k: v for k, v in viol.items() if v not in (None, "", False)}
                for viol in json.loads(r["violations"])
            ],
        }
        shards.setdefault(bucket(r["facility_id"]), {}).setdefault(r["facility_id"], []).append(entry)
        count += 1

    shutil.rmtree(HISTORY_DIR, ignore_errors=True)
    os.makedirs(HISTORY_DIR)
    for b, data in shards.items():
        with open(os.path.join(HISTORY_DIR, "%d.json" % b), "w") as f:
            json.dump(data, f, separators=(",", ":"))
    return count, len(shards)


def previous_scores(conn):
    """{facility_id: (date, score)} of the second most recent scored inspection.

    Inspections on the same day as the latest (e.g. a same-day follow-up) are skipped.
    """
    out, latest = {}, {}
    rows = conn.execute("SELECT facility_id, date, score FROM inspections"
                        " WHERE score IS NOT NULL AND date IS NOT NULL ORDER BY facility_id, date DESC")
    for fid, date, score in rows:
        if fid not in latest:
            latest[fid] = date
        elif fid not in out and date < latest[fid]:
            out[fid] = (date, score)
    return out


def grade(score):
    """Georgia food service letter grades."""
    if score is None:
        return None
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return "U"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--all-types", action="store_true")
    args = ap.parse_args()

    conn = db.connect(args.db)
    where = "" if args.all_types else "WHERE permit_type = 'Food Service'"
    rows = conn.execute("SELECT * FROM facilities %s ORDER BY name" % where).fetchall()

    # Which county each geocoded facility is in. Coverage counts every permit type:
    # a county with only pools in the portal is still a county the portal covers.
    # See CountyIndex.coverage_geojson for why inspected facilities are counted too.
    index = CountyIndex()
    county_of = {}
    # Hand-entered GNR records are counted separately: they are a sample, so they
    # must not make a county look fully covered.
    counts, inspected, imported = {}, {}, {}
    for r in conn.execute("SELECT id, lat, lon, last_inspection_date FROM facilities"
                          " WHERE lat IS NOT NULL AND lon IS NOT NULL"):
        geoid = index.lookup(r["lon"], r["lat"])
        county_of[r["id"]] = geoid
        if geoid and r["id"].startswith(GNR_PREFIX):
            imported[geoid] = imported.get(geoid, 0) + 1
        elif geoid:
            counts[geoid] = counts.get(geoid, 0) + 1
            if r["last_inspection_date"]:
                inspected[geoid] = inspected.get(geoid, 0) + 1

    coverage = index.coverage_geojson(counts, inspected)
    for c in coverage["features"]:
        if imported.get(c["properties"]["geoid"]):
            c["properties"]["imported"] = imported[c["properties"]["geoid"]]
    uncovered_ids = {c["properties"]["geoid"] for c in coverage["features"] if not c["properties"]["covered"]}

    features = []
    for r in rows:
        if r["lat"] is None or r["lon"] is None:
            continue
        geoid = county_of.get(r["id"])
        if geoid is None:
            continue  # geocoded outside Georgia: almost always a bad match
        if geoid in uncovered_ids and r["last_score"] is None:
            continue  # a permit with no inspection in a county the portal does not inspect

        address = ", ".join(p for p in (r["street"], r["city"]) if p)
        if r["zip"]:
            address += " %s" % r["zip"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(r["lon"], 6), round(r["lat"], 6)]},
            "properties": {
                "id": r["id"],
                "b": bucket(r["id"]),
                "name": r["name"],
                "address": address,
                "score": r["last_score"],
                "grade": grade(r["last_score"]) or "",
                "date": r["last_inspection_date"],
                "type": r["permit_type"],
                "permit": r["permit_number"],
                "county": index.names[geoid],
            },
        })
        if r["id"].startswith(GNR_PREFIX):
            features[-1]["properties"]["src"] = "gnr"

    # Restaurant categories from OpenStreetMap, matched by name and distance.
    cats = osm.categories(conn, [(f["properties"]["id"], f["properties"]["name"],
                                  f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
                                 for f in features])
    # Otherwise a category from the name itself ("TAQUERIA ..." -> Mexican, chains, schools).
    from_name = 0
    for f in features:
        fid = f["properties"]["id"]
        if fid not in cats:
            c = osm.name_category(f["properties"]["name"])
            if c:
                cats[fid] = c
                from_name += 1
        if fid in cats:
            f["properties"]["cat"] = cats[fid]

    # The inspection before the latest one, so the map can show "up from 64".
    prev = previous_scores(conn)
    for f in features:
        p = f["properties"]
        before = prev.get(p["id"])
        if before and p["date"] and before[0] < p["date"]:
            p["prev"], p["prev_date"] = before[1], before[0]

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "facilities.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"))

    with open(os.path.join(OUT_DIR, "counties.geojson"), "w") as f:
        json.dump(coverage, f, separators=(",", ":"))
    uncovered = sorted(c["properties"]["name"] for c in coverage["features"] if not c["properties"]["covered"])

    dates = [r["last_inspection_date"] for r in rows if r["last_inspection_date"]]
    meta = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "facilities": len(rows),
        "mapped": len(features),
        "not_mapped": len(rows) - len(features),
        "newest_inspection": max(dates) if dates else None,
        "oldest_inspection": min(dates) if dates else None,
        "counties_covered": len(coverage["features"]) - len(uncovered),
        "counties_uncovered": uncovered,
        "with_category": len(cats),
        "gnr_mapped": sum(1 for f in features if f["properties"].get("src") == "gnr"),
        "gnr_url": GNR_URL,
    }
    with open(os.path.join(OUT_DIR, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    n_insp, n_files = export_history(conn, {f["properties"]["id"] for f in features})
    print("wrote %d past inspections to %d files in web/data/history/" % (n_insp, n_files))
    print("wrote %d of %d facilities to web/data/facilities.geojson" % (len(features), len(rows)))
    print("categories: %d of %d mapped facilities (%d from OpenStreetMap, %d from the name)"
          % (len(cats), len(features), len(cats) - from_name, from_name))
    print("counties with data: %d of %d; without: %s"
          % (meta["counties_covered"], len(coverage["features"]),
             ", ".join(uncovered) if len(uncovered) <= 12 else "%d counties" % len(uncovered)))


if __name__ == "__main__":
    main()
