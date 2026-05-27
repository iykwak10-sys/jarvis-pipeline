# schedule_briefing/weather_client.py
"""기상청 단기예보 API — 강수확률 기반 이동수단 추천"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 기상청 단기예보 API
_WEATHER_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"
# 캐시 (30분)
_CACHE_FILE = Path(__file__).parent.parent / "data" / "weather_cache.json"
_CACHE_TTL_MINUTES = 30


def _api_key() -> str:
    from core import config
    key = config.get("KMA_API_KEY", "") or config.get("WEATHER_API_KEY", "")
    if not key:
        raise ValueError("KMA_API_KEY 환경변수가 설정되지 않았습니다.")
    return key


def _get_grid_coords(lat: float, lng: float) -> tuple[int, int]:
    """위경도 → 기상청 격자 좌표 (nx, ny)"""
    RE = 6371.00877
    GRID = 5.0
    SLAT1 = 30.0
    SLAT2 = 60.0
    OLON = 126.0
    OLAT = 38.0
    XO = 43
    YO = 136
    import math
    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lng * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = int(ra * math.sin(theta) + XO + 0.5)
    ny = int(ro - ra * math.cos(theta) + YO + 0.5)
    return nx, ny


def _load_cache() -> Optional[dict]:
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at < timedelta(minutes=_CACHE_TTL_MINUTES):
            return data
    except Exception:
        pass
    return None


def _save_cache(data: dict) -> None:
    try:
        data["_cached_at"] = datetime.now().isoformat()
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"날씨 캐시 저장 실패: {e}")


def get_weather_context(lat: float, lng: float) -> dict:
    """현재 위치 기준 날씨 정보 조회 (캐시 30분)

    Returns:
        {rain_prob: int, rainy: bool, sky: str, temp: float, summary: str}
    """
    cached = _load_cache()
    if cached and cached.get("lat") == lat and cached.get("lng") == lng:
        return cached

    try:
        key = _api_key()
    except ValueError:
        return _empty_weather()

    nx, ny = _get_grid_coords(lat, lng)

    now = datetime.now()
    base_time = _get_base_time(now)
    base_date = base_time.strftime("%Y%m%d")
    base_time_str = base_time.strftime("%H%M")

    try:
        resp = requests.get(
            _WEATHER_URL,
            params={
                "serviceKey": key,
                "pageNo": "1",
                "numOfRows": "60",
                "dataType": "JSON",
                "base_date": base_date,
                "base_time": base_time_str,
                "nx": nx,
                "ny": ny,
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data["response"]["body"]["items"]["item"]
        weather = _parse_weather(items)
        weather["lat"] = lat
        weather["lng"] = lng
        _save_cache(weather)
        return weather

    except Exception as e:
        logger.warning(f"날씨 조회 실패: {e}")
        return _empty_weather()


def _get_base_time(now: datetime) -> datetime:
    """기상청 초단기예보 base_time 계산 (매시 30분 발표, 발표 후 ~10분 뒤 사용 가능)

    예) 14:25 → 13:30 사용, 14:45 → 14:30 사용
    자정 경계(00:00~00:39)에는 전날 23:30 발표 사용.
    """
    minute = now.minute
    if minute < 40:
        # 한 시간 전 30분 발표 — 자정 경계는 timedelta로 안전 처리
        base = (now - timedelta(hours=1)).replace(minute=30, second=0, microsecond=0)
    else:
        base = now.replace(minute=30, second=0, microsecond=0)
    return base


def _parse_weather(items: list[dict]) -> dict:
    """기상청 응답 파싱 → 요약"""
    rain_prob = 0
    sky_code = "1"
    temp = 0.0

    for item in items:
        category = item["category"]
        fcst_value = item["fcstValue"]
        if category == "POP":
            rain_prob = int(fcst_value)
        elif category == "SKY":
            sky_code = fcst_value
        elif category == "T1H":
            temp = float(fcst_value)

    # 하늘 상태
    sky_map = {"1": "맑음", "3": "구름많음", "4": "흐림"}
    sky = sky_map.get(sky_code, "알 수 없음")

    # 비/눈 판단
    rainy = rain_prob >= 60

    # 요약 문장
    if rainy and rain_prob >= 80:
        summary = f"🌧️ 비 예보 (강수확률 {rain_prob}%) — 우산 필수, 대중교통 권장"
    elif rainy:
        summary = f"🌂 강수확률 {rain_prob}% — 우산 챙기세요"
    elif sky == "맑음":
        summary = f"☀️ {sky}, {temp:.0f}°C — 이동하기 좋은 날씨"
    else:
        summary = f"⛅ {sky}, {temp:.0f}°C"

    return {
        "rain_prob": rain_prob,
        "rainy": rainy,
        "sky": sky,
        "temp": temp,
        "summary": summary,
    }


def _empty_weather() -> dict:
    return {
        "rain_prob": 0,
        "rainy": False,
        "sky": "알 수 없음",
        "temp": 0,
        "summary": "",
    }
