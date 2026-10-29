"""AirNow API. IMPORTANT: AirNow retired its old /aq/observation/latLong/current/ service on
2026-09-30 (it now answers HTTP 410). This module uses the replacement services released in June 2026:
  /aq/observation/current/ziplatlong/   and   /aq/forecast/current/
The new observation payload is lowerCamelCase (nowcastAQI, aqiCategoryName, parameterName);
the old PascalCase names are also accepted so a rollback or schema tweak doesn't break the run.
The forecast payload shape was not verifiable from public docs, so it is parsed defensively and the
raw response is saved for inspection on the first run."""
import json

from .. import http
from ..config import RAW_DIR

BASE = "https://www.airnowapi.org"
OBS = "/aq/observation/current/ziplatlong/"
FCST = "/aq/forecast/current/"

CATEGORIES = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for Sensitive Groups"),
              (200, "Unhealthy"), (300, "Very Unhealthy"), (10**6, "Hazardous")]
GUIDANCE = {
    "Good": "Air quality is satisfactory and poses little or no risk.",
    "Moderate": "Air quality is acceptable. Unusually sensitive people should consider limiting prolonged outdoor exertion.",
    "Unhealthy for Sensitive Groups": "Sensitive groups may experience health effects. Reduce time outdoors if anyone feels symptoms such as difficulty breathing or throat irritation.",
    "Unhealthy": "Everyone may begin to experience health effects. Sensitive groups should avoid prolonged outdoor exertion.",
    "Very Unhealthy": "Health alert: the risk of health effects is increased for everyone. Limit time outdoors.",
    "Hazardous": "Health warning of emergency conditions. Avoid outdoor activity.",
}


def category(aqi):
    for top, name in CATEGORIES:
        if aqi <= top:
            return name


def _pick(d, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def parse_observations(payload):
    if isinstance(payload, dict):
        if "WebServiceError" in payload:
            raise http.SourceError("AirNow returned no observations in range (%s)" % str(payload["WebServiceError"])[:120])
        payload = payload.get("data") or payload.get("observations") or []
    rows = []
    for r in payload:
        aqi = _pick(r, "nowcastAQI", "aqi", "AQI")
        try:
            aqi = int(aqi)
        except (TypeError, ValueError):
            continue
        if aqi < 0:  # AirNow uses -999 for "no data"
            continue
        name = (_pick(r, "parameterName", "ParameterName") or "").upper()
        rows.append({"pollutant": "O3" if name == "OZONE" else name, "aqi": aqi,
                     "area": _pick(r, "reportingAreaName", "ReportingArea", "reportingArea"),
                     "date": _pick(r, "dateObserved", "DateObserved"), "hour": _pick(r, "hourObserved", "HourObserved")})
    if not rows:
        raise http.SourceError("AirNow observation response had no usable AQI values")
    return rows


def parse_forecast(payload):
    """Group forecast rows by the day they are VALID for (dateValid), taking the worst pollutant per day.
    Shape confirmed against a real response on 2026-10-29: one row per pollutant per day with dateIssue (when it
    was issued, NOT used), dateValid (the forecast day), aqi, categoryName, categoryNumber, forecastAgency.
    Older/other spellings are accepted as fallbacks. A row with no AQI number (-1) keeps its category only."""
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("forecasts") or []
    by_date = {}
    for r in payload if isinstance(payload, list) else []:
        date = _pick(r, "dateValid", "DateForecast", "dateForecast", "forecastDate")
        if not date:
            continue
        date = str(date)[:10]
        try:
            aqi = int(_pick(r, "aqi", "AQI"))
        except (TypeError, ValueError):
            aqi = -1
        num = _pick(r, "categoryNumber", "CategoryNumber")
        if aqi < 0 and not num:
            continue
        cur = by_date.get(date)
        rank = aqi if aqi >= 0 else -1
        if cur is None or rank > cur["rank"] or (rank == cur["rank"] and (num or 0) > (cur["num"] or 0)):
            by_date[date] = {"rank": rank, "num": num, "aqi": aqi if aqi >= 0 else None,
                             "name": _pick(r, "categoryName"), "agency": _pick(r, "forecastAgency"), "pollutant": _pick(r, "parameterName")}
    out = []
    for d, v in sorted(by_date.items()):
        cat = category(v["aqi"]) if v["aqi"] is not None else (v["name"] or "Unknown")
        out.append({"date": d, "aqi": v["aqi"], "category": cat, "agency": v["agency"], "pollutant": v["pollutant"]})
    return out


def fetch(lat, lon, key, distance=25):
    if not key:
        raise http.SourceError("AIRNOW_API_KEY is not set")
    params = {"latitude": lat, "longitude": lon, "distance": distance,
              "format": "application/json", "API_KEY": key}
    rows = parse_observations(http.get_json(BASE + OBS, params=params))
    worst = max(rows, key=lambda r: r["aqi"])
    out = {"source": "AirNow (EPA)", "aqi": worst["aqi"], "category": category(worst["aqi"]),
           "pollutant": worst["pollutant"], "area": worst["area"], "observed": "%s %s" % (worst["date"] or "", worst["hour"] or ""),
           "guidance": GUIDANCE[category(worst["aqi"])], "forecast": [], "notes": []}
    try:
        raw = http.get_json(BASE + FCST, params=params)
        out["forecast"] = parse_forecast(raw)
        if not out["forecast"]:
            out["notes"].append("AirNow forecast response was not in a recognized format; raw copy saved for review.")
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_DIR / "airnow_forecast_sample.json").write_text(json.dumps(raw)[:200000])
    except http.SourceError as e:
        out["notes"].append("AirNow forecast unavailable: %s" % e)
    return out
