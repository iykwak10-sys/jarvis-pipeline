from subprocess import CompletedProcess, TimeoutExpired

from schedule_briefing import apple_calendar


def test_apple_calendar_parses_events(monkeypatch) -> None:
    monkeypatch.setattr(apple_calendar.subprocess, "run", lambda *args, **kwargs:
                        CompletedProcess(args, 0, "2026-9-24|32400|36000|회의|서울\n", ""))
    events, error = apple_calendar.get_apple_events()
    assert error is None
    assert events[0]["start_dt"].isoformat() == "2026-09-24T09:00:00+09:00"


def test_apple_calendar_timeout_is_reported(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise TimeoutExpired("osascript", 12)
    monkeypatch.setattr(apple_calendar.subprocess, "run", fail)
    assert apple_calendar.get_apple_events() == ([], "Apple Calendar 조회 실패 (TimeoutExpired)")