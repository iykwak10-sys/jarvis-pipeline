# schedule_briefing/location_cache.py
"""현재 위치 캐시 — iOS 단축어 → Telegram 봇 → 파일 저장/조회"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from core import config

logger = logging.getLogger(__name__)

_LOCATION_FILE = Path(__file__).parent.parent / "data" / "current_location.json"
# 위치 정보 유효 시간 (이 이상 오래된 위치는 기본값으로 대체)
_LOCATION_TTL_HOURS = 4


def save_location(lat: float, lng: float, source: str = "telegram") -> None:
    """현재 위치 저장 (Telegram 봇이 호출)"""
    try:
        _LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "lat": lat,
            "lng": lng,
            "source": source,
            "updated_at": datetime.now().isoformat(),
        }
        _LOCATION_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        logger.info(f"위치 저장: ({lat:.4f}, {lng:.4f}) via {source}")
    except Exception as e:
        logger.error(f"위치 저장 실패: {e}")


def get_current_location() -> dict:
    """현재 위치 조회.

    Returns:
        {lat, lng, source, is_default}
        - TTL 이내 저장된 위치가 있으면 반환
        - 없거나 오래된 경우 .env의 HOME_LAT/HOME_LNG 기본값 반환
    """
    # 저장된 위치 확인
    if _LOCATION_FILE.exists():
        try:
            data = json.loads(_LOCATION_FILE.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(data["updated_at"])
            if datetime.now() - updated_at < timedelta(hours=_LOCATION_TTL_HOURS):
                return {
                    "lat": data["lat"],
                    "lng": data["lng"],
                    "source": data.get("source", "unknown"),
                    "is_default": False,
                }
        except Exception:
            pass

    # 기본값 (집 주소)
    home_lat = float(config.get("HOME_LAT", "37.5665"))
    home_lng = float(config.get("HOME_LNG", "126.9780"))
    logger.info(f"현재 위치 없음 — 기본값(집) 사용: ({home_lat}, {home_lng})")
    return {
        "lat": home_lat,
        "lng": home_lng,
        "source": "default_home",
        "is_default": True,
    }
