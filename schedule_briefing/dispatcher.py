# schedule_briefing/dispatcher.py
"""알림 디스패처 — 매 1분 실행
흐름:
  1. schedule_db에서 지금 발송해야 할 알림 조회
  2. Google Maps 장소 리뷰 조회 (캐시 우선)
  3. LLM으로 자연어 메시지 생성
  4. Telegram 발송
  5. 발송 완료 표시
"""

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시에도 core 모듈 임포트 가능)
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

import requests

from core import notifier
from core.config import LOG_DIR, OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL
from schedule_briefing import schedule_db, maps_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_DIR / "schedule_dispatcher.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def run() -> None:
    now = datetime.now()
    pending = schedule_db.get_pending_alerts(now)

    if not pending:
        return

    logger.info(f"=== 디스패처: {len(pending)}개 알림 발송 시작 ===")

    for alert in pending:
        try:
            _send_alert(alert)
            schedule_db.mark_sent(alert["event_id"])
            logger.info(f"✅ 발송 완료: {alert['summary']}")
        except Exception as e:
            logger.error(f"❌ 발송 실패 ({alert['summary']}): {e}")


def _send_alert(alert: dict) -> None:
    summary = alert["summary"]
    location = alert["location"]
    start_dt = datetime.fromisoformat(alert["start_dt"])
    travel_minutes = alert["travel_minutes"]
    travel_mode = alert["travel_mode"]
    location_is_default = alert.get("location_is_default", True)

    # Google Maps 장소 정보 조회 (캐시 우선)
    place_info = maps_client.get_place_info(location)
    place_type = maps_client.describe_place_type(place_info.get("types", []))

    # LLM으로 자연어 메시지 생성
    message = _generate_message(
        summary=summary,
        location=location,
        start_dt=start_dt,
        travel_minutes=travel_minutes,
        travel_mode=travel_mode,
        place_info=place_info,
        place_type=place_type,
        location_is_default=location_is_default,
    )

    notifier.send(message)


def _generate_message(
    summary: str,
    location: str,
    start_dt: datetime,
    travel_minutes: int,
    travel_mode: str,
    place_info: dict,
    place_type: str,
    location_is_default: bool,
) -> str:
    """LLM 호출해서 자연어 브리핑 메시지 생성. 실패 시 폴백 메시지 반환."""
    try:
        return _llm_message(
            summary, location, start_dt, travel_minutes, travel_mode,
            place_info, place_type, location_is_default,
        )
    except Exception as e:
        logger.warning(f"LLM 메시지 생성 실패 — 폴백 사용: {e}")
        return _fallback_message(summary, location, start_dt, travel_minutes, travel_mode)


def _llm_message(
    summary: str,
    location: str,
    start_dt: datetime,
    travel_minutes: int,
    travel_mode: str,
    place_info: dict,
    place_type: str,
    location_is_default: bool,
) -> str:
    """OpenRouter LLM으로 자연어 메시지 생성"""
    start_str = start_dt.strftime("%H시 %M분")
    now_str = datetime.now().strftime("%H시 %M분")

    rating_text = ""
    if place_info.get("rating"):
        stars = "⭐" * round(place_info["rating"])
        rating_text = f"평점 {place_info['rating']:.1f} {stars} ({place_info.get('user_ratings_total', 0):,}개 리뷰)"

    opening_text = ""
    if place_info.get("opening_hours"):
        opening_text = f"현재 {place_info['opening_hours']}"

    reviews_text = ""
    if place_info.get("top_reviews"):
        reviews_text = "최근 방문자 리뷰: " + " / ".join(
            r[:80] for r in place_info["top_reviews"][:2]
        )

    location_note = ""
    if location_is_default:
        location_note = "(현재 위치 불명확 — 집 기준 계산)"

    system_prompt = """당신은 개인 비서 Jarvis입니다.
사용자의 일정을 파악하고 출발해야 할 시간을 알려주는 짧고 친근한 메시지를 작성하세요.
- 말투: 친근하고 실용적 (예: "이제 슬슬 출발해야 안늦어요!")
- 길이: 4~6문장 이내
- HTML 포맷: <b>강조</b> 사용 가능
- 불필요한 인사말 금지
- 장소 특성과 교통 상황을 자연스럽게 녹여낼 것"""

    user_prompt = f"""
일정 정보:
- 일정명: {summary}
- 장소: {location} ({place_type})
- 일정 시각: {start_str}
- 현재 시각: {now_str}
- 이동시간: {travel_minutes}분 ({travel_mode} 기준)
{rating_text}
{opening_text}
{reviews_text}
{location_note}

위 정보를 바탕으로 출발 알림 메시지를 작성해주세요.
""".strip()

    resp = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 300,
            "temperature": 0.7,
        },
        timeout=15,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()

    # Telegram HTML 호환 헤더 추가
    header = f"🔔 <b>출발 알림</b> — {summary}\n\n"
    return header + content


def _fallback_message(
    summary: str,
    location: str,
    start_dt: datetime,
    travel_minutes: int,
    travel_mode: str,
) -> str:
    """LLM 실패 시 폴백 메시지 (단순 텍스트)"""
    start_str = start_dt.strftime("%H:%M")
    now_str = datetime.now().strftime("%H:%M")
    arrive_time = (start_dt).strftime("%H:%M")

    return (
        f"🔔 <b>출발 알림</b>\n\n"
        f"📅 <b>{summary}</b>\n"
        f"📍 {location}\n"
        f"🕐 일정 시각: {start_str}\n"
        f"🚗 이동: {travel_minutes}분 ({travel_mode})\n\n"
        f"지금 출발하면 딱 맞게 도착해요! ({now_str} 현재)"
    )


if __name__ == "__main__":
    run()
