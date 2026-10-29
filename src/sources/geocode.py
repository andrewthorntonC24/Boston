"""Geocoding with a permanent cache. Addresses are looked up once (US Census Geocoder, no key),
falling back to Nominatim only for addresses the Census can't match. Cached results are committed
to the repo, so pins don't move between runs and review is a normal git diff."""
import json
import time
from datetime import datetime, timezone

from .. import http
from ..config import DATA_DIR

CACHE = DATA_DIR / "geocache.json"
CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _load():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save(c):
    CACHE.write_text(json.dumps(c, indent=2, sort_keys=True))


def census(address):
    j = http.get_json(CENSUS, params={"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    m = (j.get("result") or {}).get("addressMatches") or []
    if not m:
        return None
    c = m[0]["coordinates"]            # x = longitude, y = latitude
    return float(c["y"]), float(c["x"])


def nominatim(address):
    time.sleep(1.1)                    # Nominatim usage policy: max 1 request per second
    j = http.get_json(NOMINATIM, params={"q": address, "format": "jsonv2", "limit": 1})
    return (float(j[0]["lat"]), float(j[0]["lon"])) if j else None


def geocode(address, cache=None):
    """Returns (lat, lng, source) or None. Pass a dict as `cache` to batch writes yourself."""
    own = cache is None
    cache = _load() if own else cache
    if address in cache:
        c = cache[address]
        return c["lat"], c["lng"], c["source"]
    hit, src = None, None
    for name, fn in (("census", census), ("nominatim", nominatim)):
        try:
            hit = fn(address)
        except http.SourceError:
            hit = None
        if hit:
            src = name
            break
    if not hit:
        return None
    cache[address] = {"lat": hit[0], "lng": hit[1], "source": src, "at": datetime.now(timezone.utc).isoformat()}
    if own:
        _save(cache)
    return hit[0], hit[1], src
