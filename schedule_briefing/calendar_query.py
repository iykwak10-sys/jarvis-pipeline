from __future__ import annotations

from datetime import datetime
from typing import TypedDict


class CalendarEvent(TypedDict):
    id: str
    summary: str
    location: str
    description: str
    start_dt: datetime
    end_dt: datetime
    has_location: bool


def get_events(
    service,
    time_min: datetime,
    time_max: datetime,
    max_results: int,
) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []

    for calendar_id in _selected_calendar_ids(service):
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for item in result.get("items", []):
            start_raw = item["start"].get("dateTime") or item["start"].get("date")
            end_raw = item["end"].get("dateTime") or item["end"].get("date")
            if "T" not in start_raw:
                continue

            location = item.get("location", "").strip()
            event_id = item["id"]
            if calendar_id != "primary":
                event_id = f"{calendar_id}:{event_id}"

            events.append(
                {
                    "id": event_id,
                    "summary": item.get("summary", "(제목 없음)"),
                    "location": location,
                    "description": item.get("description", ""),
                    "start_dt": datetime.fromisoformat(start_raw),
                    "end_dt": datetime.fromisoformat(end_raw),
                    "has_location": bool(location),
                }
            )

    events.sort(key=lambda event: (event["start_dt"], event["id"]))
    return events[:max_results]


def _selected_calendar_ids(service) -> list[str]:
    calendar_ids = ["primary"]
    page_token = None

    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for item in result.get("items", []):
            if item.get("primary") or not item.get("selected"):
                continue
            calendar_id = item["id"]
            if calendar_id not in calendar_ids:
                calendar_ids.append(calendar_id)

        page_token = result.get("nextPageToken")
        if not page_token:
            return calendar_ids
