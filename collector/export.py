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

from portal import SITE

OUT_DIR = os.path.join(db.ROOT, "web", "data")
HISTORY_DIR = os.path.join(OUT_DIR, "history")
BUCKETS = 1000  # history is split into this many files so the page fetches only a small one


def bucket(facility_id):
    """Which history file a facility lives in. The map reads this from the 'b' property."""
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

    features = []
    for r in rows:
        if r["lat"] is None or r["lon"] is None:
            continue
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
            },
        })

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "facilities.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"))

    dates = [r["last_inspection_date"] for r in rows if r["last_inspection_date"]]
    meta = {
        "generated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "facilities": len(rows),
        "mapped": len(features),
        "not_geocoded": len(rows) - len(features),
        "newest_inspection": max(dates) if dates else None,
        "oldest_inspection": min(dates) if dates else None,
    }
    with open(os.path.join(OUT_DIR, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    n_insp, n_files = export_history(conn, {f["properties"]["id"] for f in features})
    print("wrote %d past inspections to %d files in web/data/history/" % (n_insp, n_files))
    print("wrote %d of %d facilities to web/data/facilities.geojson" % (len(features), len(rows)))


if __name__ == "__main__":
    main()
