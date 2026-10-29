"""Fetch every source, fail soft, and write data/dashboard.json.

Fail-soft rule: if a source errors, the section keeps its last good data, is marked "stale" with the error and
the time of the last good fetch, and the dashboard says so. A section with no prior data is "unavailable".
The run never exits non-zero just because a source failed; data/run_summary.md lists problems and the
workflow turns them into a GitHub issue."""
import json
import os
import sys
from datetime import datetime, timedelta

from . import events as ev
from .config import (CENTER, CHART_MAX_ITEMS, CHART_RADIUS_MI, DATA_DIR, MAP_RADIUS_MI, TOP_EVENTS, TZ,
                     WINDOW_DAYS, env)
from .http import SourceError
from .sources import airnow, crime, geocode, massdot, mbta, nws, ticketmaster

SECTIONS = ["weather", "air", "transit", "traffic", "crime", "events"]


def _load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def ensure_coordinates(venues):
    """Geocode curated venues once; fall back to the hand-placed coordinates so a pin never disappears."""
    cache, changed = geocode._load(), False
    for slug, v in venues.items():
        if v.get("lat") and v.get("lng"):
            continue
        hit = None
        try:
            hit = geocode.geocode(v["address"], cache)
        except SourceError:
            hit = None
        if hit:
            v["lat"], v["lng"], v["geo_source"] = hit[0], hit[1], hit[2]
        else:
            v["lat"], v["lng"], v["geo_source"] = v.get("fallback_lat"), v.get("fallback_lng"), "hand-placed fallback"
        changed = True
    if changed:
        geocode._save(cache)
    return changed


def build_events(now, today):
    venues_path = DATA_DIR / "venues.json"
    venues = _load(venues_path, {})
    manual = _load(DATA_DIR / "manual_events.json", {}).get("events", [])
    start = datetime.combine(today, datetime.min.time(), tzinfo=TZ)
    end = start + timedelta(days=WINDOW_DAYS + 1)
    res = ticketmaster.fetch(env("TICKETMASTER_API_KEY"), venues, start, end)   # may resolve venue ids
    ensure_coordinates(venues)
    venues_path.write_text(json.dumps(venues, indent=2, ensure_ascii=False) + "\n")
    selected, total = ev.select_top(res["events"], venues, manual, today, WINDOW_DAYS, TOP_EVENTS)
    pins = ev.build_pins(selected, venues, CENTER, MAP_RADIUS_MI, massdot.miles)
    return {"source": res["source"], "selected": selected, "pins": pins, "total_in_window": total,
            "geo_sources": {s: v.get("geo_source") for s, v in venues.items()}}


def default_sources(now):
    lat, lon = CENTER
    return {
        "weather": lambda: nws.fetch(lat, lon),
        "air": lambda: airnow.fetch(lat, lon, env("AIRNOW_API_KEY")),
        "transit": lambda: mbta.fetch(env("MBTA_API_KEY"), env("MBTA_ROUTE_TYPES", "0,1")),
        "traffic": lambda: massdot.fetch(env("MASSDOT_EVENTS_URL", massdot.EVENTS_URL_DEFAULT), env("MASSDOT_CWZ_URL"),
                                         env("MASSDOT_CWZ_KEY"), env("MASSDOT_CWZ_AUTH", "header:x-api-key"),
                                         MAP_RADIUS_MI, CHART_RADIUS_MI, CHART_MAX_ITEMS),
        "crime": lambda: crime.fetch(env("CRIME_ARCGIS_URL"), env("CRIME_ARCGIS_DATE_FIELD"), now.date()),
        "events": lambda: build_events(now, now.date()),
    }


def build(now=None, prev=None, sources=None):
    now = now or datetime.now(TZ)
    prev = prev if prev is not None else _load(DATA_DIR / "dashboard.json", {})
    sources = sources or default_sources(now)
    out = {"generated_at": now.isoformat(), "sections": {}}
    for name in SECTIONS:
        old = (prev.get("sections") or {}).get(name)
        try:
            data = sources[name]()
            out["sections"][name] = {"status": "ok", "fetched_at": now.isoformat(), "data": data}
        except Exception as e:  # noqa: any failure must degrade gracefully, not kill the run
            msg = ("%s" % e)[:300] if isinstance(e, SourceError) else "%s: %s" % (type(e).__name__, str(e)[:250])
            if old and old.get("data") is not None:
                out["sections"][name] = {"status": "stale", "fetched_at": old["fetched_at"], "data": old["data"], "error": msg,
                                         "stale_since": old.get("stale_since") if old.get("status") == "stale" else old["fetched_at"]}
            else:
                out["sections"][name] = {"status": "unavailable", "fetched_at": None, "data": None, "error": msg}
    return out


def write_summary(out):
    lines = ["# Dashboard refresh summary", "", "Generated: %s" % out["generated_at"], ""]
    bad = 0
    for name in SECTIONS:
        s = out["sections"][name]
        flag = {"ok": "OK", "stale": "STALE", "unavailable": "UNAVAILABLE"}[s["status"]]
        bad += s["status"] != "ok"
        lines.append("- **%s**: %s%s" % (name, flag, (" (%s)" % s["error"]) if s.get("error") else ""))
        for note in (s.get("data") or {}).get("notes", []) if isinstance(s.get("data"), dict) else []:
            lines.append("    - note: %s" % note)
    (DATA_DIR / "run_summary.md").write_text("\n".join(lines) + "\n")
    return bad


def main():
    out = build()
    (DATA_DIR / "dashboard.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    d = datetime.fromisoformat(out["generated_at"])
    arch = DATA_DIR / "archive" / d.strftime("%Y/%m/%d")
    arch.mkdir(parents=True, exist_ok=True)
    (arch / (d.strftime("%H%M") + ".json")).write_text(json.dumps(out, ensure_ascii=False))
    bad = write_summary(out)
    print((DATA_DIR / "run_summary.md").read_text())
    print("sections needing attention: %d" % bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
