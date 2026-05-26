# schedule_briefing/tmap_client.py
"""카카오 API — 소요시간 계산 + 장소 검색 / 지오코딩
  (구 TMAP → Kakao Mobility + Kakao Local 으로 대체)

엔드포인트:
  자동차 경로  : GET https://apis-navi.kakaomobility.com/v1/directions
  장소 키워드  : GET https://dapi.kakao.com/v2/local/search/keyword.json
  주소 → 좌표  : GET https://dapi.kakao.com/v2/local/search/address.json

헤더 공통: Authorization: KakaoAK {REST_API_KEY}
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from core import config

logger = logging.getLogger(__name__)

_MOBILITY_BASE = "https://apis-navi.kakaomobility.com/v1/directions"
_LOCAL_KEYWORD = "https://dapi.kakao.com/v2/local/search/keyword.json"
_LOCAL_ADDRESS = "https://dapi.kakao.com/v2/local/search/address.json"

_API_KEY: str | None = None


def _key() -> str:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = config.get("KAKAO_REST_API_KEY", "")
    if not _API_KEY:
        raise ValueError("KAKAO_REST_API_KEY 환경변수가 설정되지 않았습니다.")
    return _API_KEY


def _headers() -> dict:
    return {"Authorization": f"KakaoAK {_key()}"}


# ── 소요시간 계산 ───────────────────────────────────────────────────────────

def get_travel_time(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    departure_dt: datetime,
) -> dict:
    """자동차 소요시간 계산 (미래 출발 시각 기준)

    Returns:
        {
            car_minutes: int,
            transit_minutes: None,          # 카카오는 대중교통 REST API 미제공
            recommended_minutes: int,
            mode: str,
            car_ok: bool,
            transit_ok: bool,
        }
    """
    car_minutes = _get_car_time(origin_lat, origin_lng, dest_lat, dest_lng, departure_dt)
    car_ok = car_minutes is not None

    if car_ok:
        recommended = car_minutes
        mode = "자동차"
    else:
        logger.warning("카카오 자동차 경로 실패 — 기본값 30분 사용")
        recommended = 30
        mode = "기본값"

    return {
        "car_minutes": car_minutes,
        "transit_minutes": None,    # 카카오 Mobility는 대중교통 미지원
        "recommended_minutes": recommended,
        "mode": mode,
        "car_ok": car_ok,
        "transit_ok": False,
    }


def _get_car_time(
    o_lat: float, o_lng: float,
    d_lat: float, d_lng: float,
    departure_dt: datetime,
) -> int | None:
    """카카오 Mobility 자동차 경로 소요시간 (분)

    departure_time 포맷: YYYYMMDDHHmmss (14자리)
    """
    try:
        depart_str = departure_dt.strftime("%Y%m%d%H%M%S")

        resp = requests.get(
            _MOBILITY_BASE,
            headers=_headers(),
            params={
                "origin": f"{o_lng},{o_lat}",          # 경도,위도 순서
                "destination": f"{d_lng},{d_lat}",
                "priority": "RECOMMEND",
                "departure_time": depart_str,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        routes = data.get("routes", [])
        if not routes:
            return None

        summary = routes[0].get("summary", {})
        duration_sec = summary.get("duration", 0)   # 초 단위
        return max(1, round(duration_sec / 60))

    except Exception as e:
        logger.warning(f"카카오 자동차 경로 실패: {e}")
        return None


# ── 장소 검색 / 지오코딩 ────────────────────────────────────────────────────

def pois_search(keyword: str) -> tuple[float, float] | None:
    """카카오 장소 키워드 검색 → (lat, lng) 반환

    Note: 카카오 Developers 포털에서 'OPEN_MAP_AND_LOCAL' 서비스 활성화 필요
    """
    try:
        resp = requests.get(
            _LOCAL_KEYWORD,
            headers=_headers(),
            params={"query": keyword, "size": 1},
            timeout=8,
        )
        resp.raise_for_status()
        docs = resp.json().get("documents", [])
        if not docs:
            return None

        d = docs[0]
        lat = float(d.get("y", 0))
        lng = float(d.get("x", 0))
        if lat and lng:
            logger.debug(f"카카오 POI: {d.get('place_name')} ({lat}, {lng})")
            return lat, lng
        return None

    except Exception as e:
        logger.warning(f"카카오 장소 검색 실패 ({keyword}): {e}")
        return None


def geocode_address(address: str) -> tuple[float, float] | None:
    """카카오 주소 검색 → (lat, lng) 반환

    Note: 카카오 Developers 포털에서 'OPEN_MAP_AND_LOCAL' 서비스 활성화 필요
    """
    try:
        resp = requests.get(
            _LOCAL_ADDRESS,
            headers=_headers(),
            params={"query": address},
            timeout=8,
        )
        resp.raise_for_status()
        docs = resp.json().get("documents", [])
        if not docs:
            return None

        d = docs[0]
        lat = float(d.get("y", 0))
        lng = float(d.get("x", 0))
        if lat and lng:
            return lat, lng
        return None

    except Exception as e:
        logger.warning(f"카카오 주소 검색 실패 ({address}): {e}")
        return None
