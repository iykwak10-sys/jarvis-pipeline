"""Read today's Apple Calendar events without blocking the scheduler."""
from __future__ import annotations

import subprocess
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

SCRIPT = '''
set beginDate to current date
set time of beginDate to 0
set endDate to beginDate + 1 * days
set outputLines to {}
tell application "Calendar"
    repeat with cal in calendars
        set matches to every event of cal whose start date is greater than or equal to beginDate and start date is less than endDate
        repeat with ev in matches
            set d to start date of ev
            set ending to end date of ev
            set loc to location of ev
            if loc is missing value then set loc to ""
            set row to (year of d as text) & "-" & (month of d as integer as text) & "-" & (day of d as text) & "|" & (time of d as text) & "|" & (time of ending as text) & "|" & (summary of ev) & "|" & loc
            set end of outputLines to row
        end repeat
    end repeat
end tell
set AppleScript's text item delimiters to linefeed
return outputLines as text
'''


def get_apple_events() -> tuple[list[dict], str | None]:
    try:
        result = subprocess.run(["osascript", "-e", SCRIPT], capture_output=True, text=True, timeout=12, check=True)
        tz = ZoneInfo("Asia/Seoul")
        today = datetime.now(tz).date()
        events = []
        for row in result.stdout.splitlines():
            date_text, seconds, end_seconds, summary, location = row.split("|", 4)
            year, month, day = map(int, date_text.split("-"))
            event_date = today.replace(year=year, month=month, day=day)
            start = datetime.combine(event_date, time.min, tzinfo=tz) + timedelta(seconds=int(seconds))
            end = datetime.combine(event_date, time.min, tzinfo=tz) + timedelta(seconds=int(end_seconds))
            events.append({"id": f"apple:{len(events)}", "summary": summary, "location": location,
                           "description": "", "start_dt": start, "end_dt": end, "has_location": bool(location)})
        return events, None
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, ValueError) as exc:
        return [], f"Apple Calendar 조회 실패 ({type(exc).__name__})"
