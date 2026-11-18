#!/usr/bin/env python3
"""Inspections for Gwinnett, Newton and Rockdale counties, entered by hand.

These counties publish inspections through GNR Public Health, which the state
portal does not include and whose site does not allow automated collection.
Records copied from that site by a person are kept in their own table, in their
own database file (data/gnr.db), separate from the production database.

Production never reads this table. Staging builds its database as a copy of
production plus these records (see build_staging.py).

    # add text copied from the GNR results list
    python3 collector/import_gnr.py add pasted.txt

    # how many records are stored, and the date range
    python3 collector/import_gnr.py stats

    # write everything to a CSV (backup), or read one back in
    python3 collector/import_gnr.py export-csv backup.csv
    python3 collector/import_gnr.py import-csv backup.csv
"""
import argparse
import csv
import datetime as dt
import hashlib
import os
import re
import sqlite3

import db

DEFAULT_GNR_DB = os.path.join(db.ROOT, "data", "gnr.db")
FIELDS = ["name", "street", "city", "zip", "permit_type", "date", "purpose", "score"]
ID_PREFIX = "gnr-"
SOURCE_URL = "https://inspections.myhealthdepartment.com/gwinnett-ga"

GNR_SCHEMA = """
CREATE TABLE IF NOT EXISTS gnr_inspections (
    inspection_id  INTEGER PRIMARY KEY,   -- negative, see inspection_id()
    facility_id    TEXT NOT NULL,         -- 'gnr-' + hash of name, street, zip
    name           TEXT NOT NULL,
    street         TEXT,
    city           TEXT,
    zip            TEXT,
    permit_type    TEXT NOT NULL,
    date           TEXT NOT NULL,         -- ISO yyyy-mm-dd
    purpose        TEXT,
    score          INTEGER,
    added_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gnr_fac ON gnr_inspections(facility_id, date);

-- Geocoding results, kept here so staging does not re-geocode every run.
CREATE TABLE IF NOT EXISTS gnr_places (
    facility_id    TEXT PRIMARY KEY,
    geocode_input  TEXT NOT NULL,
    lat            REAL,
    lon            REAL,
    geocode_status TEXT
);
"""

# Multi-word place names, so "... Peachtree Corners, GA" is not split as city "Corners".
CITIES = [
    "Peachtree Corners", "Stone Mountain", "Sugar Hill", "Social Circle", "Berkeley Lake",
    "Lawrenceville", "Norcross", "Loganville", "Suwanee", "Buford", "Braselton", "Snellville",
    "Duluth", "Covington", "Conyers", "Oxford", "Newborn", "Lilburn", "Grayson", "Dacula",
    "Hoschton", "Mansfield", "Porterdale", "Stockbridge", "Lithonia", "Auburn", "Tucker",
]

TYPE_LINE = re.compile(r"^(?P<type>.+?)\s+\d{4}\s*\|\s*(?P<purpose>.+)$")
SCORE_LINE = re.compile(r"^(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})\s*\|\s*(?P<score>\d+)(?:\s+(?P<grade>[ABCU]))?$")
# "GA" is occasionally missing ("770 Ewing Chapel Rd Dacula, 30019").
ADDRESS_LINE = re.compile(r"^(?P<rest>.*?)\s*,\s*(?:GA\s+)?(?P<zip>\d{5})\s*,?\s*$", re.IGNORECASE)


def split_address(line):
    """'12 Example Way SE Lawrenceville, GA 30000' -> ('12 Example Way SE', 'Lawrenceville', '30000')."""
    m = ADDRESS_LINE.match(line.replace("**ACTIVE**", " ").strip())
    if not m:
        return None
    rest = " ".join(m.group("rest").split())
    for city in CITIES:
        if rest.lower().endswith(" " + city.lower()):
            return rest[: -len(city)].strip(" ,"), city, m.group("zip")
    street, _, city = rest.rpartition(" ")
    return street.strip(" ,"), city.title(), m.group("zip")


def parse_paste(text):
    """Records from text copied off the GNR results list. Each record looks like:

        NAME
        STREET CITY, GA ZIP
        [MOST RECENT INSPECTION]
        Food 2025 | Routine
        November 17, 2026 | 98 A
        View Inspection

    Only food service records are returned; pools, hotels and body art are skipped.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and ln.upper() != "MOST RECENT INSPECTION"]
    records, skipped = [], 0
    for i, ln in enumerate(lines):
        if ln != "View Inspection" or i < 4:
            continue
        score_m, type_m = SCORE_LINE.match(lines[i - 1]), TYPE_LINE.match(lines[i - 2])
        addr = split_address(lines[i - 3])
        if not (score_m and type_m and addr):
            skipped += 1
            continue
        if type_m.group("type").lower() != "food":
            continue
        street, city, zip_code = addr
        records.append({
            "name": " ".join(lines[i - 4].split()),
            "street": street,
            "city": city,
            "zip": zip_code,
            "permit_type": "Food Service",
            "date": dt.datetime.strptime(score_m.group("date"), "%B %d, %Y").date().isoformat(),
            "purpose": type_m.group("purpose").strip(),
            "score": int(score_m.group("score")),
        })
    return records, skipped


def _norm(s):
    return " ".join((s or "").upper().split())


def facility_id(rec):
    key = "|".join(_norm(rec[k]) for k in ("name", "street", "zip"))
    return ID_PREFIX + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def inspection_id(fid, rec):
    """Negative, so it never collides with the state portal's positive inspection ids."""
    key = "|".join((fid, rec["date"], _norm(rec["purpose"]), str(rec["score"])))
    return -int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:15], 16)


def _address_key(rec):
    return db.address_key(dict(rec, state="GA"))


# --- the GNR table -----------------------------------------------------------

def connect_gnr(path=DEFAULT_GNR_DB):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(GNR_SCHEMA)
    return conn


def add_records(gconn, records, now):
    """Store records; ones already stored are ignored. Returns how many were new."""
    before = gconn.execute("SELECT COUNT(*) FROM gnr_inspections").fetchone()[0]
    for r in records:
        fid = facility_id(r)
        gconn.execute(
            "INSERT OR IGNORE INTO gnr_inspections (inspection_id, facility_id, name, street, city, zip,"
            " permit_type, date, purpose, score, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (inspection_id(fid, r), fid, r["name"], r["street"], r["city"], r["zip"],
             r["permit_type"], r["date"], r["purpose"], r["score"], now),
        )
    gconn.commit()
    return gconn.execute("SELECT COUNT(*) FROM gnr_inspections").fetchone()[0] - before


def all_records(gconn):
    return [dict(r) for r in gconn.execute("SELECT * FROM gnr_inspections ORDER BY date DESC, name")]


# --- staging ---------------------------------------------------------------

def load_into(conn, gconn, now):
    """Copy the GNR table into a (staging) database's facilities and inspections.

    Coordinates saved by save_geocodes() are restored, so only new addresses
    need geocoding. Returns (facilities, inspections).
    """
    records = all_records(gconn)
    places = {r["facility_id"]: r for r in gconn.execute("SELECT * FROM gnr_places")}
    latest = {}
    for r in records:
        if r["facility_id"] not in latest or r["date"] > latest[r["facility_id"]]["date"]:
            latest[r["facility_id"]] = r
    for fid, r in latest.items():
        conn.execute("DELETE FROM facilities WHERE id = ?", (fid,))
        conn.execute(
            "INSERT INTO facilities (id, name, street, city, state, zip, permit_type, last_score,"
            " last_inspection_date, history_date, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, 'GA', ?, ?, ?, ?, ?, ?, ?)",
            (fid, r["name"], r["street"], r["city"], r["zip"], r["permit_type"], r["score"],
             r["date"], r["date"], r["added_at"], now),
        )
        p = places.get(fid)
        if p and p["geocode_input"] == _address_key(r):
            conn.execute("UPDATE facilities SET lat = ?, lon = ?, geocode_status = ?, geocode_input = ? WHERE id = ?",
                         (p["lat"], p["lon"], p["geocode_status"], p["geocode_input"], fid))
    for r in records:
        conn.execute(
            "INSERT OR REPLACE INTO inspections (inspection_id, facility_id, date, purpose, score)"
            " VALUES (?, ?, ?, ?, ?)",
            (r["inspection_id"], r["facility_id"], r["date"], r["purpose"], r["score"]),
        )
    conn.commit()
    return len(latest), len(records)


def save_geocodes(conn, gconn):
    """Keep the staging database's geocoding results for GNR facilities."""
    n = 0
    for r in conn.execute("SELECT id, geocode_input, lat, lon, geocode_status FROM facilities"
                          " WHERE id LIKE ? AND geocode_status IS NOT NULL", (ID_PREFIX + "%",)):
        gconn.execute("INSERT OR REPLACE INTO gnr_places VALUES (?, ?, ?, ?, ?)",
                      (r["id"], r["geocode_input"], r["lat"], r["lon"], r["geocode_status"]))
        n += 1
    gconn.commit()
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gnr-db", default=DEFAULT_GNR_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add", help="add pasted GNR results").add_argument("paste")
    sub.add_parser("stats", help="show what is stored")
    sub.add_parser("export-csv", help="write all records to a CSV").add_argument("out")
    sub.add_parser("import-csv", help="add records from a CSV").add_argument("csv_path")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gconn = connect_gnr(args.gnr_db)
    if args.cmd == "add":
        with open(args.paste, encoding="utf-8") as f:
            recs, skipped = parse_paste(f.read())
        new = add_records(gconn, recs, now)
        print("parsed %d food service inspections (%d records not understood); %d new"
              % (len(recs), skipped, new))
    elif args.cmd == "import-csv":
        with open(args.csv_path, newline="") as f:
            recs = [dict(r, score=int(r["score"])) for r in csv.DictReader(f)]
        print("%d of %d records were new" % (add_records(gconn, recs, now), len(recs)))
    elif args.cmd == "export-csv":
        recs = all_records(gconn)
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            w.writerows(recs)
        print("wrote %d records to %s" % (len(recs), args.out))
    row = gconn.execute("SELECT COUNT(*), COUNT(DISTINCT facility_id), MIN(date), MAX(date)"
                        " FROM gnr_inspections").fetchone()
    print("GNR table: %d inspections, %d facilities, %s to %s" % tuple(row))


if __name__ == "__main__":
    main()
