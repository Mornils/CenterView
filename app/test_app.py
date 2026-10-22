from datetime import datetime
from zoneinfo import ZoneInfo

from app import events_from_ical

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
SUMMARY:Training
DTSTART:20261019T160000Z
DTEND:20261019T173000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Urlaub
DTSTART;VALUE=DATE:20261022
DTEND;VALUE=DATE:20261024
END:VEVENT
END:VCALENDAR
"""

tz = ZoneInfo("Europe/Berlin")
evs = events_from_ical(ICS, datetime(2026, 10, 19, tzinfo=tz), datetime(2026, 10, 26, tzinfo=tz), "x")
training = [e for e in evs if e["title"] == "Training"]
assert len(training) == 3, evs
assert training[0]["start"] == "2026-10-19T18:00:00", training[0]  # UTC -> CEST
urlaub = next(e for e in evs if e["title"] == "Urlaub")
assert urlaub["allDay"] and urlaub["end"] == "2026-10-24T00:00:00", urlaub
print("ok")
