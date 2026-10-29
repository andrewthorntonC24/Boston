"""MassDOT traffic data. WHAT IS (AND ISN'T) AVAILABLE, per MassDOT's "Highway data for developers" page:
  * Roadway Events XML feed (events.massdot.evbg.net): Highway Division events; the page states it
    "does not include all incidents in Massachusetts". Schema not published in the docs I could reach, so
    parsing here is tolerant and a raw sample is saved on the first run for you to check.
  * Connected Work Zone (CWZ) feed: WZDx-based GeoJSON; needs a free account and API key from the MassDOT
    Work Zones API portal. You set MASSDOT_CWZ_URL (from the portal) and MASSDOT_CWZ_KEY.
  * There is no public "Mass511 API". Travel-time (GoTime) and camera access are by request and aren't used here.
So this will NOT reproduce the Boston 25 list (which included NH, CT, RI and other sources). Expect fewer
incidents and a different set of construction items."""
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from .. import http
from ..config import CENTER, RAW_DIR

EVENTS_URL_DEFAULT = "http://events.massdot.evbg.net/"
INCIDENT_WORDS = re.compile(r"crash|collision|accident|disabled|stalled|vehicle fire|spill|debris|overturned|rollover", re.I)


def miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _strip(tag):
    return tag.split("}")[-1].lower()


def _flatten(el):
    out = {}
    for sub in el.iter():
        if sub is el:
            continue
        if sub.text and sub.text.strip() and len(list(sub)) == 0:
            out.setdefault(_strip(sub.tag), sub.text.strip())
    for k, v in el.attrib.items():
        out.setdefault(_strip(k), v)
    return out


def _first(d, *names, contains=None):
    for n in names:
        if d.get(n):
            return d[n]
    if contains:
        for k, v in d.items():
            if contains in k and v:
                return v
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_events_xml(text):
    root = ET.fromstring(text)
    # the record element is the most frequently repeated child tag anywhere in the tree
    tags = {}
    for el in root.iter():
        if el is not root:
            tags.setdefault(_strip(el.tag), []).append(el)
    candidates = [(len(v), t) for t, v in tags.items() if len(v) >= 1 and sum(1 for c in v[0].iter()) >= 4]
    if not candidates:
        return []
    candidates.sort(reverse=True)
    records = tags[candidates[0][1]]
    items = []
    for i, el in enumerate(records):
        d = _flatten(el)
        lat = _num(_first(d, "latitude", "lat", contains="lat"))
        lng = _num(_first(d, "longitude", "lon", "lng", "long", contains="lon"))
        desc = _first(d, "description", "headline", "message", "comments", "text", "title", "summary") or ""
        road = _first(d, "roadway", "road", "roadname", "route", "street", "location", "locationdescription") or ""
        direction = _first(d, "direction", "dir") or ""
        kind = _first(d, "eventtype", "type", "category", "eventcategory") or ""
        blob = " ".join([desc, kind])
        items.append({"id": "evt-%s-%d" % (d.get("id", ""), i), "type": "Incident" if INCIDENT_WORDS.search(blob) or "incident" in kind.lower() else "Construction",
                      "road": road, "direction": direction, "desc": desc or kind or "Roadway event",
                      "start": _first(d, "starttime", "startdate", "start", "begintime", "reported", "created", "timestamp"),
                      "end": _first(d, "endtime", "enddate", "end", "expirationtime", "estimatedend", "expected"),
                      "lat": lat, "lng": lng, "source": "MassDOT roadway events"})
    return items


def parse_wzdx(gj):
    items = []
    for i, f in enumerate(gj.get("features", [])):
        p = f.get("properties", {})
        core = p.get("core_details", p)
        g = f.get("geometry") or {}
        c = g.get("coordinates")
        pt = None
        if g.get("type") == "Point":
            pt = c
        elif g.get("type") in ("LineString", "MultiPoint") and c:
            pt = c[0]
        elif g.get("type") == "MultiLineString" and c and c[0]:
            pt = c[0][0]
        roads = core.get("road_names") or []
        items.append({"id": "cwz-%s" % (f.get("id") or core.get("road_event_id") or i), "type": "Construction",
                      "road": ", ".join(roads) if isinstance(roads, list) else str(roads),
                      "direction": core.get("direction") or "", "desc": core.get("description") or core.get("name") or "Work zone",
                      "start": p.get("start_date") or core.get("start_date"), "end": p.get("end_date") or core.get("end_date"),
                      "lat": pt[1] if pt else None, "lng": pt[0] if pt else None, "source": "MassDOT Connected Work Zones"})
    return items


def _fmt(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%b %d %I:%M %p").replace(" 0", " ")
    except ValueError:
        return str(ts)


def annotate(items, map_radius, chart_radius, chart_max):
    out = []
    for it in items:
        if it["lat"] is None or it["lng"] is None:
            it["dist_mi"], it["boston"] = None, False
        else:
            it["dist_mi"] = round(miles(CENTER[0], CENTER[1], it["lat"], it["lng"]), 1)
            it["boston"] = it["dist_mi"] <= map_radius
        it["start_fmt"], it["end_fmt"] = _fmt(it.get("start")), _fmt(it.get("end"))
        if it["dist_mi"] is None or it["dist_mi"] <= chart_radius:
            out.append(it)
    out.sort(key=lambda x: (x["type"] != "Incident", x["dist_mi"] if x["dist_mi"] is not None else 999))
    return out[:chart_max]


def _save_raw(name, text):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    p = RAW_DIR / name
    if not p.exists():
        p.write_text(text[:200000])


def fetch(events_url=EVENTS_URL_DEFAULT, cwz_url=None, cwz_key=None, cwz_auth="header:x-api-key",
          map_radius=15, chart_radius=30, chart_max=40):
    items, notes = [], []
    try:
        text = http.get_text(events_url, headers={"Accept": "application/xml"})
        _save_raw("massdot_events_sample.xml", text)
        ev = parse_events_xml(text)
        if not any(e["lat"] is not None for e in ev):
            notes.append("MassDOT events feed parsed %d records but none had coordinates; check data/raw/massdot_events_sample.xml." % len(ev))
        items += ev
    except (http.SourceError, ET.ParseError) as e:
        notes.append("MassDOT events feed failed: %s" % e)
    if cwz_url and cwz_key:
        try:
            how, name = (cwz_auth.split(":", 1) + ["x-api-key"])[:2]
            params, headers = ({name: cwz_key}, {}) if how == "query" else ({}, {name: cwz_key})
            gj = http.get_json(cwz_url, params=params, headers=headers)
            items += parse_wzdx(gj)
        except http.SourceError as e:
            notes.append("MassDOT CWZ feed failed: %s" % e)
    else:
        notes.append("MassDOT CWZ feed not configured (set MASSDOT_CWZ_URL and MASSDOT_CWZ_KEY).")
    if not items:
        raise http.SourceError("no MassDOT traffic items. " + " ".join(notes))
    return {"source": "MassDOT (roadway events feed + Connected Work Zones)", "notes": notes,
            "items": annotate(items, map_radius, chart_radius, chart_max)}
