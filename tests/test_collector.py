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
import counties  # noqa: E402
import db  # noqa: E402
import export  # noqa: E402
import geocode  # noqa: E402
import import_gnr  # noqa: E402
import osm  # noqa: E402

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


class Counties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = counties.CountyIndex()

    def test_all_georgia_counties_loaded(self):
        self.assertEqual(len(self.index.names), 159)

    def test_lookup(self):
        self.assertEqual(self.index.names[self.index.lookup(-84.3880, 33.7490)], "Fulton")      # downtown Atlanta
        self.assertEqual(self.index.names[self.index.lookup(-83.9880, 33.9562)], "Gwinnett")    # Lawrenceville
        self.assertEqual(self.index.names[self.index.lookup(-81.0998, 32.0809)], "Chatham")     # Savannah
        self.assertIsNone(self.index.lookup(-86.8025, 33.5207))                                 # Birmingham, AL

    def test_ring_with_hole(self):
        square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
        self.assertTrue(counties._in_ring(2, 2, square))
        self.assertTrue(counties._in_ring(5, 5, hole))
        self.assertFalse(counties._in_ring(11, 5, square))

    def test_coverage_flags(self):
        # Fulton: most listed facilities inspected. Gwinnett: a few stray permits, no scores.
        cov = self.index.coverage_geojson({"13121": 3, "13135": 80}, {"13121": 2, "13135": 5})
        props = {f["properties"]["name"]: f["properties"] for f in cov["features"]}
        self.assertTrue(props["Fulton"]["covered"])
        self.assertEqual((props["Fulton"]["facilities"], props["Fulton"]["inspected"]), (3, 2))
        self.assertFalse(props["Gwinnett"]["covered"])
        self.assertFalse(props["Chatham"]["covered"])  # nothing listed at all
        self.assertEqual(props["Gwinnett"]["source"], "GNR Public Health")
        self.assertNotIn("source", props["Fulton"])


GNR_PASTE = """MOST RECENT INSPECTION
TEST DINER
12 Example Way SE Peachtree Corners, GA 30092
MOST RECENT INSPECTION
Food 2025 | Routine
November 17, 2026 | 98 A
View Inspection
TEST POOL
1 Pool Rd Duluth, GA 30096
Public Swimming Pool Inspection Report 2023 | Routine
November 12, 2026 | 0
View Inspection
Test Cafe
5 Main St **ACTIVE** CONYERS, GA 30013 ,
Food 2025 | Follow-up
November 9, 2026 | 64 U
View Inspection
"""


class GnrImport(unittest.TestCase):
    def test_parse(self):
        recs, skipped = import_gnr.parse_paste(GNR_PASTE)
        self.assertEqual(skipped, 0)
        self.assertEqual([r["name"] for r in recs], ["TEST DINER", "Test Cafe"])  # pool skipped
        self.assertEqual((recs[0]["street"], recs[0]["city"], recs[0]["zip"]),
                         ("12 Example Way SE", "Peachtree Corners", "30092"))
        self.assertEqual((recs[0]["date"], recs[0]["score"]), ("2026-11-17", 98))
        self.assertEqual((recs[1]["street"], recs[1]["city"], recs[1]["purpose"]), ("5 Main St", "Conyers", "Follow-up"))

    def test_table_and_staging_load(self):
        recs, _ = import_gnr.parse_paste(GNR_PASTE)
        older = dict(recs[0], date="2026-05-01", score=85)
        g = sqlite3.connect(":memory:")
        g.row_factory = sqlite3.Row
        g.executescript(import_gnr.GNR_SCHEMA)
        self.assertEqual(import_gnr.add_records(g, recs + [older], "t0"), 3)
        self.assertEqual(import_gnr.add_records(g, recs, "t0"), 0)  # pasting twice adds nothing

        conn = memory_db()
        self.assertEqual(import_gnr.load_into(conn, g, "t1"), (2, 3))
        fid = import_gnr.facility_id(recs[0])
        row = conn.execute("SELECT last_score, last_inspection_date, history_date FROM facilities WHERE id = ?",
                           (fid,)).fetchone()
        self.assertEqual(tuple(row), (98, "2026-11-17", "2026-11-17"))  # newest wins; no portal refetch
        self.assertTrue(all(r[0] < 0 for r in conn.execute("SELECT inspection_id FROM inspections")))
        self.assertTrue(0 <= export.bucket(fid) < export.BUCKETS)

        # Geocodes survive into the next staging build, and loading twice does not duplicate.
        conn.execute("UPDATE facilities SET lat = 34.0, lon = -84.0, geocode_status = 'Match',"
                     " geocode_input = ? WHERE id = ?", (db.address_key(conn.execute(
                         "SELECT * FROM facilities WHERE id = ?", (fid,)).fetchone()), fid))
        self.assertEqual(import_gnr.save_geocodes(conn, g), 1)
        fresh = memory_db()
        import_gnr.load_into(fresh, g, "t2")
        import_gnr.load_into(fresh, g, "t3")
        self.assertEqual(fresh.execute("SELECT lat FROM facilities WHERE id = ?", (fid,)).fetchone()[0], 34.0)
        self.assertEqual(fresh.execute("SELECT COUNT(*) FROM facilities").fetchone()[0], 2)


class OsmCategories(unittest.TestCase):
    def test_names(self):
        self.assertEqual(osm.name_score("PIZZA HUT #4765", "Pizza Hut"), 1)
        self.assertEqual(osm.name_score("Teriyaki Madness", "Teryaki Madness"), 1)
        self.assertEqual(osm.name_score("Rusty Rudder's Taphouse", "Rusty Rudders Tap House"), 1)
        self.assertEqual(osm.name_score("TEQUILA'S BAR & GRILL", "McDonald's"), 0)  # not via a stray "s"
        self.assertEqual(osm.name_score("JD'S TACO AND HIBACHI", "Taco Bell"), 0)

    def test_labels(self):
        self.assertEqual(osm.label("restaurant", "korean;barbecue"), "Korean, Barbecue")
        self.assertEqual(osm.label("fast_food", None), "Fast food")
        self.assertIsNone(osm.label("restaurant", None))

    def test_name_categories(self):
        cases = {"TAQUERIA LOS HERMANOS INC": "Mexican", "PHO 24": "Vietnamese", "Phoenix Roasters": None,
                 "JENKINS ELEMENTARY SCHOOL": "School", "WAFFLE HOUSE #1915": "Breakfast",
                 "Chick-fil-A FSU #4423": "Chicken", "Brick Oven Pizzeria": "Pizza",
                 "College Park Grill": None, "Thai Palace": "Thai", "Thaitanic": None}
        for name, want in cases.items():
            self.assertEqual(osm.name_category(name), want, name)

    def test_match_by_name_and_distance(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(osm.SCHEMA)
        conn.executemany("INSERT INTO osm_places VALUES (?, ?, ?, ?, ?, ?)", [
            ("node/1", "Pizza Hut", 34.0001, -84.0, "restaurant", "pizza"),
            ("node/2", "Taco Bell", 34.0, -84.0001, "fast_food", "mexican"),
            ("node/3", "Pizza Hut", 34.01, -84.0, "restaurant", "pizza"),  # ~1.1 km away
        ])
        cats = osm.categories(conn, [("a", "PIZZA HUT #12", 34.0, -84.0),
                                     ("b", "JD'S TACO", 34.0, -84.0),
                                     ("c", "PIZZA HUT", 34.02, -84.0)])
        self.assertEqual(cats, {"a": "Pizza"})


class GeocodeCleanup(unittest.TestCase):
    def test_clean_street(self):
        self.assertEqual(geocode.clean_street("4993 RUSSELL PKWY STE 300"), "4993 RUSSELL PKWY")
        self.assertEqual(geocode.clean_street("1984 HOSEA WILLIAMS DR STE A,B"), "1984 HOSEA WILLIAMS DR")
        self.assertEqual(geocode.clean_street("5524 US HWY 17"), "5524 US HIGHWAY 17")
        self.assertEqual(geocode.clean_street("10265 GA-142"), "10265 STATE HIGHWAY 142")
        self.assertEqual(geocode.clean_street("880 Indian Trail Lilburn Rd NW #A"), "880 INDIAN TRAIL LILBURN RD NW")


if __name__ == "__main__":
    unittest.main()
