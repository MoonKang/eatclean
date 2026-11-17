#!/usr/bin/env python3
"""Low-rate collector for the Georgia DPH inspection portal.

The portal lists facilities five per page, most recent inspection first.
A normal run walks from page 0 and stops once it reaches pages it has
already stored, so a daily run costs only a handful of requests.

    python3 collector/collect.py                 # incremental
    python3 collector/collect.py --full          # walk every page (backfill)
    python3 collector/collect.py --max-pages 20  # small test run
"""
import argparse
import datetime as dt
import re
import sys
import time

import db
from portal import Blocked, fetch_json

CITY_LINE = re.compile(r"^(.*?),\s*([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?\s*$")


def _labelled(columns):
    """Turn {"1": "Phone Number: 555"} into {"phone number": "555"}."""
    out = {}
    for value in columns.values():
        if ":" in value:
            label, _, rest = value.partition(":")
            out[label.strip().lower()] = rest.strip()
    return out


def parse_facility(raw):
    cols = raw.get("columns") or {}
    labelled = _labelled({k: v for k, v in cols.items() if k != "0"})

    lines = [l.strip() for l in (raw.get("mapAddress") or cols.get("0") or "").splitlines() if l.strip()]
    street = city = state = zip_code = None
    if lines:
        m = CITY_LINE.match(lines[-1])
        if m:
            city, state, zip_code = m.group(1).strip(), m.group(2).upper(), m.group(3)
            street = " ".join(lines[:-1]) or None
        else:
            street = " ".join(lines)

    score = None
    m = re.search(r"-?\d+", labelled.get("last inspection score", ""))
    if m:
        score = int(m.group())

    date = None
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", labelled.get("last inspection date", ""))
    if m:
        date = "%s-%s-%s" % (m.group(3), m.group(1), m.group(2))

    return {
        "id": str(raw["id"]),
        "name": (raw.get("name") or "").strip(),
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "phone": labelled.get("phone number") or None,
        "permit_type": labelled.get("permit type") or None,
        "permit_number": labelled.get("permit number") or None,
        "last_score": score,
        "last_inspection_date": date,
        "info_phone": labelled.get("for more information call") or None,
    }


TRACKED = ("name", "street", "city", "state", "zip", "phone", "permit_type",
           "permit_number", "last_score", "last_inspection_date", "info_phone")


def upsert(conn, fac, now):
    """Insert or update one facility. Returns 'new', 'updated' or 'unchanged'."""
    old = conn.execute("SELECT * FROM facilities WHERE id = ?", (fac["id"],)).fetchone()
    if old is None:
        conn.execute(
            "INSERT INTO facilities (id, %s, first_seen, last_seen) VALUES (?, %s, ?, ?)"
            % (", ".join(TRACKED), ", ".join("?" * len(TRACKED))),
            [fac["id"]] + [fac[k] for k in TRACKED] + [now, now],
        )
        return "new"

    changed = any(old[k] != fac[k] for k in TRACKED)
    moved = db.address_key(old) != db.address_key(fac)
    sets = ", ".join("%s = ?" % k for k in TRACKED) + ", last_seen = ?"
    params = [fac[k] for k in TRACKED] + [now]
    if moved:  # address changed: drop the old coordinates so it is geocoded again
        sets += ", lat = NULL, lon = NULL, geocode_status = NULL, geocode_input = NULL"
    conn.execute("UPDATE facilities SET %s WHERE id = ?" % sets, params + [fac["id"]])
    return "updated" if changed else "unchanged"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--start-page", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=0, help="stop after this many pages (0 = no limit)")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between requests (minimum 1)")
    ap.add_argument("--full", action="store_true", help="do not stop early on already-stored pages")
    ap.add_argument("--stop-after", type=int, default=3,
                    help="incremental mode: stop after this many consecutive unchanged pages")
    args = ap.parse_args()
    delay = max(1.0, args.delay)

    conn = db.connect(args.db)
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    unchanged_run = 0
    page = args.start_page
    pages_done = 0

    try:
        while True:
            if args.max_pages and pages_done >= args.max_pages:
                print("reached --max-pages")
                break
            rows = fetch_json("facilities/%d" % page)
            if not isinstance(rows, list) or not rows:
                print("page %d is empty: end of list" % page)
                break

            now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            results = [upsert(conn, parse_facility(r), now) for r in rows]
            conn.commit()
            for r in results:
                counts[r] += 1
            pages_done += 1

            unchanged_run = unchanged_run + 1 if all(r == "unchanged" for r in results) else 0
            if pages_done % 10 == 0:
                print("page %d  new=%d updated=%d unchanged=%d" % (page, counts["new"], counts["updated"], counts["unchanged"]))
                sys.stdout.flush()
            if not args.full and unchanged_run >= args.stop_after:
                print("caught up at page %d" % page)
                break

            page += 1
            time.sleep(delay)
    except Blocked as e:
        print("STOPPED: the portal refused the request (%s). Not retrying." % e, file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("interrupted at page %d; resume with --start-page %d" % (page, page))

    total = conn.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
    print("done: %d pages, new=%d updated=%d unchanged=%d, %d facilities stored"
          % (pages_done, counts["new"], counts["updated"], counts["unchanged"], total))


if __name__ == "__main__":
    main()
