from __future__ import annotations

from datetime import datetime

from core import notifier
from schedule_briefing import calendar_client, planner


def _event(summary: str, start: str, location: str = "") -> dict:
    start_dt = datetime.fromisoformat(start)
    return {
        "id": summary,
        "summary": summary,
        "location": location,
        "description": "",
        "start_dt": start_dt,
        "end_dt": start_dt,
        "has_location": bool(location),
    }


def test_today_briefing_sends_the_full_day_schedule(monkeypatch) -> None:
    sent_messages: list[str] = []
    monkeypatch.setattr(
        calendar_client,
        "get_today_events",
        lambda: [
            _event("팀 미팅", "2026-09-07T09:00:00+09:00", "서울 오피스"),
            _event("병원 예약", "2026-09-07T16:30:00+09:00"),
        ],
    )
    monkeypatch.setattr(
        notifier,
        "send",
        lambda message: sent_messages.append(message) or True,
    )

    planner.run_today_briefing()

    assert len(sent_messages) == 1
    assert "오늘" in sent_messages[0]
    assert "총 <b>2건</b>의 일정" in sent_messages[0]
    assert "09:00 팀 미팅 @ 서울 오피스" in sent_messages[0]
    assert "16:30 병원 예약" in sent_messages[0]
