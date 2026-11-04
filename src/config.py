"""Central configuration. Secrets come from environment variables (GitHub Actions secrets),
never from files in the repo."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DIST_DIR = ROOT / "dist"
TEMPLATE = ROOT / "templates" / "dashboard.template.html"

TZ = ZoneInfo("America/New_York")
CENTER = (42.3601, -71.0589)          # downtown Boston, used for distance filters
WINDOW_DAYS = 7                        # "next 7 days" window for events, weather, glance
TOP_EVENTS = 10                        # events table size (your rule: always top 10)

# Distance filters for traffic items (miles from CENTER). The chart shows a wider area than the map.
MAP_RADIUS_MI = float(os.getenv("MAP_RADIUS_MI", "15"))
CHART_RADIUS_MI = float(os.getenv("CHART_RADIUS_MI", "30"))
CHART_MAX_ITEMS = int(os.getenv("CHART_MAX_ITEMS", "40"))

# Protest listings are kept to the Greater Boston area (miles from CENTER). Statewide calendars list many towns
# farther out; those are counted under "outside the Boston-area radius" and left off the table.
PROTEST_RADIUS_MI = float(os.getenv("PROTEST_RADIUS_MI", "20"))

# Manual sections are flagged stale after this many days without re-confirmation.
PROTEST_STALE_DAYS = int(os.getenv("PROTEST_STALE_DAYS", "7"))

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")  # required by the NWS terms; set it as a secret/variable
USER_AGENT = "BostonTravelRiskDashboard/1.0 (%s)" % (CONTACT_EMAIL or "set CONTACT_EMAIL")


def env(name, default=None):
    v = (os.getenv(name) or "").strip()      # a pasted key often carries a trailing space or newline
    return v if v else default


# Organizer calendar feeds read on every run (see src/sources/protests.py). Override a URL with the named variable
# if a site moves its export. The MAPA feed was confirmed from a real response; the XR Boston feed has not been seen.
PROTEST_FEEDS = [
    {"id": "mapa", "name": "Massachusetts Peace Action calendar",
     "url": env("MAPA_ICAL_URL", "https://masspeaceaction.org/events/list/?hide_subsequent_recurrences=1&ical=1")},
    {"id": "xr", "name": "Extinction Rebellion Boston calendar",
     "url": env("XR_ICAL_URL", "https://xrboston.org/action/ical/XR%20Mass%20Events?")},
    # Statewide Indivisible calendar (The Events Calendar plugin). The page's own "Export .ics file" link is a week-view
    # export, so the pipeline reads this week and next week (see src/sources/protests.py).
    {"id": "tp2b", "name": "Turn Purple 2 Blue statewide calendar",
     "url_template": env("TP2B_ICAL_TEMPLATE", "https://turnpurple2blue.org/events/week/{date}/?ical=1")},
]
