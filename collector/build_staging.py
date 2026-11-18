#!/usr/bin/env python3
"""Build the staging map: production data plus the hand-entered GNR table.

    python3 collector/build_staging.py

1. copies the production database (data/inspections.db) to data/staging.db
2. adds the GNR records from data/gnr.db
3. geocodes new GNR addresses and saves the results back to data/gnr.db
4. exports the staging database to web/data/

The production database is only read, never changed. To go back to the
production map locally, run:  python3 collector/export.py
"""
import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import sys

import db
import import_gnr

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STAGING_DB = os.path.join(db.ROOT, "data", "staging.db")


def copy_db(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)


def run(script, *args):
    subprocess.run([sys.executable, os.path.join(HERE, script), *args], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prod-db", default=db.DEFAULT_DB)
    ap.add_argument("--gnr-db", default=import_gnr.DEFAULT_GNR_DB)
    ap.add_argument("--staging-db", default=DEFAULT_STAGING_DB)
    args = ap.parse_args()
    if os.path.realpath(args.staging_db) == os.path.realpath(args.prod_db):
        sys.exit("refusing to build staging on top of the production database")

    copy_db(args.prod_db, args.staging_db)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gconn = import_gnr.connect_gnr(args.gnr_db)
    conn = db.connect(args.staging_db)
    n_fac, n_insp = import_gnr.load_into(conn, gconn, now)
    conn.close()
    print("staging: added %d GNR facilities, %d inspections" % (n_fac, n_insp))

    run("geocode.py", "--db", args.staging_db)
    conn = db.connect(args.staging_db)
    print("saved %d GNR geocodes" % import_gnr.save_geocodes(conn, gconn))
    conn.close()
    run("export.py", "--db", args.staging_db)


if __name__ == "__main__":
    main()
