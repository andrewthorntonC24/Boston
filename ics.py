"""A small iCalendar (.ics) reader: just what the protest calendar feeds need.

Handles: line folding, text escapes, TZID/UTC/floating/all-day times, GEO, STATUS:CANCELLED, EXDATE, and simple
DAILY/WEEKLY recurrence rules (INTERVAL, BYDAY, COUNT, UNTIL). Anything fancier (MONTHLY/YEARLY rules) is not
expanded: the first occurrence is kept and the event is flagged `rrule_unsupported` so nothing is silently wrong."""
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import TZ

_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def unfold(text):
    """RFC 5545: a line that starts with a space or tab continues the previous line."""
    return re.sub(r"\n[ \t]", "", text.replace("\r\n", "\n").replace("\r", "\n"))


def unescape(v):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def split_prop(line):
    """NAME;PARAM=VAL:VALUE -> (name, params, value). Colons and semicolons inside quotes are ignored."""
    in_q, cut = False, None
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            cut = i
            break
    if cut is None:
        return None
    head, value = line[:cut], line[cut + 1:]
    parts, buf, in_q = [], "", False
    for ch in head:
        if ch == '"':
            in_q = not in_q
        if ch == ";" and not in_q:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def parse_dt(value, params):
    """Returns (datetime-in-Eastern or date, all_day)."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return date(int(v[:4]), int(v[4:6]), int(v[6:8])), True
    fmt = "%Y%m%dT%H%M%S"
    if v.endswith("Z"):
        return datetime.strptime(v[:-1], fmt).replace(tzinfo=timezone.utc).astimezone(TZ), False
    try:
        tz = ZoneInfo(params["TZID"]) if params.get("TZID") else TZ
    except Exception:  # unknown zone name: assume Eastern rather than fail the whole feed
        tz = TZ
    return datetime.strptime(v, fmt).replace(tzinfo=tz).astimezone(TZ), False


def parse_ics(text):
    events, cur = [], None
    for line in unfold(text).split("\n"):
        if line.strip() == "BEGIN:VEVENT":
            cur = {"categories": [], "exdates": [], "status": ""}
        elif line.strip() == "END:VEVENT":
            if cur is not None and cur.get("start") is not None:
                events.append(cur)
            cur = None
        elif cur is not None:
            p = split_prop(line)
            if not p:
                continue
            name, params, value = p
            try:
                if name == "SUMMARY":
                    cur["summary"] = unescape(value).strip()
                elif name == "DESCRIPTION":
                    cur["description"] = unescape(value).strip()
                elif name == "LOCATION":
                    cur["location"] = unescape(value).strip()
                elif name == "URL":
                    cur["url"] = value.strip()
                elif name == "UID":
                    cur["uid"] = value.strip()
                elif name == "STATUS":
                    cur["status"] = value.strip().upper()
                elif name == "CATEGORIES":
                    cur["categories"] += [unescape(c).strip() for c in value.split(",") if c.strip()]
                elif name == "GEO":
                    la, lo = value.split(";")
                    cur["geo"] = (float(la), float(lo))
                elif name == "RRULE":
                    cur["rrule"] = value.strip()
                elif name == "EXDATE":
                    for part in value.split(","):
                        d, _ = parse_dt(part, params)
                        cur["exdates"].append(d.date() if isinstance(d, datetime) else d)
                elif name == "DTSTART":
                    cur["start"], cur["all_day"] = parse_dt(value, params)
                elif name == "DTEND":
                    cur["end"], _ = parse_dt(value, params)
            except (ValueError, KeyError):
                continue  # one malformed property should not discard the whole event
    return events


def _rule(s):
    return dict(kv.split("=", 1) for kv in s.split(";") if "=" in kv)


def occurrences(ev, window_start, window_end):
    """All (start, end) occurrences whose DATE falls in [window_start, window_end]. Dates are `date` objects."""
    start, end = ev["start"], ev.get("end")
    d0 = start.date() if isinstance(start, datetime) else start
    dur = (end - start) if isinstance(start, datetime) and isinstance(end, datetime) else None
    days = []
    rule = _rule(ev["rrule"]) if ev.get("rrule") else None
    if not rule:
        days = [d0]
    elif rule.get("FREQ") in ("DAILY", "WEEKLY"):
        interval = int(rule.get("INTERVAL", 1))
        count = int(rule["COUNT"]) if "COUNT" in rule else None
        until = None
        if "UNTIL" in rule:
            u = rule["UNTIL"]
            until = date(int(u[:4]), int(u[4:6]), int(u[6:8]))
        byday = [_DAYS[x[-2:]] for x in rule.get("BYDAY", "").split(",") if x[-2:] in _DAYS] or [d0.weekday()]
        week0 = d0 - timedelta(days=d0.weekday())
        n, d = 0, d0
        while d <= window_end and (until is None or d <= until) and (count is None or n < count):
            if rule["FREQ"] == "DAILY":
                ok = ((d - d0).days % interval == 0)
            else:
                ok = d.weekday() in byday and ((d - week0).days // 7) % interval == 0
            if ok:
                n += 1
                days.append(d)
            d += timedelta(days=1)
    else:
        days = [d0]
        ev["rrule_unsupported"] = True
    out = []
    for d in days:
        if d in ev.get("exdates", []) or not (window_start <= d <= window_end):
            continue
        if isinstance(start, datetime):
            s = datetime.combine(d, start.timetz()).replace(tzinfo=start.tzinfo)
            out.append((s, (s + dur) if dur else None))
        else:
            out.append((d, None))
    return out
