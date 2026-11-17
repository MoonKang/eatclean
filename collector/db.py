"""SQLite store for facilities collected from the Georgia DPH inspection portal."""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "inspections.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
    id                    TEXT PRIMARY KEY,   -- portal's facility id (base64 string)
    name                  TEXT NOT NULL,
    street                TEXT,
    city                  TEXT,
    state                 TEXT,
    zip                   TEXT,
    phone                 TEXT,
    permit_type           TEXT,
    permit_number         TEXT,
    last_score            INTEGER,
    last_inspection_date  TEXT,               -- ISO yyyy-mm-dd
    info_phone            TEXT,
    first_seen            TEXT NOT NULL,
    last_seen             TEXT NOT NULL,
    lat                   REAL,
    lon                   REAL,
    geocode_status        TEXT,               -- Match / No_Match / Tie
    geocode_input         TEXT                -- address string that was geocoded
);
CREATE INDEX IF NOT EXISTS idx_fac_date ON facilities(last_inspection_date);
CREATE INDEX IF NOT EXISTS idx_fac_type ON facilities(permit_type);

CREATE TABLE IF NOT EXISTS inspections (
    inspection_id  INTEGER PRIMARY KEY,       -- portal's inspection id
    facility_id    TEXT NOT NULL REFERENCES facilities(id),
    date           TEXT,                      -- ISO yyyy-mm-dd
    purpose        TEXT,
    score          INTEGER,
    inspector      TEXT,
    report_path    TEXT,                      -- portal's path to the printable report
    violations     TEXT NOT NULL DEFAULT '[]' -- JSON list
);
CREATE INDEX IF NOT EXISTS idx_insp_fac ON inspections(facility_id, date);
"""


def connect(path=DEFAULT_DB):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(facilities)")]
    if "history_date" not in cols:
        # last_inspection_date at the time this facility's history was fetched;
        # history is fetched again only when the two differ.
        conn.execute("ALTER TABLE facilities ADD COLUMN history_date TEXT")
    return conn


def address_key(row):
    """The string we geocode; if it changes, the facility must be geocoded again."""
    return "|".join((row["street"] or "", row["city"] or "", row["state"] or "", row["zip"] or ""))
