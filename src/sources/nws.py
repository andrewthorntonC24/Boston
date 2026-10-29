"""National Weather Service API (api.weather.gov). No key; a User-Agent with contact info is required.
Flow (per NWS docs): /points/{lat},{lon} -> properties.forecast URL -> list of 12-hour periods."""
from datetime import datetime

from .. import http

BASE = "https://api.weather.gov"


def _f(temp, unit):
    if temp is None:
        return None
    return round(temp * 9 / 5 + 32) if unit == "C" else int(temp)


def group_periods(periods):
    """Collapse NWS day/night periods into one card per calendar day."""
    days = {}
    for p in periods:
        start = datetime.fromisoformat(p["startTime"])
        key = start.date().isoformat()
        d = days.setdefault(key, {"date": key, "dow": start.strftime("%a"), "high": None, "low": None,
                                  "desc": None, "precip": None, "wind": None, "night_desc": None, "night_precip": None})
        t = _f(p.get("temperature"), p.get("temperatureUnit", "F"))
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        if p.get("isDaytime"):
            d["high"], d["desc"], d["precip"] = t, p.get("shortForecast"), pop
            d["wind"] = "%s %s" % (p.get("windDirection", ""), p.get("windSpeed", "")) if p.get("windSpeed") else None
        else:
            d["low"], d["night_desc"], d["night_precip"] = t, p.get("shortForecast"), pop
    out = []
    for k in sorted(days):
        d = days[k]
        d["desc"] = d["desc"] or d["night_desc"] or ""
        if d["precip"] is None:
            d["precip"] = d["night_precip"]
        d["precip"] = int(d["precip"] or 0)
        d.pop("night_desc"), d.pop("night_precip")
        out.append(d)
    return out


def fetch(lat, lon):
    pt = http.get_json("%s/points/%.4f,%.4f" % (BASE, lat, lon))
    props = pt["properties"]
    fc = http.get_json(props["forecast"])
    days = group_periods(fc["properties"]["periods"])
    alerts = []
    try:
        al = http.get_json("%s/alerts/active" % BASE, params={"point": "%.4f,%.4f" % (lat, lon)})
        for f in al.get("features", [])[:5]:
            a = f["properties"]
            alerts.append({"event": a.get("event"), "severity": a.get("severity"),
                           "headline": a.get("headline"), "ends": a.get("ends") or a.get("expires")})
    except http.SourceError:
        pass  # alerts are a bonus; the forecast is what matters
    return {"source": "National Weather Service (api.weather.gov)", "office": props.get("gridId"),
            "updated": fc["properties"].get("updateTime"), "days": days, "alerts": alerts}
