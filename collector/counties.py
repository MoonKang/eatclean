"""Georgia county lookup and inspection-data coverage, using the Census outlines
in collector/geo/ga_counties.geojson (standard library only)."""
import json
import os

GEO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geo", "ga_counties.geojson")

# Counties known to publish their inspections somewhere other than the state portal.
# Shown on the map so people can still look restaurants up.
ELSEWHERE = {
    "13135": ("GNR Public Health", "https://inspections.myhealthdepartment.com/gwinnett-ga"),  # Gwinnett
    "13217": ("GNR Public Health", "https://inspections.myhealthdepartment.com/gwinnett-ga"),  # Newton
    "13247": ("GNR Public Health", "https://inspections.myhealthdepartment.com/gwinnett-ga"),  # Rockdale
}

# A county is shaded when fewer than this share of its listed facilities have an inspection.
MIN_INSPECTED_SHARE = 0.5


def _polygons(geometry):
    """Yield each polygon as a list of rings (outer ring first, then holes)."""
    if geometry["type"] == "Polygon":
        yield geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        for poly in geometry["coordinates"]:
            yield poly


def _in_ring(x, y, ring):
    """Ray casting: is (x, y) inside this closed ring?"""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class CountyIndex:
    def __init__(self, path=GEO_FILE):
        with open(path) as fh:
            self.collection = json.load(fh)
        self._shapes = []  # (geoid, name, bbox, rings)
        for f in self.collection["features"]:
            geoid, name = f["properties"]["geoid"], f["properties"]["name"]
            for rings in _polygons(f["geometry"]):
                xs = [p[0] for p in rings[0]]
                ys = [p[1] for p in rings[0]]
                self._shapes.append((geoid, name, (min(xs), min(ys), max(xs), max(ys)), rings))
        self.names = {f["properties"]["geoid"]: f["properties"]["name"] for f in self.collection["features"]}

    def lookup(self, lon, lat):
        """Return the county GEOID containing the point, or None (outside Georgia)."""
        for geoid, _name, (x0, y0, x1, y1), rings in self._shapes:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if _in_ring(lon, lat, rings[0]) and not any(_in_ring(lon, lat, hole) for hole in rings[1:]):
                return geoid
        return None

    def coverage_geojson(self, counts, inspected):
        """County outlines with facility counts and a 'covered' flag for the map.

        counts: facilities listed per county; inspected: how many of those have an
        inspection. A county the portal does not inspect for still shows up with a
        few listed permits (mobile-vendor bases, pools, odd geocodes) that have no
        scores, so coverage is judged by the share with an inspection, not by
        whether any facility is listed. Statewide the share is ~99%; the GNR
        counties are under 10%.
        """
        features = []
        for f in self.collection["features"]:
            geoid = f["properties"]["geoid"]
            listed, scored = counts.get(geoid, 0), inspected.get(geoid, 0)
            props = {
                "geoid": geoid,
                "name": f["properties"]["name"],
                "facilities": listed,
                "inspected": scored,
                "covered": listed > 0 and scored >= MIN_INSPECTED_SHARE * listed,
            }
            if geoid in ELSEWHERE:
                props["source"], props["source_url"] = ELSEWHERE[geoid]
            features.append({"type": "Feature", "properties": props, "geometry": f["geometry"]})
        return {"type": "FeatureCollection", "features": features}
