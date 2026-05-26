# schedule_briefing/tmap_client.py
"""TMAP API — 미래 시각 기준 소요시간 계산 (자동차 + 대중교통)"""

import logging
from datetime import datetime

import requests

from core import config

logger = logging.getLogger(__name__)

TMAP_ROUTE_URL = "https://apis.openapi.sk.com/tmap/routes"
TMAP_TRANSIT_URL = "https://apis.openapi.sk.com/transit/routes"

# 무료 한도 보호: 요청당 비용이 있으므로 게이트 통과한 것만 호출
_API_KEY = None


def _key() -> str:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = config.get("TMAP_APP_KEY", "")
    if not _API_KEY:
        raise ValueError("TMAP_APP_KEY 환경변수가 설정되지 않았습니다.")
    return _API_KEY


def get_travel_time(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    departure_dt: datetime,
) -> dict:
    """자동차 + 대중교통 소요시간 계산 (미래 시각 기준)

    Args:
        origin_lat/lng: 출발지 좌표
        dest_lat/lng: 목적지 좌표
        departure_dt: 출발 예정 시각 (datetime, KST)

    Returns:
        {
            car_minutes: int,       # 자동차 소요시간
            transit_minutes: int,   # 대중교통 소요시간 (실패 시 None)
            recommended_minutes: int,  # 추천 (둘 중 짧은 것)
            mode: str,              # "자동차" | "대중교통"
            car_ok: bool,
            transit_ok: bool,
        }
    """
    car_minutes = _get_car_time(origin_lat, origin_lng, dest_lat, dest_lng, departure_dt)
    transit_minutes = _get_transit_time(origin_lat, origin_lng, dest_lat, dest_lng, departure_dt)

    car_ok = car_minutes is not None
    transit_ok = transit_minutes is not None

    if car_ok and transit_ok:
        if transit_minutes <= car_minutes:
            recommended = transit_minutes
            mode = "대중교통"
        else:
            recommended = car_minutes
            mode = "자동차"
    elif car_ok:
        recommended = car_minutes
        mode = "자동차"
    elif transit_ok:
        recommended = transit_minutes
        mode = "대중교통"
    else:
        # 두 방법 모두 실패 — 기본값 30분
        logger.warning("TMAP 양쪽 모두 실패 — 기본값 30분 사용")
        recommended = 30
        mode = "기본값"

    return {
        "car_minutes": car_minutes,
        "transit_minutes": transit_minutes,
        "recommended_minutes": recommended,
        "mode": mode,
        "car_ok": car_ok,
        "transit_ok": transit_ok,
    }


def _get_car_time(
    o_lat: float, o_lng: float,
    d_lat: float, d_lng: float,
    departure_dt: datetime,
) -> int | None:
    """TMAP 자동차 경로 소요시간 (분)"""
    try:
        # TMAP 출발시각 포맷: YYYYMMDDHHmm
        depart_str = departure_dt.strftime("%Y%m%d%H%M")

        resp = requests.post(
            TMAP_ROUTE_URL,
            headers={
                "appKey": _key(),
                "Content-Type": "application/json",
            },
            json={
                "startX": str(o_lng),
                "startY": str(o_lat),
                "endX": str(d_lng),
                "endY": str(d_lat),
                "reqCoordType": "WGS84GEO",
                "resCoordType": "WGS84GEO",
                "trafficInfo": "Y",
                "departureTime": depart_str,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        features = data.get("features", [])
        if not features:
            return None

        props = features[0].get("properties", {})
        total_time = props.get("totalTime", 0)  # 초 단위
        return max(1, round(total_time / 60))

    except Exception as e:
        logger.warning(f"TMAP 자동차 경로 실패: {e}")
        return None


def _get_transit_time(
    o_lat: float, o_lng: float,
    d_lat: float, d_lng: float,
    departure_dt: datetime,
) -> int | None:
    """TMAP 대중교통 소요시간 (분)"""
    try:
        # 대중교통 API는 시각을 ISO8601 형식으로
        depart_str = departure_dt.strftime("%Y%m%d%H%M")

        resp = requests.post(
            TMAP_TRANSIT_URL,
            headers={
                "appKey": _key(),
                "Content-Type": "application/json",
            },
            json={
                "startX": str(o_lng),
                "startY": str(o_lat),
                "endX": str(d_lng),
                "endY": str(d_lat),
                "lang": 0,
                "format": "json",
                "count": 1,
                "searchDttm": depart_str,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        itineraries = data.get("metaData", {}).get("plan", {}).get("itineraries", [])
        if not itineraries:
            return None

        # 첫 번째 경로의 totalTime (초)
        total_time = itineraries[0].get("totalTime", 0)
        return max(1, round(total_time / 60))

    except Exception as e:
        logger.warning(f"TMAP 대중교통 경로 실패: {e}")
        return None


def geocode_address(address: str) -> tuple[float, float] | None:
    """TMAP 주소 → 좌표 변환 (장소 이름으로 좌표 획득)

    Returns:
        (lat, lng) or None
    """
    try:
        resp = requests.get(
            "https://apis.openapi.sk.com/tmap/geo/geocoding",
            params={
                "version": 1,
                "format": "json",
                "city_do": "",
                "gu_gun": "",
                "dong": "",
                "bunji": "",
                "addressFlag": "F00",
                "fullAddr": address,
                "coordType": "WGS84GEO",
            },
            headers={"appKey": _key()},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        coord_info = data.get("coordinateInfo", {})
        lat = coord_info.get("lat") or coord_info.get("newLat")
        lng = coord_info.get("lon") or coord_info.get("newLon")

        if lat and lng:
            return float(lat), float(lng)
        return None

    except Exception as e:
        logger.warning(f"TMAP 지오코딩 실패 ({address}): {e}")
        return None


def pois_search(keyword: str) -> tuple[float, float] | None:
    """TMAP POI 검색으로 장소명 → 좌표 획득

    Returns:
        (lat, lng) or None
    """
    try:
        resp = requests.get(
            "https://apis.openapi.sk.com/tmap/pois",
            params={
                "version": 1,
                "format": "json",
                "searchKeyword": keyword,
                "searchType": "all",
                "page": 1,
                "count": 1,
                "resCoordType": "WGS84GEO",
            },
            headers={"appKey": _key()},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        pois = data.get("searchPoiInfo", {}).get("pois", {}).get("poi", [])
        if not pois:
            return None

        first = pois[0]
        lat = float(first.get("frontLat") or first.get("noorLat", 0))
        lng = float(first.get("frontLon") or first.get("noorLon", 0))
        if lat and lng:
            return lat, lng
        return None

    except Exception as e:
        logger.warning(f"TMAP POI 검색 실패 ({keyword}): {e}")
        return None
