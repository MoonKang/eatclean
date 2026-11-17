"""Shared HTTP access to the Georgia DPH inspection portal."""
import json
import time
import urllib.error
import urllib.request

SITE = "https://ga.healthinspections.us"
BASE = SITE + "/stateofgeorgia/API/index.cfm"
USER_AGENT = "ga-inspection-map-collector/0.1 (low-rate; public inspection records)"


class Blocked(Exception):
    """The portal refused the request. We stop instead of retrying."""


def fetch_json(path, timeout=30, retries=3):
    url = "%s/%s" % (BASE, path)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                raise Blocked("HTTP %d on %s" % (e.code, url))
            err = "HTTP %d" % e.code
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            err = str(e)
        if attempt == retries:
            raise RuntimeError("giving up on %s: %s" % (url, err))
        time.sleep(5 * attempt)
