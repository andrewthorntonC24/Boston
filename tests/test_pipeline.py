import json
import shutil
from datetime import datetime, timezone

import pytest

from src import build_data, config, events, http, render
from src.sources import airnow, crime, geocode, massdot, mbta, nws, ticketmaster
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
    monkeypatch.setattr(http, "get_json", fw.route_json)
    monkeypatch.setattr(http, "get_text", fw.route_text)
    monkeypatch.setattr(http, "open_text", fw.open_text)
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
    counts = crime.count_by_day(fw.open_text("x"))
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
    assert "Showing last-known data for: crime" in page


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
    assert "Stale: last reviewed 20 days ago" in page
