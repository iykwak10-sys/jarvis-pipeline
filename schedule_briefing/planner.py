# schedule_briefing/planner.py
"""알림 예약 플래너 — 매 30분 실행
흐름:
  1. Google Calendar 오늘 남은 일정 조회
  2. 장소 있는 일정만 필터
  3. 게이트: 출발 2시간 이내 일정만 TMAP 호출
  4. 소요시간 계산 → 알림 시각 = start - travel - 30분
  5. schedule_db에 예약 저장
"""

from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시에도 core 모듈 임포트 가능)
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import LOG_DIR
from schedule_briefing import calendar_client, maps_client, tmap_client, schedule_db, location_cache

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

        # 게이트: 2시간 이내 일정만 Kakao 경로 API 호출
        if hours_until > TMAP_GATE_HOURS:
            logger.info(f"⏳ 경로 게이트 통과 안 됨 ({hours_until:.1f}시간 후): {summary}")
            continue

        if hours_until < 0:
            logger.info(f"⏭️ 이미 지난 일정 스킵: {summary}")
            continue

        logger.info(f"🗺️ 카카오 경로 호출: {summary} @ {location_text} ({hours_until:.1f}시간 후)")

        # 목적지 좌표 조회 (POI 검색 → 지오코딩 폴백)
        dest_coords = _resolve_destination(location_text)
        if not dest_coords:
            logger.warning(f"목적지 좌표 실패 — 기본값 사용: {location_text}")
            travel_minutes = FALLBACK_TRAVEL_MIN
            travel_mode = "기본값"
            dest_lat, dest_lng = None, None
        else:
            dest_lat, dest_lng = dest_coords

            # 카카오 소요시간 계산 (출발 예정 시각 기준)
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
            "description": event.get("description", ""),
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

    # ── 마지막 일정 귀가 경로 계산 ──
    if planned_count > 0:
        _add_return_home_if_applicable(events, origin_lat, origin_lng)

    logger.info(f"=== 플래너 완료: {planned_count}개 예약 ===")


def _resolve_destination(location_text: str) -> tuple[float, float] | None:
    """장소 텍스트 → 좌표 (카카오 POI 검색 → 카카오 지오코딩 → Google 지오코딩 순서로 시도)"""
    # 1차: 카카오 장소명 검색 (POI)
    coords = tmap_client.pois_search(location_text)
    if coords:
        return coords

    # 2차: 카카오 주소 지오코딩
    coords = tmap_client.geocode_address(location_text)
    if coords:
        return coords

    # 3차: Google Maps 지오코딩 (폴백)
    coords = maps_client.geocode(location_text)
    if coords:
        return coords

    return None


def _add_return_home_if_applicable(events: list[dict], origin_lat: float, origin_lng: float) -> None:
    """오늘 마지막 장소 일정의 귀가 경로를 계산해 schedule_db에 업데이트"""
    from core import config

    # 마지막 장소 있는 일정 찾기
    last_event = None
    for event in reversed(events):
        if event["has_location"]:
            last_event = event
            break

    if not last_event:
        return

    event_id = last_event["id"]
    # 이미 예약된 알림이어야 함
    alerts = schedule_db._load()
    alert = next((a for a in alerts if a["event_id"] == event_id and not a.get("sent")), None)
    if not alert:
        return

    dest_lat = alert.get("dest_lat")
    dest_lng = alert.get("dest_lng")
    if not dest_lat or not dest_lng:
        return

    # HOME 좌표
    home_lat = float(config.get("HOME_LAT", "0"))
    home_lng = float(config.get("HOME_LNG", "0"))
    if not home_lat or not home_lng:
        return

    try:
        end_dt = last_event["end_dt"]
        return_info = tmap_client.get_travel_time(dest_lat, dest_lng, home_lat, home_lng, end_dt)
        alert["return_home_minutes"] = return_info["recommended_minutes"]
        alert["return_home_mode"] = return_info["mode"]
        schedule_db.upsert_alert(event_id, alert)

        return_time = end_dt + timedelta(minutes=return_info["recommended_minutes"])
        logger.info(
            f"🏠 귀가 경로: {last_event['summary']} 종료 후 "
            f"{return_info['recommended_minutes']}분 → 예상 귀가 {return_time.strftime('%H:%M')}"
        )
    except Exception as e:
        logger.warning(f"귀가 경로 계산 실패: {e}")


def run_tomorrow() -> None:
    """내일 사전 브리핑 — 첫 일정 역산 기상 시간 + 하루 일정 요약"""
    from datetime import date, timedelta
    from core import notifier

    tomorrow_date = date.today() + timedelta(days=1)
    tomorrow_str = tomorrow_date.strftime("%m/%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][tomorrow_date.weekday()]

    logger.info(f"=== 내일({tomorrow_str} {weekday}) 사전 브리핑 시작 ===")

    events = calendar_client.get_tomorrow_events()
    if not events:
        logger.info("내일 일정 없음 — 브리핑 생략")
        return

    # 현재 위치 (내일도 같은 위치 가정)
    location = location_cache.get_current_location()
    origin_lat = location["lat"]
    origin_lng = location["lng"]

    lines = [
        f"🌙 <b>내일({tomorrow_str} {weekday}) 일정 브리핑</b>",
        "",
        f"📅 총 <b>{len(events)}건</b>의 일정",
        "",
    ]

    first_event = None
    wake_up_time = None

    for i, event in enumerate(events, 1):
        start_str = event["start_dt"].strftime("%H:%M")
        location_text = event["location"] if event["has_location"] else "장소 미정"
        lines.append(
            f"<b>{i}.</b> {start_str} {event['summary']}"
            f"{' @ ' + location_text if event['has_location'] else ''}"
        )

        # 첫 번째 장소 있는 일정으로 기상 시간 계산
        if first_event is None and event["has_location"]:
            first_event = event

    # 첫 일정 역산: 기상 시간 계산
    if first_event:
        dest_coords = _resolve_destination(first_event["location"])
        if dest_coords:
            dest_lat, dest_lng = dest_coords
            start_dt = first_event["start_dt"]
            travel_info = tmap_client.get_travel_time(
                origin_lat, origin_lng, dest_lat, dest_lng, start_dt,
            )
            travel_min = travel_info["recommended_minutes"]
            travel_mode = travel_info["mode"]

            # 기상 시간 = 일정 - 이동 - 버퍼 - 1시간 준비
            prep_minutes = 60
            wake_dt = start_dt - timedelta(minutes=travel_min + BUFFER_MINUTES + prep_minutes)
            wake_up_time = wake_dt.strftime("%H:%M")

            lines.append("")
            lines.append(
                f"⏰ <b>권장 기상: {wake_up_time}</b> "
                f"(첫 일정 {start_dt.strftime('%H:%M')}, {travel_mode} {travel_min}분)"
            )
        else:
            lines.append("")
            lines.append(f"⏰ 첫 일정: {first_event['start_dt'].strftime('%H:%M')} @ {first_event['location']}")

    # 첫 일정 준비물 힌트
    if first_event and first_event.get("description"):
        desc = first_event["description"].strip()[:200]
        lines.append(f"📋 메모: {desc}")

    lines.append("")
    lines.append("좋은 밤 되세요! 🌙")

    message = "\n".join(lines)
    ok = notifier.send(message)
    logger.info(f"내일 브리핑 전송: {'성공' if ok else '실패'}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["today", "tomorrow"], default="today",
                        help="today: 오늘 일정 알림 예약 (기본), tomorrow: 내일 일정 사전 브리핑")
    args = parser.parse_args()

    if args.mode == "tomorrow":
        run_tomorrow()
    else:
        run()
