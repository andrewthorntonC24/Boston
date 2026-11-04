"""Protest and demonstration listings from organizers' public calendar feeds.

Feeds (configured in src/config.py):
  * Massachusetts Peace Action (The Events Calendar plugin): CONFIRMED from a real response on 2026-11-04.
    Caveat: the feed URL uses hide_subsequent_recurrences=1, so a weekly series appears ONCE (its next date).
    A second date of the same series inside the 7-day window can be missing, which is why the hand-kept
    weekly entries in data/protests.json stay as a backup (duplicates are merged).
  * Turn Purple 2 Blue (statewide Indivisible calendar, same plugin): its page offers a week-view ".ics" export
    (/events/week/YYYY-MM-DD/?ical=1). The listings were read from the real pages on 2026-11-04; the .ics
    files themselves were seen only through search-result excerpts (standard fields, GEO and CATEGORIES present).
    This week and next week are read on every run.
  * Extinction Rebellion Boston: the page advertises an iCal export; its contents have NOT been seen, so it is
    parsed with the same tolerant reader and a raw sample is saved on first run (data/raw/xr_ical_sample.ics).
Calendars mix demonstrations with talks, films, meetings, and online events, so every listing is classified. Only
in-person demonstrations inside PROTEST_RADIUS_MI of downtown are rated; the rest are listed under "not counted".
Not covered: Mass 50501, Boston Indivisible, and No Kings Boston are reviewed by hand. The Boston Protest List and
protest.boston could not be found or read."""
import re
import time
from datetime import date, datetime, timedelta

from .. import http, ics
from ..config import CENTER, PROTEST_RADIUS_MI, RAW_DIR, WINDOW_DAYS
from . import geocode, massdot

# Strong signals: looked for in the title, categories, and the start of the description.
STRONG = re.compile(r"protest|rally|rallies|vigil|demonstrat|stand[\s-]?outs?|bearing witness|picket|die-?in|walk-?out|sit-?in|"
                    r"blockade|civil disobedience|day of action|street theat|with signs|nonviolent opposition|"
                    r"with posters|posters, banners|sign-?holding", re.I)
# Weaker signals: only trusted in the title (the word "march" is also a month name).
WEAK_TITLE = re.compile(r"\bmarch\b|\bstrike\b|\boccupy\b|\baction\b", re.I)
# Titles that mark something as not a demonstration (unless a strong signal is also in the title).
NOT_DEMO = re.compile(r"book club|book talk|film|screening|concert|open house|webinar|training|orientation|welcome call|"
                      r"general meeting|conference|panel|reception|party|swap|court support|podcast|primer|conversation|"
                      r"discussion|workshop|meeting|action hours|office hours|postcard|phone ?bank|text ?bank|canvass|meet ?ups?|"
                      r"door knocking|fundraiser|huddle|ballots?\b", re.I)
ONLINE = re.compile(r"\bonline\b|\bvirtual\b|\bzoom\b|\bwebinar\b|jit\.?si|signal\.group", re.I)
CITIES = ["Boston", "Cambridge", "Somerville", "Burlington", "Brookline", "Medford", "Arlington", "Watertown", "Newton", "Quincy",
          "Waltham", "Lexington", "Concord", "Worcester", "Salem", "Lowell", "Lynn", "Everett", "Chelsea", "Revere", "Malden",
          "Framingham", "Woburn", "Wilmington", "Reading", "Lincoln", "Danvers", "Peabody", "Natick", "Needham", "Dedham",
          "Milton", "Braintree", "Randolph", "Northborough", "Methuen", "Ipswich", "Middleborough"]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def clean_location(loc):
    """Calendar locations often repeat themselves ("1000 District Avenue Burlington, MA 01803 United States, 1000 ...").
    Keep the distinct parts, drop the country, and cap the length."""
    seen, out = set(), []
    for part in [p.strip() for p in (loc or "").split(",")]:
        key = _norm(part)
        if part and key and key != "united states" and key not in seen:
            seen.add(key)
            out.append(part)
    return ", ".join(out[:4])


def region_of(loc):
    for c in CITIES:
        if re.search(r"\b%s\b" % c, loc or "", re.I):
            return "%s, MA" % c
    return "Massachusetts"


def classify(ev):
    """Returns (is_demo, reason). `reason` explains why something was NOT counted."""
    title = ev.get("summary", "")
    blob = " ".join([title, " ".join(ev.get("categories", [])), (ev.get("description") or "")[:1200]])
    loc = ev.get("location") or ""
    if ONLINE.search(loc) or ONLINE.search(title):
        return False, "online event"
    strong_in_title = bool(STRONG.search(title))
    if NOT_DEMO.search(title) and not strong_in_title:
        return False, "talk, film, meeting, or training"
    if STRONG.search(blob) or WEAK_TITLE.search(title):
        return True, ""
    return False, "no demonstration signal in the listing"


def _time_text(start, end):
    if not isinstance(start, datetime):
        return "All day"
    f = lambda d: d.strftime("%I:%M %p").lstrip("0")
    return "%s to %s" % (f(start), f(end)) if end else f(start)


def locate(ev, known_places, cache):
    """lat/lng from, in order: the feed's own GEO, the hand-kept places table, the Census geocoder."""
    if ev.get("geo"):
        return ev["geo"][0], ev["geo"][1]
    loc = _norm(clean_location(ev.get("location")))
    for key, p in (known_places or {}).items():
        if _norm(key) in loc:
            return p["lat"], p["lng"]
    if loc:
        try:
            hit = geocode.geocode(clean_location(ev.get("location")), cache)
        except http.SourceError:
            hit = None
        if hit:
            return hit[0], hit[1]
    return None, None


def parse_feed(text, feed, today, manual_by_name, known_places, cache):
    start, end = today, today + timedelta(days=WINDOW_DAYS)
    events, skipped, total = [], [], 0
    for ev in ics.parse_ics(text):
        if ev.get("status") == "CANCELLED":
            continue
        total += 1
        occs = ics.occurrences(ev, start, end)
        if not occs:
            continue
        ok, why = classify(ev)
        for s, e in occs:
            d = s.date() if isinstance(s, datetime) else s
            if not ok:
                skipped.append({"name": ev.get("summary", ""), "date": d.isoformat(), "reason": why, "source": feed["name"]})
                continue
            lat, lng = locate(ev, known_places, cache)
            if lat is not None and massdot.miles(CENTER[0], CENTER[1], lat, lng) > PROTEST_RADIUS_MI:
                skipped.append({"name": ev.get("summary", ""), "date": d.isoformat(), "reason": "outside the Boston-area radius", "source": feed["name"]})
                continue
            manual = manual_by_name.get(_norm(ev.get("summary")))
            events.append({
                "id": "%s-%s-%s" % (feed["id"], ev.get("uid") or _norm(ev.get("summary")), d.isoformat()),
                "name": ev.get("summary", ""), "date": d.isoformat(), "time_text": _time_text(s, e),
                "sort_time": s.strftime("%H:%M") if isinstance(s, datetime) else "00:00",
                "category": "Vigil / protest" if re.search(r"vigil|bearing witness|stand", ev.get("summary", "") + ev.get("description", "")[:200], re.I) else "Demonstration",
                "region": region_of(ev.get("location")), "venue": clean_location(ev.get("location")) or "Location not listed",
                "attendance": (manual or {}).get("attendance") or
                              "Attendance not published by the organizer. Rated Low provisionally; re-rate if the organizer or news reports "
                              "indicate a large turnout. Organizers may cancel for weather, so confirm before relying on it.",
                "rating": (manual or {}).get("rating") or "Low", "lat": lat, "lng": lng,
                "source_name": feed["name"], "url": ev.get("url"),
            })
    return events, skipped, total


def week_urls(template, today, days=WINDOW_DAYS):
    """One URL per Sunday-start calendar week touched by [today, today+days]."""
    seen, out = set(), []
    for i in range(days + 1):
        day = today + timedelta(days=i)
        sunday = day - timedelta(days=(day.weekday() + 1) % 7)
        if sunday not in seen:
            seen.add(sunday)
            out.append(template.replace("{date}", sunday.isoformat()))
    return out


def fetch(today, feeds, manual):
    """`manual` is the parsed data/protests.json. A feed (or one week of a feed) that fails is recorded and skipped;
    the section only fails if EVERY feed fails, so one broken website cannot blank the others."""
    known = (manual or {}).get("known_places", {})
    by_name = {_norm(e["name"]): e for e in (manual or {}).get("events", [])}
    cache = geocode._load()
    out = {"source": "Organizer calendar feeds", "feeds": [], "events": [], "skipped": [], "notes": []}
    for feed in feeds:
        rec = {"id": feed["id"], "name": feed["name"], "status": "ok", "listed": 0, "error": None}
        urls = week_urls(feed["url_template"], today) if feed.get("url_template") else [feed["url"]]
        errors, read = [], 0
        for i, url in enumerate(urls):
            try:
                text = http.get_text(url, headers={"Accept": "text/calendar, text/plain;q=0.9, */*;q=0.5"})
                if "BEGIN:VCALENDAR" not in text:
                    raise http.SourceError("response was not a calendar file (the site may have changed its export)")
                RAW_DIR.mkdir(parents=True, exist_ok=True)
                sample = RAW_DIR / ("%s_ical_sample.ics" % feed["id"])
                if not sample.exists():
                    sample.write_text(text[:200000])
                ev, sk, total = parse_feed(text, feed, today, by_name, known, cache)
                rec["listed"] += total
                out["events"] += ev
                out["skipped"] += sk
                read += 1
            except (http.SourceError, ValueError) as e:
                errors.append(str(e)[:160])
            if i < len(urls) - 1:
                time.sleep(1.0)        # be polite to volunteer-run sites
        if read == 0:
            rec["status"], rec["error"] = "error", (errors[0] if errors else "no data")
        else:
            if errors:
                out["notes"].append("%s: %d of %d calendar files could not be read (%s)." % (feed["name"], len(errors), len(urls), errors[0]))
            if rec["listed"] == 0:
                out["notes"].append("%s returned a calendar with no events; check data/raw/%s_ical_sample.ics." % (feed["name"], feed["id"]))
        out["feeds"].append(rec)
    geocode._save(cache)
    if feeds and all(f["status"] == "error" for f in out["feeds"]):
        raise http.SourceError("every protest feed failed: " + "; ".join("%s: %s" % (f["name"], f["error"]) for f in out["feeds"]))
    seen, uniq = set(), []
    for e in out["events"]:                      # a multi-day event can appear in two weekly files
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    out["events"] = uniq
    sk_seen, sk_uniq = set(), []
    for k in sorted(out["skipped"], key=lambda s: (s["date"], s["name"])):
        key = (_norm(k["name"]), k["date"])
        if key not in sk_seen:
            sk_seen.add(key)
            sk_uniq.append(k)
    out["skipped"] = sk_uniq
    return out
