#!/usr/bin/env python3
"""Download simplified Georgia county outlines from the US Census Bureau (TIGERweb)
and save them to collector/geo/ga_counties.geojson. Run only when boundaries change.

    python3 collector/fetch_counties.py
"""
import json
import os
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "geo", "ga_counties.geojson")
URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"
PARAMS = {
    "where": "STATE='13'",              # Georgia
    "outFields": "BASENAME,GEOID",
    "outSR": "4326",
    "maxAllowableOffset": "0.002",       # ~200 m simplification: plenty for a statewide overlay
    "geometryPrecision": "4",
    "f": "geojson",
}


def main():
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(PARAMS),
                                 headers={"User-Agent": "ga-inspection-map/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    features = []
    for f in sorted(data["features"], key=lambda f: f["properties"]["GEOID"]):
        features.append({
            "type": "Feature",
            "properties": {"geoid": f["properties"]["GEOID"], "name": f["properties"]["BASENAME"]},
            "geometry": f["geometry"],
        })
    if len(features) != 159:
        raise SystemExit("expected 159 Georgia counties, got %d" % len(features))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, separators=(",", ":"))
    print("wrote %d counties to %s" % (len(features), OUT))


if __name__ == "__main__":
    main()
