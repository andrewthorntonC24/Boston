"""Offline stand-ins for every API.

IMPORTANT: these payloads were WRITTEN FROM THE DOCUMENTATION, not captured from the live services.
They prove the pipeline's logic (parsing, ranking, fail-soft, rendering, escaping). They do NOT prove the
live APIs return exactly this shape. The first real run is the real test; see the README checklist."""
import io
from datetime import date, timedelta

TODAY = date(2026, 10, 21)


def d(n):
    return (TODAY + timedelta(days=n)).isoformat()


# CAPTURED FROM THE LIVE SERVICE on 2026-10-29 (the only fixture here that is a real response, not written from docs).
AIRNOW_FORECAST_REAL = [
    {"dateIssue": "2026-10-28", "dateValid": "2026-10-29", "reportingArea": "Boston Metro", "reportingAreaCode": "ma001", "stateCode": "MA", "parameterName": "PM2.5", "aqi": 50, "forecastAgency": "Massachusetts Dept. of Environmental Protection", "categoryNumber": 1, "categoryName": "Good", "actionDay": False, "discussion": ""},
    {"dateIssue": "2026-10-28", "dateValid": "2026-10-30", "reportingArea": "Boston Metro", "reportingAreaCode": "ma001", "stateCode": "MA", "parameterName": "PM2.5", "aqi": 50, "forecastAgency": "Massachusetts Dept. of Environmental Protection", "categoryNumber": 1, "categoryName": "Good", "actionDay": False, "discussion": ""},
    {"dateIssue": "2026-10-28", "dateValid": "2026-10-31", "reportingArea": "Boston Metro", "reportingAreaCode": "ma001", "stateCode": "MA", "parameterName": "PM2.5", "aqi": 40, "forecastAgency": "Massachusetts Dept. of Environmental Protection", "categoryNumber": 1, "categoryName": "Good", "actionDay": False, "discussion": ""},
]


def nws_forecast():
    periods = []
    for i in range(7):
        day = TODAY + timedelta(days=i)
        periods.append({"startTime": "%sT06:00:00-04:00" % day, "isDaytime": True, "temperature": 58 + i, "temperatureUnit": "F",
                        "shortForecast": "Mostly sunny", "windSpeed": "10 mph", "windDirection": "NW",
                        "probabilityOfPrecipitation": {"value": 5 * i}})
        periods.append({"startTime": "%sT18:00:00-04:00" % day, "isDaytime": False, "temperature": 42 + i, "temperatureUnit": "F",
                        "shortForecast": "Clear", "windSpeed": "5 mph", "windDirection": "N",
                        "probabilityOfPrecipitation": {"value": 0}})
    return {"properties": {"updateTime": "2026-10-21T09:30:00+00:00", "periods": periods}}


def airnow_obs():
    return [{"reportingAreaName": "Boston", "parameterName": "PM2.5", "nowcastAQI": 51, "aqiCategoryName": "Moderate",
             "dateObserved": "2026-10-21", "hourObserved": "06:00"},
            {"reportingAreaName": "Boston", "parameterName": "OZONE", "nowcastAQI": 33, "aqiCategoryName": "Good",
             "dateObserved": "2026-10-21", "hourObserved": "06:00"}]


def mbta():
    return {"data": [
        {"id": "a1", "attributes": {"header": "Red Line: delays of about 15 minutes due to a signal problem.", "effect": "DELAY", "severity": 6,
                                    "lifecycle": "NEW", "active_period": [{"start": "2026-10-21T04:00:00-04:00", "end": None}],
                                    "informed_entity": [{"route": "Red", "route_type": 1}], "updated_at": "2026-10-21T05:50:00-04:00"}},
        {"id": "a2", "attributes": {"header": "Minor elevator outage.", "effect": "ACCESS_ISSUE", "severity": 2, "lifecycle": "ONGOING",
                                    "active_period": [], "informed_entity": [{"route": "Orange"}], "updated_at": "2026-10-20T10:00:00-04:00"}},
        {"id": "a3", "attributes": {"header": "Expired alert.", "effect": "DELAY", "severity": 8, "lifecycle": "NEW",
                                    "active_period": [{"start": "2026-10-01T00:00:00-04:00", "end": "2026-10-02T00:00:00-04:00"}],
                                    "informed_entity": [{"route": "Blue"}], "updated_at": "2026-10-01T00:00:00-04:00"}}]}


MASSDOT_XML = """<?xml version="1.0"?>
<events>
  <event><id>1</id><type>Crash</type><description>Crash on I-93 SB, left lane blocked &lt;script&gt;alert(1)&lt;/script&gt;</description>
    <roadway>I-93</roadway><direction>SB</direction><latitude>42.3875</latitude><longitude>-71.0995</longitude>
    <starttime>2026-10-21T05:40:00-04:00</starttime></event>
  <event><id>2</id><type>Planned maintenance</type><description>Overnight lane closures for paving</description>
    <roadway>Storrow Dr</roadway><direction>EB</direction><latitude>42.3555</latitude><longitude>-71.0815</longitude>
    <starttime>2026-10-21T20:00:00-04:00</starttime><endtime>2026-10-22T05:00:00-04:00</endtime></event>
  <event><id>3</id><type>Crash</type><description>Crash in Springfield</description><roadway>I-91</roadway>
    <latitude>42.1015</latitude><longitude>-72.5898</longitude></event>
</events>"""

WZDX = {"type": "FeatureCollection", "features": [
    {"id": "wz-1", "type": "Feature", "properties": {"core_details": {"road_names": ["Rt 28"], "direction": "southbound",
                                                                       "description": "Road work between Rt 93 and Rt 60"},
                                                     "start_date": "2026-10-21T00:00:00Z", "end_date": "2026-10-30T00:00:00Z"},
     "geometry": {"type": "LineString", "coordinates": [[-71.1, 42.41], [-71.11, 42.42]]}}]}


def crime_csv(today=TODAY):
    lines = ["INCIDENT_NUMBER,OFFENSE_CODE,OCCURRED_ON_DATE,STREET"]
    for back in range(0, 800):
        day = today - timedelta(days=back)
        n = 3 if back < 400 else 2
        for k in range(n):
            lines.append('I%d-%d,619,%s 12:00:00,"MAIN ST"' % (back, k, day))
    return "\n".join(lines) + "\n"


def tm_events():
    def ev(i, name, day, time, seg, genre, vid, vname, city="Boston", status="onsale"):
        return {"id": i, "name": name, "url": "https://example.test/" + i,
                "dates": {"start": {"localDate": day, "localTime": time}, "status": {"code": status}},
                "classifications": [{"segment": {"name": seg}, "genre": {"name": genre}}],
                "_embedded": {"venues": [{"id": vid, "name": vname, "city": {"name": city}, "address": {"line1": "1 Test St"},
                                          "location": {"latitude": "42.36", "longitude": "-71.06"}}]}}
    return {"_embedded": {"events": [
        ev("e1", "Rod Wave: Don't Look Down Tour", d(4), "20:00:00", "Music", "Hip-Hop/Rap", "V1", "TD Garden"),
        ev("e2", "Celtics vs. Knicks", d(2), "19:00:00", "Sports", "Basketball", "V1", "TD Garden"),
        ev("e3", "PARKING: Celtics vs. Knicks", d(2), "19:00:00", "Miscellaneous", "Undefined", "V1", "TD Garden"),
        ev("e4", "Quadeca", d(2), "19:00:00", "Music", "Hip-Hop/Rap", "V2", "House of Blues Boston"),
        ev("e5", "The Jayhawks", d(3), "20:00:00", "Music", "Rock", "V3", "Boch Center - Shubert Theatre"),
        ev("e6", "Watsky", d(5), "19:00:00", "Music", "Hip-Hop/Rap", "V4", "Somerville Theatre", "Somerville"),
        ev("e7", "Indie Night", d(1), "21:00:00", "Music", "Rock", "V9", "Paradise Rock Club"),
        ev("e8", "Cancelled Show", d(1), "21:00:00", "Music", "Rock", "V9", "Paradise Rock Club", status="cancelled"),
        ev("e9", "Far Future Show", d(14), "20:00:00", "Music", "Rock", "V1", "TD Garden"),
    ]}}


def route_json(url, params=None, headers=None, timeout=30):
    from src.http import SourceError
    params = params or {}
    if "api.weather.gov/points" in url:
        return {"properties": {"forecast": "https://api.weather.gov/gridpoints/BOX/71,90/forecast", "gridId": "BOX"}}
    if "gridpoints" in url:
        return nws_forecast()
    if "alerts/active" in url:
        return {"features": []}
    if "airnowapi.org" in url and "observation" in url:
        return airnow_obs()
    if "airnowapi.org" in url and "forecast" in url:
        return AIRNOW_FORECAST_REAL
    if "api-v3.mbta.com" in url:
        return mbta()
    if "package_show" in url:
        return {"result": {"resources": [{"name": "Crime Incident Reports - 2023 to Present", "format": "CSV", "url": "https://data.test/crime.csv"}]}}
    if "ticketmaster.com" in url and "venues.json" in url:
        kw = params.get("keyword", "")
        table = {"TD Garden": ("V1", "TD Garden"), "House of Blues Boston": ("V2", "House of Blues Boston"),
                 "Shubert Theatre": ("V3", "Boch Center - Shubert Theatre"), "Somerville Theatre": ("V4", "Somerville Theatre")}
        vid, name = table[kw]
        return {"_embedded": {"venues": [{"id": vid, "name": name, "location": {"latitude": "42.3662", "longitude": "-71.0621"}}]}}
    if "ticketmaster.com" in url and "events.json" in url:
        return tm_events() if (params.get("venueId") or params.get("city") == "Boston") else {}
    if "geocoding.geo.census.gov" in url:
        return {"result": {"addressMatches": [{"coordinates": {"x": -71.06, "y": 42.36}}]}}
    if "cwz.test" in url:
        return WZDX
    raise SourceError("unexpected URL in fake world: " + url)


def route_text(url, params=None, headers=None, timeout=30):
    if "events.massdot" in url:
        return MASSDOT_XML
    raise AssertionError(url)


def iter_lines(url, params=None, headers=None, timeout=120):
    return iter(crime_csv().splitlines())
