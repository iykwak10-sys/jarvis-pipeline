# schedule_briefing/planner.py
"""알림 예약 플래너 — 매 30분 실행
흐름:
  1. Google Calendar 오늘 남은 일정 조회
  2. 장소 있는 일정만 필터
  3. 게이트: 출발 2시간 이내 일정만 TMAP 호출
  4. 소요시간 계산 → 알림 시각 = start - travel - 30분
  5. schedule_db에 예약 저장
"""

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시에도 core 모듈 임포트 가능)
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import LOG_DIR
from schedule_briefing import calendar_client, tmap_client, schedule_db, location_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_DIR / "schedule_planner.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────
BUFFER_MINUTES = 30      # 이동시간 외 여유시간
TMAP_GATE_HOURS = 2      # 이 시간 이내 일정만 TMAP API 호출
FALLBACK_TRAVEL_MIN = 30 # TMAP 실패 시 기본 이동시간


def run() -> None:
    logger.info("=== 일정 플래너 시작 ===")

    # 현재 위치 조회
    location = location_cache.get_current_location()
    origin_lat = location["lat"]
    origin_lng = location["lng"]
    location_source = location["source"]
    logger.info(f"출발지: ({origin_lat:.4f}, {origin_lng:.4f}) [{location_source}]")

    # 오늘 남은 일정 조회
    events = calendar_client.get_todays_events()
    if not events:
        logger.info("오늘 남은 일정 없음")
        return

    now = datetime.now().astimezone()
    planned_count = 0

    for event in events:
        event_id = event["id"]
        summary = event["summary"]
        start_dt = event["start_dt"]

        # 장소 없는 일정 스킵
        if not event["has_location"]:
            logger.debug(f"장소 없음 스킵: {summary}")
            continue

        # 이미 예약된 알림 스킵 (중복 방지)
        if schedule_db.is_already_planned(event_id):
            logger.debug(f"이미 예약됨 스킵: {summary}")
            continue

        location_text = event["location"]
        hours_until = (start_dt - now).total_seconds() / 3600

        # 게이트: 2시간 이내 일정만 TMAP 호출
        if hours_until > TMAP_GATE_HOURS:
            logger.info(f"⏳ TMAP 게이트 통과 안 됨 ({hours_until:.1f}시간 후): {summary}")
            continue

        if hours_until < 0:
            logger.info(f"⏭️ 이미 지난 일정 스킵: {summary}")
            continue

        logger.info(f"🗺️ TMAP 호출: {summary} @ {location_text} ({hours_until:.1f}시간 후)")

        # 목적지 좌표 조회 (POI 검색 → 지오코딩 폴백)
        dest_coords = _resolve_destination(location_text)
        if not dest_coords:
            logger.warning(f"목적지 좌표 실패 — 기본값 사용: {location_text}")
            travel_minutes = FALLBACK_TRAVEL_MIN
            travel_mode = "기본값"
            dest_lat, dest_lng = None, None
        else:
            dest_lat, dest_lng = dest_coords

            # TMAP 소요시간 계산 (출발 예정 시각 기준)
            depart_estimate = start_dt - timedelta(minutes=BUFFER_MINUTES)
            travel_info = tmap_client.get_travel_time(
                origin_lat, origin_lng,
                dest_lat, dest_lng,
                depart_estimate,
            )
            travel_minutes = travel_info["recommended_minutes"]
            travel_mode = travel_info["mode"]

        # 알림 발송 시각 계산
        alert_dt = start_dt - timedelta(minutes=travel_minutes + BUFFER_MINUTES)

        # 알림 시각이 이미 지났으면 지금 즉시 발송 예약 (5분 후)
        if alert_dt <= now:
            alert_dt = now + timedelta(minutes=5)
            logger.info(f"⚠️ 알림 시각 이미 지남 — 5분 후로 변경: {summary}")

        schedule_db.upsert_alert(event_id, {
            "summary": summary,
            "location": location_text,
            "start_dt": start_dt.isoformat(),
            "alert_dt": alert_dt.isoformat(),
            "travel_minutes": travel_minutes,
            "travel_mode": travel_mode,
            "origin_lat": origin_lat,
            "origin_lng": origin_lng,
            "dest_lat": dest_lat,
            "dest_lng": dest_lng,
            "location_is_default": location["is_default"],
            "planned_at": now.isoformat(),
        })

        logger.info(
            f"✅ 알림 예약: {summary}\n"
            f"   📍 {location_text}\n"
            f"   🕐 일정: {start_dt.strftime('%H:%M')}\n"
            f"   🚗 이동: {travel_minutes}분 ({travel_mode})\n"
            f"   🔔 알림: {alert_dt.strftime('%H:%M')}"
        )
        planned_count += 1

    # 오래된 알림 정리
    schedule_db.cleanup_old_alerts(days=3)
    logger.info(f"=== 플래너 완료: {planned_count}개 예약 ===")


def _resolve_destination(location_text: str) -> tuple[float, float] | None:
    """장소 텍스트 → 좌표 (POI 검색 → 지오코딩 순서로 시도)"""
    # 1차: POI 키워드 검색 (장소명)
    coords = tmap_client.pois_search(location_text)
    if coords:
        return coords

    # 2차: 지오코딩 (주소 형식)
    coords = tmap_client.geocode_address(location_text)
    if coords:
        return coords

    return None


if __name__ == "__main__":
    run()
