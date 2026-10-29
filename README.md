# Boston Travel Risk Dashboard: automation

Refreshes the dashboard every 6 hours (12 AM, 6 AM, 12 PM, 6 PM Eastern) with GitHub Actions and publishes it to GitHub Pages.
Layout and CSS are unchanged from `boston_events_dashboard_v3.html`; only the data regions became placeholders in `templates/dashboard.template.html`.

## How it works
`src/sources/*` fetch one API each -> `src/build_data.py` runs them **fail-soft** and writes `data/dashboard.json` -> `src/render.py` fills the template and
**validates** the page (placeholders filled, tags balanced, script parses) -> the workflow commits `data/`, opens an issue if anything is stale, and deploys.
A source that fails keeps its last good data, is labelled **Stale** on the page with the error and the last-good time, and never blanks the page.

| Section | Source | Key |
|---|---|---|
| Weather | National Weather Service (api.weather.gov) | none, needs `CONTACT_EMAIL` |
| Air quality | AirNow, **2026 replacement services** (old endpoints were retired Sept 30, 2026) | `AIRNOW_API_KEY` |
| Transit alerts | MBTA V3 API (subway and light rail only) | `MBTA_API_KEY` (optional) |
| Traffic | MassDOT roadway events XML feed + Connected Work Zones feed | `MASSDOT_CWZ_URL`, `MASSDOT_CWZ_KEY` |
| Crime | Analyze Boston open data (or the BPD ArcGIS service if you set it) | none |
| Events | Ticketmaster Discovery API + `data/manual_events.json` | `TICKETMASTER_API_KEY` |
| Venue pins | US Census Geocoder (cached in `data/geocache.json`), traffic items use feed coordinates | none |
| Protests | **Manual**: `data/protests.json` (no reliable feed exists) | n/a |

## One-time setup
1. Put this folder in a GitHub repo. Settings -> Pages -> Source: **GitHub Actions**.
2. Get keys: AirNow `docs.airnowapi.org`, MBTA `api-v3.mbta.com`, Ticketmaster `developer.ticketmaster.com`, MassDOT work zones `api-app.massdot-swzm.com` (the portal shows the feed URL and how the key is sent).
3. Settings -> Secrets and variables -> Actions. **Secrets:** `AIRNOW_API_KEY`, `MBTA_API_KEY`, `TICKETMASTER_API_KEY`, `MASSDOT_CWZ_URL`, `MASSDOT_CWZ_KEY`. **Variables:** `CONTACT_EMAIL`, and if the CWZ portal says the key goes in the URL, `MASSDOT_CWZ_AUTH=query:<param name>` (default is header `x-api-key`).
4. Run the workflow once by hand (Actions -> Refresh dashboard -> Run workflow). Never paste keys into chat or commit them.

## First-run checklist (the payloads in `tests/fake_world.py` were written from documentation, not captured from live services)
- [ ] `data/run_summary.md`: every section OK? Fix any STALE/UNAVAILABLE first.
- [ ] **AirNow forecast**: if the summary says the forecast format was not recognized, read `data/raw/airnow_forecast_sample.json` and adjust `parse_forecast`.
- [ ] **MassDOT events feed**: open `data/raw/massdot_events_sample.xml`. Confirm incidents and coordinates parsed; adjust field names in `parse_events_xml` if not.
- [ ] **MassDOT CWZ**: confirm `MASSDOT_CWZ_AUTH` and that work zones appear.
- [ ] `data/venues.json`: Ticketmaster venue IDs filled in for all four venues; `geo_source` is `census`, not `hand-placed fallback`; open each pin on the map.
- [ ] Compare crime counts with the BPD dashboard. They will differ somewhat (see below).
- [ ] Review `data/geocache.json` in the first commit.

## Things that will look different from the hand-built version
- **Air quality labels** follow the EPA scale: AQI 51 is **Moderate**, not "Poor" (that was AccuWeather's scale).
- **Weather numbers** come from the NWS forecast, not AccuWeather, so they will not match exactly.
- **Traffic** comes from MassDOT, which says its events feed "does not include all incidents." It will not reproduce the Boston 25 list (no NH, CT, RI, or non-MassDOT incidents). Radii are set in `src/config.py`.
- **Crime** counts are offense rows from the open data file through the last complete day; the BPD dashboard uses its own method and timing.
- **Events** appear only if Ticketmaster lists them. Everything else needs a line in `data/manual_events.json`.
- **Ratings**: arena venues are Medium with the provisional density wording; everything else is Low. **High and Very High are never automatic.**

## Keeping manual sections honest
`data/protests.json` carries a `reviewed_on` date. After `PROTEST_STALE_DAYS` (7) the page shows a yellow "Stale" warning. Update the file after each review.

## Run locally
```
pip install -r requirements.txt
python -m pytest -q                 # offline, no keys
export AIRNOW_API_KEY=... TICKETMASTER_API_KEY=... CONTACT_EMAIL=you@example.com
python -m src.build_data && python -m src.render   # writes dist/index.html
```
`SAMPLE_preview_with_fake_data.html` shows the automated page built from the test data (all numbers fake, red banner at top).

## Not built yet
- Review gate (a pull request you approve before publishing). Today the page publishes automatically after validation.
- Claude-written summaries. Ticker and glance text are template-generated from the data, so numbers can't drift between runs.
- Protest automation, and any source outside the list above.
