"""Event selection, rating, and ranking. Pure functions (no network), so they are easy to test.

RATING RULES (carried over from the rubric and your approved wording):
  * Arena-class venue (type "arena", e.g. TD Garden): Medium, with the density estimate stored in
    data/venues.json, always labelled provisional until the venue confirms its exit plan.
  * Every other venue: Low, using the capacity text you supplied for that venue. Seated venues never get a
    standing-density estimate.
  * Venue not in data/venues.json: Low, flagged "capacity not verified".
  * High and Very High are never assigned automatically. A human has to raise a rating.
The density figure is a rule of thumb attached to a venue class, not a measurement."""
import re
from datetime import date, datetime, timedelta


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def match_venue(ev, venues):
    name = norm(ev.get("venue_name"))
    for slug, v in venues.items():
        if ev.get("venue_id") and ev["venue_id"] == v.get("tm_venue_id"):
            return slug, v
        if any(a in name for a in v.get("aliases", [])):
            return slug, v
    return None, None


def kind(ev):
    t = "%s %s" % (ev.get("name", ""), ev.get("genre") or "")
    t = t.lower()
    if "bruins" in t or "hockey" in t:
        return "hockey"
    if "celtics" in t or "basketball" in t:
        return "basketball"
    if ev.get("segment") == "Music":
        return "concert"
    return "default"


def category_label(ev):
    seg, genre = ev.get("segment"), ev.get("genre")
    if seg == "Sports":
        return "%s%s" % (genre or "Sports", " (NHL)" if kind(ev) == "hockey" else " (NBA)" if kind(ev) == "basketball" else "")
    if seg == "Music":
        return "Concert (%s)" % genre if genre and genre != "Undefined" else "Concert"
    return seg or "Event"


def assess(ev, venue):
    """Return (rating, attendance_text, rank_value)."""
    if venue is None:
        return "Low", "Venue capacity not verified. Rated Low provisionally; add the venue to data/venues.json to improve this estimate.", 300
    k = kind(ev)
    caps = venue.get("capacity", {})
    cap = caps.get(k) or caps.get("default") or 300
    if venue.get("type") == "arena":
        label = (venue.get("capacity_label") or {}).get(k) or (venue.get("capacity_label") or {}).get("default") or "venue capacity"
        extra = (" " + venue["capacity_note"]) if k == "concert" and venue.get("capacity_note") else ""
        text = ("About {cap:,} ({label}).{extra} Exit-zone density estimated at {d} people per m² as the crowd exits "
                "through a few gates within about an hour. Provisional until the venue confirms its exit plan.").format(
            cap=cap, label=label, extra=extra, d=venue.get("density_estimate", "not estimated"))
        return "Medium", text, cap
    return "Low", venue.get("capacity_text", "Capacity not provided."), cap


def when_text(ev):
    d = date.fromisoformat(ev["date"])
    t = ""
    if ev.get("time"):
        t = datetime.strptime(ev["time"][:5], "%H:%M").strftime("%I:%M %p").lstrip("0")
    return "%s, %s %d" % (d.strftime("%a"), d.strftime("%b"), d.day), t


def select_top(tm_events, venues, manual, today, window_days, n):
    end = today + timedelta(days=window_days)
    rows = []
    for ev in tm_events:
        d = date.fromisoformat(ev["date"])
        if not (today <= d <= end):
            continue
        slug, v = match_venue(ev, venues)
        rating, text, rank = assess(ev, v)
        day, tm = when_text(ev)
        status = (ev.get("status") or "").lower()
        if status in ("postponed", "rescheduled"):
            text = "Listed as %s by Ticketmaster; confirm the date. %s" % (status, text)
        rows.append({"name": ev["name"], "date": ev["date"], "time": ev.get("time") or "", "date_text": day + ("<br>" + tm if tm else ""),
                     "category": category_label(ev), "region": (v or {}).get("region") or ("%s, MA" % ev.get("city") if ev.get("city") else "Boston area"),
                     "venue": (v or {}).get("name") or ev.get("venue_name") or "Venue not listed", "address": (v or {}).get("address") or ev.get("address"),
                     "venue_slug": slug or "tm-%s" % (ev.get("venue_id") or norm(ev.get("venue_name"))), "attendance": text, "rating": rating,
                     "rank": rank, "lat": ev.get("lat"), "lng": ev.get("lng"), "source": "Ticketmaster", "url": ev.get("url")})
    for m in manual:
        rows.append({"name": m["name"], "date": today.isoformat() if m.get("always_in_window") else m.get("date", ""), "time": "",
                     "date_text": m.get("date_text") or m.get("date", ""), "category": m["category"], "region": m["region"], "venue": m["venue"],
                     "address": m.get("address"), "venue_slug": "manual-%s" % m["id"], "attendance": m["attendance"], "rating": m["rating"],
                     "rank": m.get("rank_value", 10), "lat": m.get("lat"), "lng": m.get("lng"), "source": "manual (confirmed %s)" % m.get("confirmed_on"), "url": None})
    rows.sort(key=lambda r: (-r["rank"], r["date"], r["time"]))
    return rows[:n], len(rows)


def build_pins(selected, venues, center, radius_mi, miles):
    """One pin per venue; each pin lists its events. Coordinates: venues.json (geocoded), else the event's own."""
    by = {}
    for r in selected:
        by.setdefault(r["venue_slug"], []).append(r)
    pins = []
    for slug, rows in by.items():
        v = venues.get(slug) or {}
        lat, lng = v.get("lat") or rows[0].get("lat") or v.get("fallback_lat"), v.get("lng") or rows[0].get("lng") or v.get("fallback_lng")
        if lat is None or lng is None or miles(center[0], center[1], lat, lng) > radius_mi:
            continue
        pins.append({"venue": rows[0]["venue"], "address": rows[0].get("address") or "", "lat": lat, "lng": lng,
                     "events": [{"name": r["name"], "when": (r["date_text"].replace("<br>", ", ")), "category": r["category"],
                                 "rating": r["rating"], "note": r["attendance"]} for r in sorted(rows, key=lambda r: (r["date"], r["time"]))]})
    return pins
