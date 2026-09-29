# schedule_briefing/schedule_db.py
"""알림 예약 DB — JSON 파일 기반 CRUD"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_FILE = Path(__file__).parent.parent / "data" / "schedule_alerts.json"


def _load() -> list[dict]:
    if not _DB_FILE.exists():
        return []
    try:
        return json.loads(_DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(alerts: list[dict]) -> None:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DB_FILE.write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def upsert_alert(event_id: str, alert: dict) -> None:
    """알림 예약 추가 또는 업데이트 (event_id 기준)

    alert 구조:
    {
        event_id: str,
        summary: str,           # 일정 제목
        location: str,          # 목적지
        start_dt: str,          # ISO8601, KST
        alert_dt: str,          # 알림 발송 시각 ISO8601, KST
        travel_minutes: int,    # 이동시간
        travel_mode: str,       # 이동수단
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
        sent: bool,             # 발송 완료 여부
        planned_at: str,        # 예약 계획 시각
    }
    """
    alerts = _load()
    existing_idx = next((i for i, a in enumerate(alerts) if a["event_id"] == event_id), None)

    alert["event_id"] = event_id
    if "sent" not in alert:
        alert["sent"] = False

    if existing_idx is not None:
        # 이미 발송된 건 업데이트 안 함
        if alerts[existing_idx].get("sent"):
            return
        alerts[existing_idx] = alert
    else:
        alerts.append(alert)

    _save(alerts)
    logger.info(f"알림 예약: {alert.get('summary')} @ {alert.get('alert_dt')}")


def get_pending_alerts(now: datetime | None = None) -> list[dict]:
    """지금 발송해야 할 알림 목록 (alert_dt <= now, sent=False)"""
    from datetime import timedelta

    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.astimezone()
    stale_cutoff = now - timedelta(hours=6)

    alerts = _load()
    pending = []
    for a in alerts:
        if a.get("sent"):
            continue
        try:
            alert_dt = datetime.fromisoformat(a["alert_dt"])
            if alert_dt.tzinfo is None:
                alert_dt = alert_dt.astimezone()
            if alert_dt < stale_cutoff:
                continue
            if alert_dt <= now:
                pending.append(a)
        except Exception:
            pass
    return pending


def mark_sent(event_id: str) -> None:
    """알림 발송 완료 표시"""
    alerts = _load()
    for a in alerts:
        if a["event_id"] == event_id:
            a["sent"] = True
            a["sent_at"] = datetime.now().isoformat()
            break
    _save(alerts)


def cleanup_old_alerts(days: int = 3) -> None:
    """오래된 알림 정리.

    발송 완료 알림뿐 아니라, 발송 시각이 오래 지난 미발송 알림도 제거한다.
    오래된 미발송 알림을 남겨두면 dispatcher 복구 후 과거 알림이 발송될 수 있다.
    """
    from datetime import timedelta
    now = datetime.now().astimezone()
    sent_cutoff = now - timedelta(days=days)
    unsent_cutoff = now - timedelta(hours=6)
    alerts = _load()
    kept = []
    for a in alerts:
        try:
            planned_at = datetime.fromisoformat(a.get("planned_at", "2000-01-01"))
            if planned_at.tzinfo is None:
                planned_at = planned_at.astimezone()
            alert_dt = datetime.fromisoformat(a.get("alert_dt", a.get("start_dt", "2000-01-01")))
            if alert_dt.tzinfo is None:
                alert_dt = alert_dt.astimezone()
        except Exception:
            continue

        if a.get("sent"):
            if planned_at > sent_cutoff:
                kept.append(a)
        else:
            # 미래/최근 미발송 알림만 유지. 6시간 넘게 지난 미발송 알림은 폐기.
            if alert_dt > unsent_cutoff:
                kept.append(a)
    _save(kept)


def is_already_planned(event_id: str) -> bool:
    """이미 예약된 알림인지 확인"""
    alerts = _load()
    return any(a["event_id"] == event_id and not a.get("sent") for a in alerts)


def get_active_alert(event_id: str) -> dict | None:
    """미발송 상태의 알림 1건 조회 (event_id 기준)

    Returns:
        알림 dict 또는 None — 발송됐거나 존재하지 않으면 None
    """
    alerts = _load()
    return next(
        (a for a in alerts if a["event_id"] == event_id and not a.get("sent")),
        None,
    )
