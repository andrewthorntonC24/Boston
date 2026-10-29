"""Ticketmaster Discovery API v2 (free key, 5,000 calls/day, 5 requests/second).
Uses venueId filters (the `latlong` filter is deprecated in favor of geoPoint) plus city queries so
events at venues not in data/venues.json still appear (rated Low and flagged as capacity-unverified).
Ticketmaster does not cover every venue; small rooms that sell elsewhere won't appear. Cycleboat and similar
come from data/manual_events.json."""
import re
import time
from datetime import timedelta, timezone

from .. import http

BASE = "https://app.ticketmaster.com/discovery/v2"
EXCLUDE = re.compile(r"parking|vip|package|premium|upgrade|add-?on|season ticket|suite|hospitality|meet\s*&\s*greet", re.I)
CITIES = ["Boston", "Somerville", "Cambridge"]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def resolve_venue_ids(venues, key):
    """Fill tm_venue_id for curated venues. Returns True if anything changed (caller saves venues.json)."""
    changed = False
    for slug, v in venues.items():
        if v.get("tm_venue_id") or not v.get("tm_keyword"):
            continue
        j = http.get_json(BASE + "/venues.json", params={"apikey": key, "keyword": v["tm_keyword"], "stateCode": "MA", "size": 10})
        for cand in (j.get("_embedded") or {}).get("venues", []):
            if _norm(v["tm_keyword"]) in _norm(cand.get("name")):
                v["tm_venue_id"] = cand["id"]
                loc = cand.get("location") or {}
                if loc.get("latitude") and not v.get("lat"):
                    v["lat"], v["lng"] = float(loc["latitude"]), float(loc["longitude"])
                changed = True
                break
        time.sleep(0.25)
    return changed


def _parse(ev):
    st = ev.get("dates", {}).get("start", {})
    vn = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
    loc = vn.get("location") or {}
    cl = (ev.get("classifications") or [{}])[0]
    return {"id": ev["id"], "name": ev.get("name", ""), "date": st.get("localDate"), "time": st.get("localTime"),
            "status": (ev.get("dates", {}).get("status") or {}).get("code", ""), "url": ev.get("url"),
            "segment": (cl.get("segment") or {}).get("name"), "genre": (cl.get("genre") or {}).get("name"),
            "venue_id": vn.get("id"), "venue_name": vn.get("name"), "city": (vn.get("city") or {}).get("name"),
            "address": (vn.get("address") or {}).get("line1"),
            "lat": float(loc["latitude"]) if loc.get("latitude") else None,
            "lng": float(loc["longitude"]) if loc.get("longitude") else None, "source": "Ticketmaster"}


def fetch(key, venues, start, end):
    """start/end are timezone-aware datetimes."""
    if not key:
        raise http.SourceError("TICKETMASTER_API_KEY is not set")
    venues_changed = resolve_venue_ids(venues, key)
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"apikey": key, "startDateTime": fmt(start), "endDateTime": fmt(end), "size": 200, "sort": "date,asc"}
    queries = []
    ids = [v["tm_venue_id"] for v in venues.values() if v.get("tm_venue_id")]
    if ids:
        queries.append(dict(base, venueId=",".join(ids)))
    queries += [dict(base, city=c, stateCode="MA") for c in CITIES]
    seen, events = set(), []
    for q in queries:
        j = http.get_json(BASE + "/events.json", params=q)
        for ev in (j.get("_embedded") or {}).get("events", []):
            e = _parse(ev)
            sig = (_norm(e["name"]), e["date"], e["venue_id"])
            if e["id"] in seen or sig in seen or EXCLUDE.search(e["name"]) or e["status"] == "cancelled" or not e["date"]:
                continue
            seen.update([e["id"], sig])
            events.append(e)
        time.sleep(0.25)
    return {"source": "Ticketmaster Discovery API", "events": events, "venues_changed": venues_changed}
