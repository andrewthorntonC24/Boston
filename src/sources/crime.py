"""Crime counts from Boston's open data hub (Analyze Boston, CKAN) with an optional ArcGIS path.

IMPORTANT: these counts will NOT exactly match the BPD dashboard you screenshot. The open-data file is
refreshed daily with a lag, excludes records covered by MGL ch.41 s.98f, and is counted here as one row per
offense through the last COMPLETE day. The dashboard states its method differently. The section label says so.

ArcGIS path: set CRIME_ARCGIS_URL (a FeatureServer layer .../query URL) and CRIME_ARCGIS_DATE_FIELD to count
directly from the service behind the dashboard. The URL isn't public in the docs I could reach, so it's opt-in."""
import csv
from collections import Counter
from datetime import date, datetime, timedelta

from .. import http
from ..config import TZ

PACKAGE = "https://data.boston.gov/api/3/action/package_show"
PACKAGE_ID = "crime-incident-reports-august-2015-to-date-source-new-system"
WINDOWS = [("d7", 7, "Last 7 days"), ("d30", 30, "Last 30 days"), ("d365", 365, "Last 365 days")]


def pick_resource(resources, wanted="2023 to present"):
    for r in resources:
        if wanted in (r.get("name") or "").lower() and r.get("url"):
            return r
    csvs = [r for r in resources if (r.get("format") or "").lower() == "csv" and r.get("url")]
    if not csvs:
        raise http.SourceError("no CSV resource found in the crime dataset")
    return csvs[0]


def count_by_day(stream):
    """stream: any text file-like or iterable of CSV lines."""
    reader = csv.DictReader(stream)
    counts = Counter()
    field = None
    for row in reader:
        if field is None:
            field = next((k for k in row if k and k.upper() == "OCCURRED_ON_DATE"), None)
            if not field:
                raise http.SourceError("crime CSV has no OCCURRED_ON_DATE column")
        d = (row.get(field) or "")[:10]
        if len(d) == 10:
            counts[d] += 1
    return counts


def windows_from_counts(counts, today):
    days = sorted(counts)
    if not days:
        raise http.SourceError("crime CSV contained no dated rows")
    latest = date.fromisoformat(days[-1])
    partial = latest >= today          # today's file is incomplete; use the last complete day
    end = today - timedelta(days=1) if partial else latest
    out = {}
    for key, n, label in WINDOWS:
        def total(a, b):
            return sum(v for d, v in counts.items() if a <= date.fromisoformat(d) <= b)
        out[key] = {"now": total(end - timedelta(days=n - 1), end),
                    "prev": total(end - timedelta(days=2 * n - 1), end - timedelta(days=n)), "label": label}
    return out, end, partial


def _arcgis_total(url, field, a, b):
    where = "%s >= TIMESTAMP '%s 00:00:00' AND %s < TIMESTAMP '%s 00:00:00'" % (field, a, field, b)
    j = http.get_json(url, params={"where": where, "returnCountOnly": "true", "f": "json"})
    if "count" not in j:
        raise http.SourceError("ArcGIS query returned no count")
    return int(j["count"])


def fetch_arcgis(url, field, today):
    end = today                         # exclusive upper bound = today 00:00, so complete days only
    out = {}
    for key, n, label in WINDOWS:
        a, m, p = end - timedelta(days=n), end, end - timedelta(days=2 * n)
        out[key] = {"now": _arcgis_total(url, field, a, m), "prev": _arcgis_total(url, field, p, a), "label": label}
    return out, end - timedelta(days=1)


def fetch(arcgis_url=None, arcgis_field=None, today=None):
    today = today or datetime.now(TZ).date()
    if arcgis_url and arcgis_field:
        counts, through = fetch_arcgis(arcgis_url, arcgis_field, today)
        return {"source": "BPD crime service (ArcGIS)", "through": through.isoformat(), **counts, "method": "arcgis"}
    pkg = http.get_json(PACKAGE, params={"id": PACKAGE_ID})["result"]
    res = pick_resource(pkg["resources"])
    counts = count_by_day(http.open_text(res["url"]))
    out, end, partial = windows_from_counts(counts, today)
    return {"source": "Analyze Boston, Crime Incident Reports (%s)" % res.get("name"), "through": end.isoformat(),
            "partial_day_excluded": partial, **out, "method": "ckan-csv"}
