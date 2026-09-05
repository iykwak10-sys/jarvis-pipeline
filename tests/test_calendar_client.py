from __future__ import annotations

from pathlib import Path

from schedule_briefing import calendar_client


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeCalendarList:
    def list(self, **kwargs):
        return FakeRequest(
            {
                "items": [
                    {"id": "owner@example.com", "primary": True, "selected": True},
                    {"id": "naver@example.com", "selected": True},
                    {"id": "hidden@example.com", "selected": False},
                ]
            }
        )


class FakeEvents:
    def __init__(self, events_by_calendar):
        self.events_by_calendar = events_by_calendar
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(
            {"items": self.events_by_calendar.get(kwargs["calendarId"], [])}
        )


class FakeService:
    def __init__(self, events_by_calendar):
        self.fake_events = FakeEvents(events_by_calendar)
        self.fake_calendar_list = FakeCalendarList()

    def calendarList(self):
        return self.fake_calendar_list

    def events(self):
        return self.fake_events


def timed_event(event_id: str, summary: str, start: str) -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": start},
    }


def _pure_source_line_count(source: str) -> int:
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in source.splitlines()
    )


def test_calendar_client_stays_within_250_source_lines() -> None:
    source = Path(calendar_client.__file__).read_text()
    source_lines = _pure_source_line_count(source)

    assert source_lines <= 250, (
        f"calendar_client.py has {source_lines} source lines; limit is 250"
    )


def test_today_includes_events_from_selected_secondary_calendars(monkeypatch) -> None:
    service = FakeService(
        {
            "primary": [],
            "naver@example.com": [
                timed_event("naver-1", "네이버 예약", "2026-09-06T15:00:00+09:00")
            ],
            "hidden@example.com": [
                timed_event("hidden-1", "숨긴 일정", "2026-09-06T16:00:00+09:00")
            ],
        }
    )
    monkeypatch.setattr(calendar_client, "_get_service", lambda: service)

    events = calendar_client.get_todays_events()

    assert [event["summary"] for event in events] == ["네이버 예약"]
    assert [event["id"] for event in events] == ["naver@example.com:naver-1"]
    assert {call["calendarId"] for call in service.fake_events.calls} == {
        "primary",
        "naver@example.com",
    }


def test_tomorrow_sorts_selected_calendars_before_global_limit(monkeypatch) -> None:
    service = FakeService(
        {
            "primary": [
                timed_event("primary-1", "늦은 일정", "2026-09-07T18:00:00+09:00")
            ],
            "naver@example.com": [
                timed_event("naver-1", "이른 일정", "2026-09-07T09:00:00+09:00")
            ],
        }
    )
    monkeypatch.setattr(calendar_client, "_get_service", lambda: service)

    events = calendar_client.get_tomorrow_events(max_results=1)

    assert [event["summary"] for event in events] == ["이른 일정"]
