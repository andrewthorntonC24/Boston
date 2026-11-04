import json
import shutil
from datetime import date, datetime, timezone

import pytest

from src import build_data, config, events, http, ics, render
from src.sources import airnow, crime, geocode, massdot, mbta, nws, protests, ticketmaster
from tests import fake_world as fw

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=config.TZ)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Copy data/ to a temp dir, point every module at it, and route all HTTP to the fake world."""
    data = tmp_path / "data"
    shutil.copytree(config.DATA_DIR, data, ignore=shutil.ignore_patterns("archive", "raw", "dashboard.json", "geocache.json", "run_summary.md"))
    for mod in (build_data, render):
        monkeypatch.setattr(mod, "DATA_DIR", data)
    monkeypatch.setattr(geocode, "CACHE", data / "geocache.json")
    monkeypatch.setattr(airnow, "RAW_DIR", data / "raw")
    monkeypatch.setattr(massdot, "RAW_DIR", data / "raw")
    monkeypatch.setattr(protests, "RAW_DIR", data / "raw")
    monkeypatch.setattr(http, "get_json", fw.route_json)
    monkeypatch.setattr(http, "get_text", fw.route_text)
    monkeypatch.setattr(http, "iter_lines", fw.iter_lines)
    monkeypatch.setattr(ticketmaster.time, "sleep", lambda s: None)
    for k, v in {"AIRNOW_API_KEY": "k", "TICKETMASTER_API_KEY": "k", "MBTA_API_KEY": "k",
                 "MASSDOT_CWZ_URL": "https://cwz.test/feed", "MASSDOT_CWZ_KEY": "k"}.items():
        monkeypatch.setenv(k, v)
    return data


# ------------------------------------------------------------------ source parsers
def test_nws_groups_day_and_night_into_cards():
    days = nws.group_periods(fw.nws_forecast()["properties"]["periods"])
    assert len(days) == 7 and days[0]["date"] == "2026-10-21"
    assert (days[0]["high"], days[0]["low"], days[2]["precip"]) == (58, 42, 10)


def test_airnow_new_schema_uses_epa_categories_and_normalizes_ozone():
    rows = airnow.parse_observations(fw.airnow_obs())
    assert {r["pollutant"] for r in rows} == {"PM2.5", "O3"}
    assert airnow.category(51) == "Moderate" and airnow.category(101) == "Unhealthy for Sensitive Groups"


def test_airnow_no_data_envelope_is_an_error_not_a_blank_reading():
    with pytest.raises(http.SourceError):
        airnow.parse_observations({"WebServiceError": [{"Message": "no data"}]})


def test_airnow_retired_endpoint_is_reported_clearly(monkeypatch):
    def gone(url, params=None, headers=None, timeout=30):
        raise http.SourceError("https://www.airnowapi.org/x returned 410 (endpoint retired)")
    monkeypatch.setattr(http, "get_json", gone)
    with pytest.raises(http.SourceError, match="410"):
        airnow.fetch(42.36, -71.06, "key")


def test_mbta_filters_by_severity_and_active_period(world):
    out = mbta.fetch("k", now=NOW)
    assert [a["id"] for a in out["alerts"]] == ["a1"]


def test_massdot_parsers_and_distance_filter():
    ev = massdot.parse_events_xml(fw.MASSDOT_XML)
    assert len(ev) == 3 and ev[0]["type"] == "Incident" and ev[1]["type"] == "Construction"
    items = massdot.annotate(ev + massdot.parse_wzdx(fw.WZDX), map_radius=15, chart_radius=30, chart_max=40)
    assert all("Springfield" not in i["desc"] for i in items), "item ~80 miles away must not reach the chart"
    assert items[0]["type"] == "Incident" and items[0]["boston"] is True


def test_crime_windows_use_complete_days_only():
    counts = crime.count_by_day(fw.iter_lines("x"))
    out, end, partial = crime.windows_from_counts(counts, fw.TODAY)
    assert partial is True and end.isoformat() == "2026-10-20"
    assert out["d7"] == {"now": 21, "prev": 21, "label": "Last 7 days"}


def test_census_geocoder_reads_x_as_longitude(world):
    lat, lng, src = geocode.geocode("100 Legends Way, Boston, MA 02114", {})
    assert (lat, lng, src) == (42.36, -71.06, "census")


# ------------------------------------------------------------------ events and ratings
def venues(world):
    return json.loads((world / "venues.json").read_text())


def test_event_selection_rating_and_exclusions(world):
    v = venues(world)
    tm = [ticketmaster._parse(e) for e in fw.tm_events()["_embedded"]["events"]]
    for slug, vid in {"td-garden": "V1", "house-of-blues-boston": "V2", "shubert-theatre": "V3", "somerville-theatre": "V4"}.items():
        v[slug]["tm_venue_id"] = vid
    rows, total = events.select_top(tm, v, [], fw.TODAY, 7, 10)
    names = [r["name"] for r in rows]
    assert "Far Future Show" not in names, "outside the window"
    arena = [r for r in rows if r["venue"] == "TD Garden"]
    assert arena and all(r["rating"] == "Medium" for r in arena)
    assert all(r["rating"] == "Low" for r in rows if r["venue"] != "TD Garden")
    assert rows[0]["venue"] == "TD Garden", "largest capacity ranks first"
    rod = next(r for r in rows if r["name"].startswith("Rod Wave"))
    assert "About 17,500 (venue capacity)" in rod["attendance"] and "Provisional until the venue confirms its exit plan." in rod["attendance"]
    celtics = next(r for r in rows if r["name"] == "Celtics vs. Knicks")
    assert "About 18,624" in celtics["attendance"]
    unknown = next(r for r in rows if r["name"] == "Indie Night")
    assert "not verified" in unknown["attendance"]


def test_ticketmaster_drops_parking_and_cancelled(world):
    res = ticketmaster.fetch("k", venues(world), NOW, NOW.replace(day=29))
    names = {e["name"] for e in res["events"]}
    assert "PARKING: Celtics vs. Knicks" not in names and "Cancelled Show" not in names


# ------------------------------------------------------------------ end to end
def run_all(world, **overrides):
    sources = build_data.default_sources(NOW)
    sources.update(overrides)
    return build_data.build(now=NOW, prev={}, sources=sources)


def test_end_to_end_all_sections_ok_and_page_validates(world):
    dash = run_all(world)
    assert {s["status"] for s in dash["sections"].values()} == {"ok"}, {k: v.get("error") for k, v in dash["sections"].items()}
    page = render.render(dash, today=fw.TODAY)
    assert "Data Snapshot: <strong>October 21, 2026</strong>" in page
    assert "Top 10 events" in page and "AQI 51" in page.replace("(51)", "AQI 51")
    assert 'class="pill medium">Medium' in page and "Provisional until the venue confirms its exit plan." in page
    pins = json.loads(page.split("const eventVenues = ")[1].split(";\n  function buildEventPopup")[0])
    td = next(p for p in pins if p["venue"] == "TD Garden")
    assert len(td["events"]) >= 2, "multi-event venues keep one pin with several events"
    assert not (world / "venues.json").read_text().count('"tm_venue_id": null'), "venue ids resolved and saved"


def test_failed_source_keeps_last_good_data_and_says_so(world):
    first = run_all(world)

    def boom():
        raise http.SourceError("HTTP 503 from https://api.weather.gov/points/x")
    second = build_data.build(now=NOW.replace(hour=12), prev=first, sources={**build_data.default_sources(NOW), "weather": boom})
    w = second["sections"]["weather"]
    assert w["status"] == "stale" and w["data"] == first["sections"]["weather"]["data"] and w["stale_since"] == first["sections"]["weather"]["fetched_at"]
    page = render.render(second, today=fw.TODAY)
    assert "Stale: showing last good data" in page and "503" in page


def test_source_with_no_history_renders_as_unavailable_not_blank(world):
    dash = run_all(world, crime=lambda: (_ for _ in ()).throw(http.SourceError("crime feed down")))
    assert dash["sections"]["crime"]["status"] == "unavailable"
    page = render.render(dash, today=fw.TODAY)
    assert "Not available" in page and "n/a" not in page.split("<script>")[0]
    assert "No data this run for crime" in page and "last-known" not in page.split("tickerItems")[1].split("];")[0]


def test_missing_api_key_is_a_clear_per_section_failure(world, monkeypatch):
    monkeypatch.delenv("AIRNOW_API_KEY")
    dash = build_data.build(now=NOW, prev={}, sources=build_data.default_sources(NOW))
    assert dash["sections"]["air"]["status"] == "unavailable" and "AIRNOW_API_KEY" in dash["sections"]["air"]["error"]
    render.render(dash, today=fw.TODAY)  # still produces a valid page


def test_feed_text_is_escaped_in_page_and_popups(world):
    page = render.render(run_all(world), today=fw.TODAY)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_protest_list_is_flagged_stale_when_review_is_old(world):
    p = json.loads((world / "protests.json").read_text())
    p["reviewed_on"] = "2026-10-01"
    (world / "protests.json").write_text(json.dumps(p))
    page = render.render(run_all(world), today=fw.TODAY)
    assert "Stale: the hand-reviewed sources were last checked 20 days ago" in page


def test_streaming_download_over_real_http_with_gzip(monkeypatch):
    """Regression: the live run failed with 'I/O operation on closed file'. This serves a gzipped CSV from a
    local server and reads it with the real http.iter_lines (nothing patched)."""
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    body = gzip.compress(fw.crime_csv().encode())

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        counts = crime.count_by_day(http.iter_lines("http://127.0.0.1:%d/crime.csv" % srv.server_address[1]))
    finally:
        srv.shutdown()
    assert sum(counts.values()) == len(fw.crime_csv().splitlines()) - 1


def test_rejected_key_message_is_actionable(monkeypatch):
    class R:
        status_code = 401
    monkeypatch.setattr(http._session, "get", lambda *a, **k: R())
    with pytest.raises(http.SourceError, match="rejected the API key"):
        http.get_json("https://app.ticketmaster.com/discovery/v2/venues.json", params={"apikey": "secret"})


def test_env_strips_pasted_whitespace(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "abc123 \n")
    assert config.env("TICKETMASTER_API_KEY") == "abc123"


def test_airnow_forecast_parses_the_real_captured_payload():
    fc = airnow.parse_forecast(fw.AIRNOW_FORECAST_REAL)
    assert [(f["date"], f["aqi"], f["category"]) for f in fc] == [
        ("2026-10-29", 50, "Good"), ("2026-10-30", 50, "Good"), ("2026-10-31", 40, "Good")], "dateValid is the forecast day, not dateIssue"
    assert fc[0]["agency"] == "Massachusetts Dept. of Environmental Protection"


def test_airnow_forecast_keeps_worst_pollutant_and_category_only_rows():
    rows = [{"dateValid": "2026-11-01", "aqi": 40, "categoryNumber": 1, "categoryName": "Good", "parameterName": "O3"},
            {"dateValid": "2026-11-01", "aqi": 88, "categoryNumber": 2, "categoryName": "Moderate", "parameterName": "PM2.5"},
            {"dateValid": "2026-11-02", "aqi": -1, "categoryNumber": 2, "categoryName": "Moderate", "parameterName": "PM2.5"}]
    fc = airnow.parse_forecast(rows)
    assert (fc[0]["aqi"], fc[0]["category"], fc[0]["pollutant"]) == (88, "Moderate", "PM2.5")
    assert (fc[1]["aqi"], fc[1]["category"]) == (None, "Moderate")


def test_air_section_shows_forecast_days_after_today_and_names_the_forecaster(world):
    page = render.render(run_all(world), today=fw.TODAY)
    # fake TODAY is Oct 21 but the captured forecast is Oct 29-31, all after today, so all three appear
    assert "Forecast (Massachusetts Dept. of Environmental Protection)" in page and "Oct 31" in page


def test_snapshot_line_wording_and_disclaimer_are_on_the_page(world):
    page = render.render(run_all(world), today=fw.TODAY)
    assert ("Data Snapshot: <strong>October 21, 2026</strong>. Updated 6:00 AM ET. Data is refreshed every 6 hours "
            "(12:00 AM, 6:00 AM, 12:00 PM, and 6:00 PM ET). Sections marked stale kept their last good data. "
            "Data can be refreshed upon request.") in page
    assert "Distributed every 6 hours" not in page
    footer = page.split("<footer>")[1].split("</footer>")[0]
    assert footer.strip().endswith("or cause others to take because of its use or misuse.</p>"), "disclaimer is the last item in the footer"
    assert "Crisis24 does not guarantee the accuracy or completeness of this report" in footer


def test_page_asks_search_engines_not_to_index_it(world):
    page = render.render(run_all(world), today=fw.TODAY)
    head = page.split("</head>")[0]
    assert '<meta name="robots" content="noindex, nofollow, noarchive">' in head



# ------------------------------------------------------------------ protest feeds
TODAY_P = date(2026, 11, 4)
MAPA = {"id": "mapa", "name": "Massachusetts Peace Action calendar", "url": "https://masspeaceaction.org/events/list/?ical=1"}
XR = {"id": "xr", "name": "Extinction Rebellion Boston calendar", "url": "https://xrboston.org/action/ical/x"}


def protest_manual(world):
    return json.loads((world / "protests.json").read_text())


def test_ics_reads_the_real_mapa_capture():
    evs = ics.parse_ics((fw.FIX / "mapa_sample_abridged.ics").read_text(encoding="utf-8"))
    assert len(evs) == 8
    j4a = next(e for e in evs if e["summary"] == "Justice 4 All Thursdays")
    assert (j4a["start"].date(), j4a["start"].hour, j4a["end"].hour) == (date(2026, 11, 5), 11, 13)
    assert "Greater Assabet Indivisible Network (GAIN)" in j4a["description"] and "\\n" not in j4a["description"] and "\\," not in j4a["description"] and "\n" in j4a["description"]
    assert "any other political views, you are welcome to join us" in j4a["description"], "escaped commas are restored"
    wired = next(e for e in evs if e["summary"].startswith("Wired to Steal"))
    assert wired["geo"] == (42.3506456, -71.0763255)
    open_house = next(e for e in evs if e["summary"] == "Peace Open House")
    assert open_house["description"].startswith("We are Massachusetts Peace Action (MAPA), the largest"), "folded lines are rejoined"


def test_real_mapa_listings_are_split_into_demonstrations_and_everything_else(world):
    man = protest_manual(world)
    text = (fw.FIX / "mapa_sample_abridged.ics").read_text(encoding="utf-8")
    evs, skipped, total = protests.parse_feed(text, MAPA, TODAY_P, {}, man["known_places"], {})
    assert [e["name"] for e in evs] == ["Justice 4 All Thursdays", "No Fear with Street Theater", "Bearing Witness @ ICE"]
    assert all(e["rating"] == "Low" and e["lat"] and e["region"] == "Burlington, MA" for e in evs)
    reason = lambda prefix: next(s["reason"] for s in skipped if s["name"].startswith(prefix))
    assert reason("The Ukraine War Now") == "online event" and reason("EARTH'S GREATEST") == "online event"
    assert reason("Boiling Point:") == "no demonstration signal in the listing"
    assert "Peace Open House" not in " ".join(s["name"] for s in skipped), "Nov 12 is outside the 7-day window"
    assert total == 8


def test_xr_style_feed_handles_utc_all_day_and_cancelled(world):
    text = (fw.FIX / "xr_sample_synthetic.ics").read_text(encoding="utf-8")
    evs, skipped, total = protests.parse_feed(text, XR, TODAY_P, {}, protest_manual(world)["known_places"], {"Boston Common, Boston, MA": {"lat": 42.355, "lng": -71.0656, "source": "t"}})
    assert [e["name"] for e in evs] == ["Banner Drop and Rally"] and evs[0]["time_text"] == "All day"
    names = {s["name"]: s["reason"] for s in skipped}
    assert names["November General Meeting!"].startswith("talk, film, meeting")
    assert names["Virtual Book Club: What If We Get It Right? Part Four"] == "online event"
    assert total == 3, "the cancelled event is not counted"
    meeting = next(e for e in ics.parse_ics(text) if e["summary"] == "November General Meeting!")
    assert meeting["start"].hour == 16 and meeting["start"].date() == date(2026, 11, 8), "21:00Z is 4 PM Eastern"


def test_weekly_recurrence_is_expanded_and_exdates_respected():
    base = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:w\nSUMMARY:Weekly Vigil\nDTSTART;TZID=America/New_York:20261007T110000\nDTEND;TZID=America/New_York:20261007T130000\n%s\nEND:VEVENT\nEND:VCALENDAR\n"
    ev = ics.parse_ics(base % "RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20261231T000000Z")[0]
    days = [o[0].date() for o in ics.occurrences(ev, date(2026, 11, 4), date(2026, 11, 11))]
    assert days == [date(2026, 11, 4), date(2026, 11, 11)]
    ev2 = ics.parse_ics(base % "RRULE:FREQ=WEEKLY;BYDAY=WE\nEXDATE;TZID=America/New_York:20261111T110000")[0]
    assert [o[0].date() for o in ics.occurrences(ev2, date(2026, 11, 4), date(2026, 11, 11))] == [date(2026, 11, 4)]
    ev3 = ics.parse_ics(base % "RRULE:FREQ=MONTHLY;BYDAY=1WE")[0]
    ics.occurrences(ev3, date(2026, 11, 1), date(2026, 11, 30))
    assert ev3.get("rrule_unsupported") is True, "unsupported rules are flagged, not silently expanded wrong"


def test_one_failing_feed_does_not_blank_the_other(world, monkeypatch):
    def route(url, params=None, headers=None, timeout=30):
        if "xrboston.org" in url:
            raise http.SourceError("HTTP 503 from https://xrboston.org/action/ical/x")
        return fw.route_text(url)
    monkeypatch.setattr(http, "get_text", route)
    out = protests.fetch(TODAY_P, [MAPA, XR], protest_manual(world))
    assert [f["status"] for f in out["feeds"]] == ["ok", "error"] and len(out["events"]) == 3
    monkeypatch.setattr(http, "get_text", lambda *a, **k: (_ for _ in ()).throw(http.SourceError("down")))
    with pytest.raises(http.SourceError, match="every protest feed failed"):
        protests.fetch(TODAY_P, [MAPA, XR], protest_manual(world))


def test_non_calendar_response_is_rejected_with_a_clear_message(world, monkeypatch):
    monkeypatch.setattr(http, "get_text", lambda *a, **k: "<html>Please enable JavaScript</html>")
    with pytest.raises(http.SourceError, match="not a calendar file"):
        protests.fetch(TODAY_P, [MAPA], protest_manual(world))


def test_feed_and_hand_kept_entries_are_merged_without_duplicates(world):
    man = protest_manual(world)
    text = (fw.FIX / "mapa_sample_abridged.ics").read_text(encoding="utf-8")
    evs, skipped, _ = protests.parse_feed(text, MAPA, TODAY_P, {}, man["known_places"], {})
    rows = render.merge_protests(man, {"events": evs}, TODAY_P)
    got = [(r[0].isoformat(), r[1]["name"], r[1]["source_label"]) for r in rows]
    assert got == [("2026-11-04", "Bearing Witness @ ICE", "hand-kept list"),
                   ("2026-11-05", "Justice 4 All Thursdays", "Massachusetts Peace Action calendar and hand-kept list"),
                   ("2026-11-07", "No Fear with Street Theater", "Massachusetts Peace Action calendar and hand-kept list"),
                   ("2026-11-11", "Bearing Witness @ ICE", "Massachusetts Peace Action calendar and hand-kept list")], got


def test_page_shows_both_protest_sources_and_the_not_counted_note(world):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=config.TZ)
    dash = build_data.build(now=now, prev={}, sources=build_data.default_sources(now))
    assert dash["sections"]["protests"]["status"] == "ok", dash["sections"]["protests"].get("error")
    page = render.render(dash, today=TODAY_P)
    unrest = page.split('id="unrest-title"')[1].split('id="traffic-title"')[0]
    assert "Massachusetts Peace Action calendar: OK, 8 listings" in unrest and "Extinction Rebellion Boston calendar: OK, 3 listings" in unrest
    assert "Justice 4 All Thursdays" in unrest and "Source: Massachusetts Peace Action calendar and hand-kept list." in unrest
    assert "not counted as demonstrations" in unrest and "Boiling Point" in unrest and "November General Meeting!" in unrest
    assert "Mass 50501" in unrest and "Boston.gov traffic advisory" in unrest and "last full review Nov 4" in unrest
    assert "Data is refreshed every 6 hours" in page


def test_page_survives_the_feeds_failing_and_uses_the_hand_kept_list(world, monkeypatch):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=config.TZ)
    srcs = build_data.default_sources(now)
    srcs["protests"] = lambda: (_ for _ in ()).throw(http.SourceError("both calendars unreachable"))
    dash = build_data.build(now=now, prev={}, sources=srcs)
    assert dash["sections"]["protests"]["status"] == "unavailable"
    unrest = render.render(dash, today=TODAY_P).split('id="unrest-title"')[1].split('id="traffic-title"')[0]
    assert "Not available: both calendars unreachable" in unrest and "Bearing Witness @ ICE" in unrest and "Justice 4 All Thursdays" in unrest


def test_calendar_text_cannot_inject_markup(world):
    evil = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Rally <script>alert(1)</script>\nDTSTART;TZID=America/New_York:20261105T120000\n"
            "LOCATION:1000 District Avenue Burlington\\, MA\nEND:VEVENT\nEND:VCALENDAR\n")
    evs, _, _ = protests.parse_feed(evil, MAPA, TODAY_P, {}, protest_manual(world)["known_places"], {})
    now = datetime(2026, 11, 4, 12, 0, tzinfo=config.TZ)
    srcs = build_data.default_sources(now)
    srcs["protests"] = lambda: {"source": "t", "feeds": [{"id": "mapa", "name": "T", "status": "ok", "listed": 1, "error": None}], "events": evs, "skipped": [], "notes": []}
    page = render.render(build_data.build(now=now, prev={}, sources=srcs), today=TODAY_P)
    assert "<script>alert(1)</script>" not in page and "Rally &lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_ticker_distinguishes_stale_sections_from_sections_with_no_data(world):
    first = run_all(world)
    srcs = build_data.default_sources(NOW)
    srcs["weather"] = lambda: (_ for _ in ()).throw(http.SourceError("503"))
    srcs["crime"] = lambda: (_ for _ in ()).throw(http.SourceError("down"))
    prev = {"sections": {"weather": first["sections"]["weather"]}}      # weather has history, crime does not
    page = render.render(build_data.build(now=NOW, prev=prev, sources=srcs), today=fw.TODAY)
    assert "Showing last-known data for weather; no data this run for crime." in page


def tp2b_text():
    return (fw.FIX / "tp2b_sample_listings.ics").read_text(encoding="utf-8")


TP2B = {"id": "tp2b", "name": "Turn Purple 2 Blue statewide calendar", "url_template": "https://turnpurple2blue.org/events/week/{date}/?ical=1"}


def test_week_urls_cover_this_week_and_next_with_sunday_starts():
    urls = protests.week_urls(TP2B["url_template"], date(2026, 11, 4))
    assert urls == ["https://turnpurple2blue.org/events/week/2026-11-01/?ical=1", "https://turnpurple2blue.org/events/week/2026-11-08/?ical=1"]
    assert protests.week_urls(TP2B["url_template"], date(2026, 11, 8))[0].endswith("2026-11-08/?ical=1"), "a Sunday starts its own week"


def test_tp2b_listings_are_classified_and_limited_to_the_boston_area(world):
    evs, skipped, total = protests.parse_feed(tp2b_text(), TP2B, TODAY_P, {}, protest_manual(world)["known_places"], {})
    names = [e["name"] for e in evs]
    assert "Brookline Rally for Democracy, November 2026" in names and "Malden Democrats Stand Out for Democracy" in names
    assert "De-ICE Hanscom Friday Standouts" in names and "Defend Democracy Danvers Peabody" in names, "'with posters, banners' counts as a demonstration signal"
    for far in ("Greater Methuen", "Peace, Justice", "Rally for Democracy", "GAIN Weekly Northborough"):
        assert not any(n.startswith(far) and "Brookline" not in n for n in names), far + " is outside 20 miles"
    reasons = {s["name"]: s["reason"] for s in skipped}
    assert reasons["Indivisible LAB Daytime Action Hours"].startswith("talk, film, meeting"), "'action hours' must not look like an action"
    assert reasons["First Friday Postcard Party"].startswith("talk, film, meeting") and "Voter Protection calls to Cure Ballots-various shifts" in reasons
    assert total == 17


def test_duplicate_listings_of_one_vigil_collapse_to_one_row_but_a_later_rally_does_not(world):
    man = protest_manual(world)
    mapa, _, _ = protests.parse_feed((fw.FIX / "mapa_sample_abridged.ics").read_text(encoding="utf-8"), MAPA, TODAY_P, {}, man["known_places"], {})
    tp, _, _ = protests.parse_feed(tp2b_text(), TP2B, TODAY_P, {}, man["known_places"], {})
    rows = render.merge_protests(man, {"events": mapa + tp}, TODAY_P)
    bw = [(r[0].isoformat(), r[1]["name"], r[1]["source_label"]) for r in rows if "Bear" in r[1]["name"]]
    assert bw == [("2026-11-04", "Bearing Witness @ ICE", "Turn Purple 2 Blue statewide calendar and hand-kept list"),
                  ("2026-11-11", "Bearing Witness @ ICE", "Massachusetts Peace Action calendar and hand-kept list")], bw
    nofear = [r for r in rows if r[0].isoformat() == "2026-11-07" and r[1]["name"] == "No Fear with Street Theater"]
    assert len(nofear) == 1 and nofear[0][1]["source_label"] == "Massachusetts Peace Action calendar, Turn Purple 2 Blue statewide calendar and hand-kept list"
    extra = {"name": "Noon Rally at the ICE office", "date": "2026-11-05", "time_text": "12:00 PM to 1:00 PM", "sort_time": "12:00", "category": "Rally",
             "region": "Burlington, MA", "venue": "1000 District Avenue", "attendance": "x", "rating": "Low", "lat": 42.4885, "lng": -71.2175,
             "source_name": "Test calendar", "id": "x"}
    rows2 = render.merge_protests(man, {"events": [extra]}, TODAY_P)
    assert any(r[1]["name"] == "Noon Rally at the ICE office" for r in rows2), "a different event an hour into the vigil stays visible"


def test_a_week_that_fails_is_reported_without_losing_the_other_week(world, monkeypatch):
    def route(url, params=None, headers=None, timeout=30):
        if "2026-11-08" in url:
            raise http.SourceError("HTTP 503 from https://turnpurple2blue.org/events/week/2026-11-08/")
        return tp2b_text()
    monkeypatch.setattr(http, "get_text", route)
    out = protests.fetch(TODAY_P, [TP2B], protest_manual(world))
    assert out["feeds"][0]["status"] == "ok" and any("1 of 2 calendar files could not be read" in n for n in out["notes"])
    assert out["events"], "this week's events survive"


def test_no_findaprotest_anywhere_in_the_page(world):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=config.TZ)
    page = render.render(build_data.build(now=now, prev={}, sources=build_data.default_sources(now)), today=TODAY_P)
    assert "findaprotest" not in page.lower()
    unrest = page.split('id="unrest-title"')[1].split('id="traffic-title"')[0]
    assert "Turn Purple 2 Blue statewide calendar: OK" in unrest and "Brookline Rally for Democracy" in unrest


def test_feed_read_by_hand_is_labelled_as_such_not_as_a_failure(world):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=config.TZ)
    srcs = build_data.default_sources(now)
    real = protests.fetch(TODAY_P, [MAPA], protest_manual(world))
    real["feeds"].append({"id": "xr", "name": "Extinction Rebellion Boston calendar", "status": "hand", "listed": 0, "error": "its page was read by hand on Nov 4"})
    srcs["protests"] = lambda: real
    unrest = render.render(build_data.build(now=now, prev={}, sources=srcs), today=TODAY_P).split('id="unrest-title"')[1].split('id="traffic-title"')[0]
    assert "Extinction Rebellion Boston calendar: read by hand (its page was read by hand on Nov 4)" in unrest
    assert "failed" not in unrest.split("<table")[0]


def test_a_demonstration_counted_by_one_calendar_is_never_listed_as_not_counted(world):
    man = protest_manual(world)
    counted = {"id": "a", "name": "Justice 4 All Thursdays", "date": "2026-11-05", "time_text": "11:00 AM to 1:00 PM", "sort_time": "11:00", "category": "Vigil / protest",
               "region": "Burlington, MA", "venue": "1000 District Avenue", "attendance": "x", "rating": "Low", "lat": 42.4885, "lng": -71.2175, "source_name": "Calendar A"}
    skipped = [{"name": "Justice 4 All Thursdays", "date": "2026-11-05", "reason": "no demonstration signal in the listing", "source": "Calendar B"},
               {"name": "Some Book Talk", "date": "2026-11-06", "reason": "talk, film, meeting, or training", "source": "Calendar B"}]
    tokens, _, _ = render.render_protests(TODAY_P, {"status": "ok", "fetched_at": "2026-11-04T12:00:00-05:00", "data": {"feeds": [], "events": [counted], "skipped": skipped, "notes": []}})
    assert "Some Book Talk" in tokens["unrest_notes"] and "Justice 4 All Thursdays" not in tokens["unrest_notes"]
