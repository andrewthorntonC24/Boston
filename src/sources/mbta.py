"""MBTA V3 API (JSON:API). Key is free; anonymous requests work but are heavily rate limited.
Default route types are subway/light rail only (0,1). Commuter rail delays were removed from the
dashboard at your request, so type 2 is NOT included unless you set MBTA_ROUTE_TYPES=0,1,2."""
from datetime import datetime, timezone

from .. import http

URL = "https://api-v3.mbta.com/alerts"


def _active(periods, now):
    if not periods:
        return True
    for p in periods:
        s = datetime.fromisoformat(p["start"]) if p.get("start") else None
        e = datetime.fromisoformat(p["end"]) if p.get("end") else None
        if (s is None or s <= now) and (e is None or now <= e):
            return True
    return False


def fetch(key=None, route_types="0,1", min_severity=5, limit=8, now=None):
    now = now or datetime.now(timezone.utc)
    headers = {"Accept": "application/vnd.api+json"}
    if key:
        headers["x-api-key"] = key
    params = {"filter[route_type]": route_types, "filter[datetime]": "NOW", "page[limit]": 100}
    j = http.get_json(URL, params=params, headers=headers)
    alerts = []
    for a in j.get("data", []):
        at = a.get("attributes", {})
        if (at.get("severity") or 0) < min_severity or not _active(at.get("active_period"), now):
            continue
        routes = sorted({e["route"] for e in at.get("informed_entity", []) if e.get("route")})
        alerts.append({"id": a.get("id"), "header": at.get("header"), "effect": at.get("effect"),
                       "severity": at.get("severity"), "lifecycle": at.get("lifecycle"),
                       "routes": routes, "updated": at.get("updated_at")})
    alerts.sort(key=lambda x: -(x["severity"] or 0))
    return {"source": "MBTA V3 API", "route_types": route_types, "min_severity": min_severity,
            "total_matching": len(alerts), "alerts": alerts[:limit]}
