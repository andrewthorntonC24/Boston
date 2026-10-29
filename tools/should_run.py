"""Decide whether this scheduled run should refresh.

GitHub cron is UTC-only and can start late, but you want 12 AM / 6 AM / 12 PM / 6 PM Eastern year-round
(daylight saving moves the UTC hours). The workflow therefore fires at BOTH the EDT and EST UTC hours, and this
gate lets a run through only if the last refresh is at least MIN_MINUTES old. That makes the second trigger of
each pair a no-op, tolerates cron delays of up to ~45 minutes, and always runs if there is no previous refresh.
Manual runs (workflow_dispatch) skip this gate entirely."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_MINUTES = 315  # 5h15m: a 6-hour cadence with room for late starts


def should_run(path, now=None, min_minutes=MIN_MINUTES):
    now = now or datetime.now(timezone.utc)
    p = Path(path)
    if not p.exists():
        return True
    try:
        last = datetime.fromisoformat(json.loads(p.read_text())["generated_at"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return True
    return (now - last).total_seconds() / 60 >= min_minutes


if __name__ == "__main__":
    print("true" if should_run(sys.argv[1] if len(sys.argv) > 1 else "data/dashboard.json") else "false")
