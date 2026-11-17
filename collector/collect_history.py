#!/usr/bin/env python3
"""Fetch each facility's past inspections and violations, one request per facility.

A facility is fetched only when it has no stored history or its latest
inspection date has changed since the last fetch, so repeat runs are cheap.

    python3 collector/collect_history.py              # food service facilities that need it
    python3 collector/collect_history.py --limit 50   # small test run
    python3 collector/collect_history.py --all-types  # include pools and hotels
"""
import argparse
import json
import re
import sys
import time

import db
from portal import Blocked, fetch_json

LABELS = {
    "points": "points",
    "corrected during inspection?": "corrected",
    "repeat": "repeat",
    "inspector notes": "notes",
}


def _labelled(columns):
    out = {}
    for value in (columns or {}).values():
        if ":" in value:
            label, _, rest = value.partition(":")
            out[label.strip().lower()] = rest.strip()
    return out


def _iso(text):
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", text or "")
    return "%s-%s-%s" % (m.group(3), m.group(1), m.group(2)) if m else None


def _int(text):
    m = re.search(r"-?\d+", text or "")
    return int(m.group()) if m else None


def parse_violation(lines):
    """Portal gives a list like [item, code, 'Points: 9', 'Repeat: No', 'Inspector Notes: ...']."""
    v = {"item": None, "code": None, "points": None, "corrected": None, "repeat": None, "notes": None}
    plain = []
    for line in lines:
        if not isinstance(line, str) or not line.strip():
            continue
        label, sep, rest = line.partition(":")
        key = LABELS.get(label.strip().lower()) if sep else None
        if key:
            v[key] = rest.strip()
        else:
            plain.append(line.strip())
    if plain:
        v["item"] = plain[0]
    if len(plain) > 1:
        v["code"] = plain[1]
    v["points"] = _int(v["points"])
    for flag in ("corrected", "repeat"):
        if v[flag] is not None:
            v[flag] = v[flag].lower().startswith("y")
    return v


def parse_inspection(raw):
    cols = _labelled(raw.get("columns"))
    violations = raw.get("violations") or {}
    ordered = [violations[k] for k in sorted(violations, key=lambda k: int(k) if str(k).isdigit() else 0)]
    return {
        "inspection_id": int(raw["inspectionId"]),
        "date": _iso(cols.get("date")),
        "purpose": cols.get("inspection purpose") or None,
        "score": _int(cols.get("score")),
        "inspector": cols.get("inspector") or None,
        "report_path": (raw.get("printablePath") or "").replace("../", "") or None,
        "violations": json.dumps([parse_violation(v) for v in ordered if isinstance(v, list)]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0, help="fetch at most this many facilities (0 = no limit)")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between requests (minimum 1)")
    ap.add_argument("--all-types", action="store_true")
    args = ap.parse_args()
    delay = max(1.0, args.delay)

    conn = db.connect(args.db)
    where = "(history_date IS NULL OR history_date != COALESCE(last_inspection_date, ''))"
    if not args.all_types:
        where += " AND permit_type = 'Food Service'"
    sql = "SELECT id, name, last_inspection_date FROM facilities WHERE %s ORDER BY last_inspection_date DESC" % where
    if args.limit:
        sql += " LIMIT %d" % args.limit
    todo = conn.execute(sql).fetchall()
    print("%d facilities need history" % len(todo))

    done = stored = 0
    try:
        for fac in todo:
            rows = fetch_json("inspectionsData/%s" % fac["id"])
            if isinstance(rows, list):
                for raw in rows:
                    i = parse_inspection(raw)
                    conn.execute(
                        "INSERT OR REPLACE INTO inspections "
                        "(inspection_id, facility_id, date, purpose, score, inspector, report_path, violations) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (i["inspection_id"], fac["id"], i["date"], i["purpose"], i["score"],
                         i["inspector"], i["report_path"], i["violations"]),
                    )
                    stored += 1
            # '' rather than NULL so a facility with no inspection date is not fetched forever
            conn.execute("UPDATE facilities SET history_date = ? WHERE id = ?",
                         (fac["last_inspection_date"] or "", fac["id"]))
            conn.commit()
            done += 1
            if done % 25 == 0:
                print("  %d/%d facilities, %d inspections" % (done, len(todo), stored))
                sys.stdout.flush()
            time.sleep(delay)
    except Blocked as e:
        print("STOPPED: the portal refused the request (%s). Not retrying." % e, file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("interrupted; run again to continue where it stopped")

    print("done: %d facilities, %d inspections stored" % (done, stored))


if __name__ == "__main__":
    main()
