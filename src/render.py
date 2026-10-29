"""Fill templates/dashboard.template.html from data/dashboard.json and the hand-maintained files.

Rules baked in (from the saved dashboard skill):
  * Data Snapshot shows the day of the run; every section shows when it was last refreshed, or says it is stale.
  * Ratings are Low / Medium / High / Very High, capitalized, colored, and bold in the glance section.
  * All feed text is HTML-escaped before it reaches the page or the map pop-ups.
  * The output is validated (placeholders filled, tags balanced, script parses) before it is written."""
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from .config import (CHART_RADIUS_MI, DATA_DIR, DIST_DIR, PROTEST_STALE_DAYS, TEMPLATE, TOP_EVENTS, TZ, WINDOW_DAYS)

CLS = {"Low": "low", "Medium": "medium", "High": "high", "Very High": "very-high"}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def js(obj):
    """JSON for embedding in a <script>: neutralize anything that could close the tag."""
    return json.dumps(obj, ensure_ascii=False, indent=2).replace("</", "<\\/").replace("<!--", "<\\!--")


def pill(r):
    return '<span class="pill %s">%s</span>' % (CLS.get(r, "low"), esc(r))


def rating_word(r):
    return '<strong style="color:var(--%s)">%s</strong>' % (CLS.get(r, "low"), esc(r))


def fmt_day(d):
    return "%s %d" % (d.strftime("%b"), d.day)


def fmt_range(a, b):
    return "%s-%d" % (fmt_day(a), b.day) if a.month == b.month else "%s-%s" % (fmt_day(a), fmt_day(b))


def fmt_ts(iso):
    if not iso:
        return "never"
    d = datetime.fromisoformat(iso).astimezone(TZ)
    return "%s %d, %s %s ET" % (d.strftime("%b"), d.day, d.year, d.strftime("%I:%M %p").lstrip("0"))


def fresh(sec):
    """A one-line freshness note; yellow and explicit when data is stale or missing."""
    if sec["status"] == "ok":
        return "Updated %s." % fmt_ts(sec["fetched_at"])
    if sec["status"] == "stale":
        return '<span class="pending">Stale: showing last good data from %s. This refresh failed: %s</span>' % (fmt_ts(sec["fetched_at"]), esc(sec.get("error")))
    return '<span class="pending">Not available: %s</span>' % esc(sec.get("error"))


def data_of(sec):
    return sec.get("data") if sec else None


# ---------------------------------------------------------------- weather / air
def render_weather(sec, today):
    d = data_of(sec)
    if not d:
        return {"weather_cards": '      <div class="day"><div class="day-desc">Forecast not available this run.</div></div>',
                "weather_meta": '<p class="meta">%s</p>' % fresh(sec)}
    cards = []
    for day in d["days"]:
        dd = date.fromisoformat(day["date"])
        if dd < today:
            continue
        hi = "%s&deg;" % day["high"] if day["high"] is not None else "&ndash;"
        lo = "/ %s&deg;" % day["low"] if day["low"] is not None else "/ &ndash;"
        label = fmt_day(dd) + (" (today)" if dd == today else "")
        cards.append('      <div class="day"><div class="day-name">%s</div><div class="day-date">%s</div><div class="temps">%s <span>%s</span></div>'
                     '<div class="day-desc">%s</div><div class="precip">Precip %d%%</div></div>' % (esc(day["dow"]), label, hi, lo, esc(day["desc"]), day["precip"]))
    meta = '<p class="meta">Forecast source: %s, forecast office %s. Forecast issued %s. %s</p>' % (
        esc(d["source"]), esc(d.get("office")), fmt_ts(d.get("updated")), fresh(sec))
    if d.get("alerts"):
        meta += '\n    <p class="meta"><strong>Active NWS alerts:</strong> %s</p>' % "; ".join(esc(a["headline"] or a["event"]) for a in d["alerts"][:3])
    return {"weather_cards": "\n".join(cards), "weather_meta": meta}


def render_air(sec):
    d = data_of(sec)
    if not d:
        value, text = "Not available", "Air quality could not be refreshed. %s" % fresh(sec)
    else:
        value = "%s (%d)" % (d["category"], d["aqi"])
        fc = d.get("forecast") or []
        fc_txt = " Forecast: %s." % "; ".join("%s AQI %d (%s)" % (date.fromisoformat(f["date"]).strftime("%a %b ") + str(date.fromisoformat(f["date"]).day), f["aqi"], f["category"]) for f in fc[:3]) if fc else ""
        notes = " ".join(esc(n) for n in d.get("notes", []))
        text = "Source: %s, %s reporting area. Current AQI %d (%s), driven by %s. %s%s %s %s" % (
            esc(d["source"]), esc(d.get("area") or "nearest"), d["aqi"], esc(d["category"]), esc(d.get("pollutant") or "the highest pollutant"),
            esc(d["guidance"]), esc(fc_txt), notes, fresh(sec))
    return {"aq_block": '<div class="aq">\n      <div>\n        <div class="day-name">Air Quality</div>\n        <div class="aq-value">%s</div>\n      </div>\n      <div class="aq-text">%s</div>\n    </div>' % (esc(value), text)}


# ---------------------------------------------------------------- events
def render_events(sec, today):
    d = data_of(sec)
    rows = []
    if d:
        for r in d["selected"]:
            rows.append('        <tr>\n          <td data-label="Date(s)">%s</td>\n          <td data-label="Event" class="event-name">%s</td>\n'
                        '          <td data-label="Category">%s</td>\n          <td data-label="Region">%s</td>\n          <td data-label="Venue / Area">%s</td>\n'
                        '          <td data-label="Est. Attendance" class="note">%s</td>\n          <td data-label="Disruption Rating">%s</td>\n        </tr>' % (
                            r["date_text"], esc(r["name"]), esc(r["category"]), esc(r["region"]), esc(r["venue"]), esc(r["attendance"]), pill(r["rating"])))
    if not rows:
        rows.append('        <tr><td colspan="7" class="note">No events available this run. %s</td></tr>' % fresh(sec))
    end = today + timedelta(days=WINDOW_DAYS)
    shown = len(d["selected"]) if d else 0
    total = d["total_in_window"] if d else 0
    note = ('<p class="meta" style="margin-bottom:8px">Top %d events in the %s window, ranked by potential impact on travel (capacity first). Showing %d of %d found. '
            'Ticketmaster does not list every venue; Cycleboat comes from the manual list. %s</p>') % (TOP_EVENTS, fmt_range(today, end), shown, total, fresh(sec))
    return {"event_rows": "\n".join(rows), "events_note": note,
            "event_venues_json": js([{**p, "venue": esc(p["venue"]), "address": esc(p["address"]),
                                      "events": [{k: esc(v) for k, v in e.items()} for e in p["events"]]} for p in (d["pins"] if d else [])])}


# ---------------------------------------------------------------- protests (manual)
def protest_rows(pr, today):
    end = today + timedelta(days=WINDOW_DAYS)
    out = []
    for e in pr["events"]:
        if e.get("recurrence", "").startswith("weekly:"):
            wd = e["recurrence"].split(":")[1][:3].lower()
            days = [today + timedelta(days=i) for i in range(WINDOW_DAYS + 1) if (today + timedelta(days=i)).strftime("%a").lower() == wd]
            for d in days:
                out.append((d, e, "%s, %s %d<br>%s (weekly)" % (d.strftime("%a"), d.strftime("%b"), d.day, e["time"])))
        elif e.get("date") and today <= date.fromisoformat(e["date"]) <= end:
            d = date.fromisoformat(e["date"])
            out.append((d, e, "%s, %s %d<br>%s" % (d.strftime("%a"), d.strftime("%b"), d.day, e["time"])))
    return sorted(out, key=lambda x: x[0])


def render_protests(today):
    pr = json.loads((DATA_DIR / "protests.json").read_text())
    age = (today - date.fromisoformat(pr["reviewed_on"])).days
    stale = age > PROTEST_STALE_DAYS
    rows_data = protest_rows(pr, today)
    rows = ['        <tr>\n          <td data-label="Date(s)">%s</td>\n          <td data-label="Event" class="event-name">%s</td>\n          <td data-label="Category">%s</td>\n'
            '          <td data-label="Region">%s</td>\n          <td data-label="Venue / Area">%s</td>\n          <td data-label="Est. Attendance" class="note">%s</td>\n'
            '          <td data-label="Disruption Rating">%s</td>\n        </tr>' % (dt, esc(e["name"]), esc(e["category"]), esc(e["region"]), esc(e["venue"]), esc(e["attendance"]), pill(e["rating"]))
            for _, e, dt in rows_data]
    if not rows:
        rows = ['        <tr><td colspan="7" class="note">No protests confirmed for this window in the last manual review.</td></tr>']
    warn = ' <span class="pending">Stale: last reviewed %d days ago. Re-check the sources before relying on this section.</span>' % age if stale else ""
    meta = ('<p class="meta">Manually maintained list: no reliable protest feed exists, so the automation cannot refresh this section. Last reviewed %s. '
            'Sources checked: %s.%s</p>') % (fmt_day(date.fromisoformat(pr["reviewed_on"])), esc("; ".join(pr["sources_checked"])), warn)
    pins = [{"name": esc(e["name"]), "when": esc(re.sub("<br>", ", ", dt)), "note": esc(e["attendance"]), "lat": e["lat"], "lng": e["lng"]}
            for _, e, dt in rows_data if e.get("lat")]
    seen, uniq = set(), []
    for p in pins:
        if p["name"] not in seen:
            seen.add(p["name"]); uniq.append(p)
    return {"unrest_meta": meta, "unrest_rows": "\n".join(rows), "unrest_pins_json": js(uniq),
            "unrest_notes": '<p class="stamp">Ratings follow the Disruption Rating Rubric. Both small-group formats here are Low because density stays well under 2 people per m².</p>',
            "unrest_map_note": '<p class="stamp">Pins are approximate. Events with no single location (for example, statewide actions) are not pinned. Map tiles: Esri, with OpenStreetMap data.</p>'}, rows_data, pr


# ---------------------------------------------------------------- traffic / transit / crime
def render_traffic(tsec, msec):
    d = data_of(tsec)
    parts = []
    notes = " ".join(esc(n) for n in (d or {}).get("notes", []))
    parts.append('<p class="meta">Source: MassDOT roadway events feed and Connected Work Zones feed. %s MassDOT\'s feeds do not include every incident in Massachusetts, so a missing item does not mean a road is clear. '
                 'Chart shows items within %d miles of downtown; the map shows Greater Boston only. Use mass511.com for live conditions. %s</p>' % (fresh(tsec), CHART_RADIUS_MI, notes))
    items = (d or {}).get("items", [])

    def card(it):
        name = ("%s %s" % (it["road"], it["direction"])).strip() or it["desc"][:40]
        where = "Boston area" if it["boston"] else ("Outside Boston (%s mi)" % it["dist_mi"] if it["dist_mi"] is not None else "Location not provided")
        rep = "Reported: Started %s" % it["start_fmt"] if it.get("start_fmt") else "Reported: time not provided"
        if it.get("end_fmt"):
            rep += ". Ends: %s" % it["end_fmt"]
        return '      <div class="day"><div class="day-name">%s</div><div class="day-date">%s</div><div class="day-desc">%s</div><div class="precip">%s</div></div>' % (
            esc(name), esc(where), esc(it["desc"]), esc(rep))

    for kind in ("Incident", "Construction"):
        sel = [i for i in items if i["type"] == kind]
        parts.append('<h3 class="sub">%s (%d)</h3>' % (kind, len(sel)))
        parts.append('    <div class="forecast-grid">\n%s\n    </div>' % ("\n".join(card(i) for i in sel) if sel else '      <div class="day"><div class="day-desc">None reported by the MassDOT feeds.</div></div>'))
        if kind == "Incident":
            m = data_of(msec)
            if m is None:
                parts.append('<p class="meta" style="margin-top:12px">MBTA alerts not available this run. %s</p>' % fresh(msec))
            elif m["alerts"]:
                parts.append('<p class="meta" style="margin-top:12px">MBTA service alerts (subway and light rail, severity %d or higher, %d matching): %s %s</p>' % (
                    m["min_severity"], m["total_matching"], "; ".join("%s (%s)" % (esc(a["header"]), esc(", ".join(a["routes"]) or "system")) for a in m["alerts"][:4]), fresh(msec)))
            else:
                parts.append('<p class="meta" style="margin-top:12px">No active MBTA subway or light rail alerts at severity %d or higher. %s</p>' % (m["min_severity"], fresh(msec)))
    pins = [{"name": esc(("%s %s" % (i["road"], i["direction"])).strip() or i["desc"][:40]), "type": i["type"], "desc": esc(i["desc"]),
             "reported": esc("Started %s" % i["start_fmt"] if i.get("start_fmt") else "time not provided"), "delay": "Not provided",
             "end": esc(i["end_fmt"] or "Not provided"), "lat": i["lat"], "lng": i["lng"],
             "icon": "trafficIncidentIcon" if i["type"] == "Incident" else "constructionIcon", "boston": True}
            for i in items if i["boston"] and i["lat"] is not None]
    return {"traffic_body": "\n    ".join(parts), "traffic_items_json": js(pins),
            "traffic_map_note": '<p class="stamp">Pins use the coordinates supplied by the MassDOT feeds, not hand placement. Hover over a pin for details. Items without coordinates appear in the chart only.</p>'}, items


def render_crime(sec):
    d = data_of(sec)
    if not d:
        counts = {"d7": None, "d30": None, "d365": None, "asOf": "not available", "stamp": "Crime counts not available this run."}
        meta = '<p class="meta">Source: Analyze Boston crime incident reports. %s</p>' % fresh(sec)
    else:
        counts = {k: {"now": d[k]["now"], "prev": d[k]["prev"], "label": d[k]["label"]} for k in ("d7", "d30", "d365")}
        counts["asOf"] = d["through"]
        counts["stamp"] = "Through %s (complete days only). Source: %s." % (d["through"], d["source"])
        meta = ('<p class="meta">Source: %s. Counts are offense rows through %s, complete days only. These may differ from the BPD dashboard, '
                'which uses its own method and refresh timing. %s</p>') % (esc(d["source"]), esc(d["through"]), fresh(sec))
    return {"crime_meta": meta, "crime_counts_json": js(counts)}


# ---------------------------------------------------------------- glance, ticker, rubric, footer
def build_text(secs, today, rows_data, pr, ev_sel, traffic_items):
    end = today + timedelta(days=WINDOW_DAYS)
    glance = []
    w = data_of(secs["weather"])
    if w:
        days = [x for x in w["days"] if date.fromisoformat(x["date"]) >= today]
        highs = [x["high"] for x in days if x["high"] is not None]
        lows = [x["low"] for x in days if x["low"] is not None]
        wet = max(days, key=lambda x: x["precip"]) if days else None
        glance.append("<li><strong>Weather (%s):</strong> Highs %d to %d F, lows %d to %d F. Highest precipitation chance is %d%% on %s.%s</li>" % (
            fmt_range(today, end), min(highs), max(highs), min(lows), max(lows), wet["precip"], wet["dow"],
            (" NWS alert: %s." % esc(w["alerts"][0]["headline"] or w["alerts"][0]["event"])) if w.get("alerts") else "") if highs and lows and wet else "<li><strong>Weather:</strong> Forecast incomplete this run.</li>")
    else:
        glance.append("<li><strong>Weather:</strong> Not available this run.</li>")
    a = data_of(secs["air"])
    glance.append("<li><strong>Air quality:</strong> AQI %d (%s) per AirNow.</li>" % (a["aqi"], esc(a["category"])) if a else "<li><strong>Air quality:</strong> Not available this run.</li>")
    if ev_sel:
        med = [r for r in ev_sel if r["rating"] == "Medium"]
        top = ev_sel[0]
        glance.append("<li><strong>Events:</strong> %d of the top %d events are rated %s and %d are rated %s. Largest: %s at %s on %s.</li>" % (
            len(med), len(ev_sel), rating_word("Medium"), len(ev_sel) - len(med), rating_word("Low"), esc(top["name"]), esc(top["venue"]), top["date_text"].replace("<br>", ", ")))
    else:
        glance.append("<li><strong>Events:</strong> Event data not available this run.</li>")
    if rows_data:
        glance.append("<li><strong>Protests:</strong> %s (manual list, rated %s).</li>" % ("; ".join("%s, %s" % (esc(e["name"]), dt.replace("<br>", " ")) for _, e, dt in rows_data[:3]), rating_word("Low")))
    else:
        glance.append("<li><strong>Protests:</strong> None confirmed for this window in the last manual review (%s).</li>" % fmt_day(date.fromisoformat(pr["reviewed_on"])))
    inc = sum(1 for i in traffic_items if i["type"] == "Incident")
    con = sum(1 for i in traffic_items if i["type"] == "Construction")
    m = data_of(secs["transit"])
    glance.append("<li><strong>Transit and traffic:</strong> %s%s</li>" % (
        ("%d incidents and %d work zones within %d miles in MassDOT feeds. " % (inc, con, CHART_RADIUS_MI)) if data_of(secs["traffic"]) else "MassDOT traffic data not available this run. ",
        ("%d active MBTA subway alerts at severity %d or higher." % (m["total_matching"], m["min_severity"])) if m else "MBTA alerts not available this run."))

    ticker = []
    if a:
        ticker.append(["Air quality:", "Boston reads %s (AQI %d) per AirNow. %s" % (a["category"], a["aqi"], a["guidance"])])
    if w and days and highs:
        ticker.append(["Weather:", "Highs %d to %d F from %s, with precipitation chances up to %d%% (National Weather Service)." % (min(highs), max(highs), fmt_range(today, end), max(x["precip"] for x in days))])
        for al in w.get("alerts", [])[:2]:
            ticker.append(["NWS alert:", al["headline"] or al["event"]])
    if m and m["alerts"]:
        ticker.append(["Transit:", m["alerts"][0]["header"][:200]])
    for r in ev_sel[:3]:
        ticker.append(["Event:", "%s at %s, %s. Rated %s." % (r["name"], r["venue"], r["date_text"].replace("<br>", ", "), r["rating"])])
    for _, e, dt in rows_data[:1]:
        ticker.append(["Protest:", "%s, %s (manual list)." % (e["name"], dt.replace("<br>", " "))])
    ticker.append(["Travel window:", "Nov 8 to 9 is outside this edition. Refresh that picture about one month before travel."])
    bad = [n for n, s in secs.items() if s["status"] != "ok"]
    if bad:
        ticker.append(["Data notice:", "Showing last-known data for: %s. See the stale notes in each section." % ", ".join(bad)])
    return glance, [[esc(a_), esc(b_)] for a_, b_ in ticker]


def rubric_cells(ev_sel, rows_data):
    names = lambda lvl: [r["name"] for r in ev_sel if r["rating"] == lvl]
    low = names("Low") + [e["name"] for _, e, _ in rows_data]
    med = names("Medium")
    join = lambda xs, n=4: esc("; ".join(dict.fromkeys(xs[:n])) + ("; and %d more" % (len(xs) - n) if len(xs) > n else ""))
    return {"rubric_low": join(low) + "." if low else "None identified.",
            "rubric_medium": (join(med) + " (estimated at 2.0 to 3.0 people per m²; provisional until the venue confirms its exit plan).") if med else "None identified.",
            "rubric_high": "None identified. Ratings above Medium are assigned by a person, not by the automation. Re-rate any event that draws large crowds into a single corridor.",
            "rubric_vhigh": "None identified."}


def footer(secs, pr):
    stamp = fmt_ts(max((s["fetched_at"] for s in secs.values() if s.get("fetched_at")), default=None))
    return ('<p><strong>Sources (latest successful refresh %s):</strong> National Weather Service forecast; AirNow (EPA) air quality; MBTA V3 API alerts; MassDOT roadway events and Connected Work Zones feeds; '
            'Analyze Boston crime incident reports; Ticketmaster Discovery API; US Census Geocoder for venue pins. Map tiles from Esri and OpenStreetMap.</p>\n'
            '    <p><strong>Manual sections:</strong> Protest list (last reviewed %s) and Cycleboat. The automation cannot refresh these.</p>\n'
            '    <p><strong>Not covered:</strong> Live traffic conditions beyond the MassDOT feeds (check mass511.com), and any event Ticketmaster does not list.</p>') % (stamp, fmt_day(date.fromisoformat(pr["reviewed_on"])))


# ---------------------------------------------------------------- assembly + validation
class _Balance(HTMLParser):
    VOID = {"meta", "link", "br", "input", "img", "hr", "iframe"}

    def __init__(self):
        super().__init__()
        self.stack, self.err = [], []

    def handle_starttag(self, t, a):
        if t not in self.VOID:
            self.stack.append(t)

    def handle_endtag(self, t):
        if t in self.VOID:
            return
        if self.stack and self.stack[-1] == t:
            self.stack.pop()
        else:
            self.err.append((t, self.getpos()))


def validate(out):
    left = re.findall(r"\{\{\w+\}\}", out)
    if left:
        raise SystemExit("unfilled placeholders: %s" % sorted(set(left)))
    p = _Balance()
    p.feed(out)
    if p.stack or p.err:
        raise SystemExit("HTML tags unbalanced: unclosed=%s mismatched=%s" % (p.stack[:5], p.err[:5]))
    if shutil.which("node"):
        script = out.split("<script>")[-1].split("</script>")[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write("new Function(require('fs').readFileSync(process.argv[2],'utf8'))")
            runner = f.name
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            body = f.name
        r = subprocess.run(["node", runner, body], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("page script does not parse: %s" % r.stderr.strip()[:300])


def render(dash, today=None, template=None):
    now = datetime.fromisoformat(dash["generated_at"]).astimezone(TZ)
    today = today or now.date()
    secs = dash["sections"]
    tokens = {}
    tokens.update(render_weather(secs["weather"], today))
    tokens.update(render_air(secs["air"]))
    ev_tokens = render_events(secs["events"], today)
    tokens.update(ev_tokens)
    pr_tokens, rows_data, pr = render_protests(today)
    tokens.update(pr_tokens)
    tr_tokens, traffic_items = render_traffic(secs["traffic"], secs["transit"])
    tokens.update(tr_tokens)
    tokens.update(render_crime(secs["crime"]))
    ev_sel = (data_of(secs["events"]) or {}).get("selected", [])
    glance, ticker = build_text(secs, today, rows_data, pr, ev_sel, traffic_items)
    tokens.update(rubric_cells(ev_sel, rows_data))
    end = today + timedelta(days=WINDOW_DAYS)
    tokens.update({
        "snapshot_line": '<p class="meta">Data Snapshot: <strong>%s %d, %d</strong>. Updated %s. Refreshes every 6 hours (12:00 AM, 6:00 AM, 12:00 PM, and 6:00 PM ET). '
                         'Sections marked stale kept their last good data. Data can be refreshed upon request.</p>' % (
                             now.strftime("%B"), now.day, now.year, now.strftime("%I:%M %p ET").lstrip("0")),
        "glance_title": "Next 7 Days at a Glance (%s, %d)" % (fmt_range(today, end), end.year),
        "weather_title": "Weather: Boston (%s)" % fmt_range(today, end),
        "glance_items": "\n".join("      " + g for g in glance),
        "ticker_json": js(ticker),
        "footer_sources": footer(secs, pr),
    })
    out = (template or TEMPLATE.read_text(encoding="utf-8"))
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: tokens[m.group(1)], out)
    validate(out)
    return out


def main():
    dash = json.loads((DATA_DIR / "dashboard.json").read_text())
    out = render(dash)
    DIST_DIR.mkdir(exist_ok=True)
    (DIST_DIR / "index.html").write_text(out, encoding="utf-8")
    print("wrote %s (%d bytes)" % (DIST_DIR / "index.html", len(out)))


if __name__ == "__main__":
    sys.exit(main())
