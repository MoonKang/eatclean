"""Unit tests for the collector's parsing, storage and export logic.

Run from the project root:  python3 -m unittest discover -s tests -v
The sample records below are made up but follow the portal's real response format.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collector"))

import collect  # noqa: E402
import collect_history  # noqa: E402
import db  # noqa: E402
import export  # noqa: E402

FACILITY = {
    "mapAddress": "123 MAIN ST STE 4 \r\nSPRINGFIELD, GA 30000",
    "columns": {
        "0": "123 MAIN ST STE 4 \r\nSPRINGFIELD, GA 30000",
        "1": "Phone Number: (555) 555-0100",
        "2": "Permit Type: Food Service",
        "3": "Permit Number: FS-000-0001",
        "4": "Last Inspection Score: 87",
        "5": "Last Inspection Date: 11-16-2026",
        "6": "For More Information Call: (555) 555-0199",
    },
    "id": "MTIzNA==",
    "name": " Test Diner ",
}

INSPECTION = {
    "printablePath": "../../_templates/1/food/_report_full.cfm?fsimID=99&dsn=X",
    "inspectionId": 99,
    "facilityId": "MTIzNA==",
    "columns": {
        "0": "Date: 11-16-2026",
        "1": "Inspection Purpose: Routine",
        "2": "Score: 87",
        "3": "Inspector: Pat Example",
    },
    "violations": {
        "1": ["11D - thermometers", "511-6-1.05 - tmd", "Points: 3",
              "Corrected during inspection?: Yes", "Repeat: No", "Inspector Notes: No thermometer."],
        "0": ["6-1A - cold holding", "511-6-1.04 - cold holding", "Points: 9",
              "Corrected during inspection?: No", "Repeat: Yes", "Inspector Notes: Eggs at 52 F."],
        "2": ["17C - facilities", None, "", "Points: 1"],
    },
}


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute("ALTER TABLE facilities ADD COLUMN history_date TEXT")
    return conn


class ParseFacility(unittest.TestCase):
    def test_fields(self):
        f = collect.parse_facility(FACILITY)
        self.assertEqual(f["id"], "MTIzNA==")
        self.assertEqual(f["name"], "Test Diner")
        self.assertEqual((f["street"], f["city"], f["state"], f["zip"]),
                         ("123 MAIN ST STE 4", "SPRINGFIELD", "GA", "30000"))
        self.assertEqual(f["permit_type"], "Food Service")
        self.assertEqual(f["last_score"], 87)
        self.assertEqual(f["last_inspection_date"], "2026-11-16")
        self.assertEqual(f["phone"], "(555) 555-0100")

    def test_missing_values(self):
        raw = dict(FACILITY, mapAddress="", columns={"0": "", "1": "Phone Number: ",
                                                    "4": "Last Inspection Score: ",
                                                    "5": "Last Inspection Date: "})
        f = collect.parse_facility(raw)
        self.assertIsNone(f["street"])
        self.assertIsNone(f["phone"])
        self.assertIsNone(f["last_score"])
        self.assertIsNone(f["last_inspection_date"])

    def test_address_without_city_line(self):
        f = collect.parse_facility(dict(FACILITY, mapAddress="SOMEWHERE RURAL"))
        self.assertEqual(f["street"], "SOMEWHERE RURAL")
        self.assertIsNone(f["zip"])


class Upsert(unittest.TestCase):
    def test_new_unchanged_updated(self):
        conn = memory_db()
        fac = collect.parse_facility(FACILITY)
        self.assertEqual(collect.upsert(conn, fac, "t1"), "new")
        self.assertEqual(collect.upsert(conn, fac, "t2"), "unchanged")
        self.assertEqual(collect.upsert(conn, dict(fac, last_score=95), "t3"), "updated")
        row = conn.execute("SELECT last_score, first_seen, last_seen FROM facilities").fetchone()
        self.assertEqual(tuple(row), (95, "t1", "t3"))

    def test_address_change_clears_coordinates(self):
        conn = memory_db()
        fac = collect.parse_facility(FACILITY)
        collect.upsert(conn, fac, "t1")
        conn.execute("UPDATE facilities SET lat = 33.0, lon = -84.0, geocode_status = 'Match'")
        collect.upsert(conn, dict(fac, last_score=90), "t2")
        self.assertEqual(conn.execute("SELECT lat FROM facilities").fetchone()[0], 33.0)
        collect.upsert(conn, dict(fac, street="9 OTHER RD"), "t3")
        row = conn.execute("SELECT lat, lon, geocode_status FROM facilities").fetchone()
        self.assertEqual(tuple(row), (None, None, None))


class ParseInspection(unittest.TestCase):
    def test_fields_and_violation_order(self):
        i = collect_history.parse_inspection(INSPECTION)
        self.assertEqual(i["inspection_id"], 99)
        self.assertEqual(i["date"], "2026-11-16")
        self.assertEqual(i["purpose"], "Routine")
        self.assertEqual(i["score"], 87)
        self.assertEqual(i["report_path"], "_templates/1/food/_report_full.cfm?fsimID=99&dsn=X")
        v = json.loads(i["violations"])
        self.assertEqual([x["item"] for x in v], ["6-1A - cold holding", "11D - thermometers", "17C - facilities"])
        self.assertEqual(v[0]["points"], 9)
        self.assertIs(v[0]["repeat"], True)
        self.assertIs(v[0]["corrected"], False)
        self.assertEqual(v[0]["notes"], "Eggs at 52 F.")

    def test_null_and_blank_lines_are_skipped(self):
        v = json.loads(collect_history.parse_inspection(INSPECTION)["violations"])[2]
        self.assertEqual(v["item"], "17C - facilities")
        self.assertIsNone(v["code"])
        self.assertEqual(v["points"], 1)

    def test_no_violations(self):
        i = collect_history.parse_inspection(dict(INSPECTION, violations={}))
        self.assertEqual(i["violations"], "[]")


class Export(unittest.TestCase):
    def test_grade_boundaries(self):
        cases = {100: "A", 90: "A", 89: "B", 80: "B", 79: "C", 70: "C", 69: "U", 0: "U", None: None}
        for score, expected in cases.items():
            self.assertEqual(export.grade(score), expected, score)

    def test_bucket_is_stable_and_in_range(self):
        self.assertEqual(export.bucket("MTIzNA=="), 1234 % export.BUCKETS)
        b = export.bucket("not base64 !")
        self.assertTrue(0 <= b < export.BUCKETS)
        self.assertEqual(b, export.bucket("not base64 !"))

    def test_history_files(self):
        conn = memory_db()
        i = collect_history.parse_inspection(INSPECTION)
        conn.execute(
            "INSERT INTO inspections (inspection_id, facility_id, date, purpose, score, inspector, report_path, violations)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (i["inspection_id"], "MTIzNA==", i["date"], i["purpose"], i["score"], i["inspector"],
             i["report_path"], i["violations"]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            old = export.HISTORY_DIR
            export.HISTORY_DIR = os.path.join(tmp, "history")
            try:
                count, files = export.export_history(conn, {"MTIzNA==", "other"})
                with open(os.path.join(export.HISTORY_DIR, "%d.json" % export.bucket("MTIzNA=="))) as f:
                    data = json.load(f)
            finally:
                export.HISTORY_DIR = old
        self.assertEqual((count, files), (1, 1))
        entry = data["MTIzNA=="][0]
        self.assertTrue(entry["report"].startswith("https://ga.healthinspections.us/_templates/"))
        self.assertNotIn("inspector", entry)  # inspector names are not published
        self.assertNotIn("repeat", entry["violations"][1])  # false flags are dropped to save space


if __name__ == "__main__":
    unittest.main()
