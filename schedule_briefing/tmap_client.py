# schedule_briefing/tmap_client.py
"""이동수단별 소요시간 + 장소 검색 / 지오코딩

  자동차 경로  : Kakao Mobility  GET apis-navi.kakaomobility.com/v1/directions
  대중교통 경로: ODsay LIVE       GET api.odsay.com/v1/api/searchPubTransPathT
  장소 키워드  : Kakao Local      GET dapi.kakao.com/v2/local/search/keyword.json
  주소 → 좌표  : Kakao Local      GET dapi.kakao.com/v2/local/search/address.json

인증: 카카오 = Authorization: KakaoAK {KAKAO_REST_API_KEY} 헤더
      ODsay  = apiKey 쿼리 파라미터 {ODSAY_API_KEY}
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
_ODSAY_PUBTRANS = "https://api.odsay.com/v1/api/searchPubTransPathT"  # 대중교통 (ODsay)

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
    """이동수단별 소요시간 계산 → 최단시간 수단 추천

    - 자동차: 카카오 Mobility (항상 조회)
    - 대중교통: ODsay LIVE API (ODSAY_API_KEY 있으면 자동 조회, door-to-door)
    - 도보/자전거: 카카오 제휴 API 전용 — 제휴 계약 전까지 미구현(None).
      제휴 확보 후 KAKAO_MULTIMODAL=1 로 활성화.
    추천 정책: 성공한 수단 중 무조건 최단시간 (실패 수단은 후보에서 제외).

    Returns:
        {
            car_minutes: int | None,
            transit_minutes: int | None,
            walk_minutes: int | None,
            bike_minutes: int | None,
            options: dict[str, int],        # 성공한 수단별 소요시간(분)
            recommended_minutes: int,
            mode: str,
            car_ok: bool,
            transit_ok: bool,
        }
    """
    args = (origin_lat, origin_lng, dest_lat, dest_lng, departure_dt)
    options: dict[str, int] = {}

    car_minutes = _get_car_time(*args)
    if car_minutes is not None:
        options["자동차"] = car_minutes

    # 대중교통은 ODSAY_API_KEY 유무로 자기-게이팅 (키 없으면 None → 후보 제외)
    transit_minutes = _get_transit_time(*args)
    if transit_minutes is not None:
        options["대중교통"] = transit_minutes

    # 도보/자전거는 카카오 제휴 API 확보 후 KAKAO_MULTIMODAL 플래그로 활성화
    walk_minutes = bike_minutes = None
    if _multimodal_enabled():
        walk_minutes = _get_walk_time(*args)
        bike_minutes = _get_bike_time(*args)
        for mode_name, minutes in (("도보", walk_minutes), ("자전거", bike_minutes)):
            if minutes is not None:
                options[mode_name] = minutes

    if options:
        # 최단시간 수단 (동률이면 삽입 순서상 자동차 우선)
        mode = min(options, key=options.get)
        recommended = options[mode]
    else:
        logger.warning("모든 이동수단 경로 실패 — 기본값 30분 사용")
        recommended = 30
        mode = "기본값"

    return {
        "car_minutes": car_minutes,
        "transit_minutes": transit_minutes,
        "walk_minutes": walk_minutes,
        "bike_minutes": bike_minutes,
        "options": options,
        "recommended_minutes": recommended,
        "mode": mode,
        "car_ok": car_minutes is not None,
        "transit_ok": transit_minutes is not None,
    }


def _multimodal_enabled() -> bool:
    """신규 다중수단 API 활성화 여부 (.env KAKAO_MULTIMODAL=1)"""
    return config.get_bool("KAKAO_MULTIMODAL", False)


# ── 대중교통 (ODsay LIVE API) ────────────────────────────────────────────────

def _get_transit_time(
    o_lat: float, o_lng: float, d_lat: float, d_lng: float, departure_dt: datetime,
) -> int | None:
    """대중교통 소요시간 (분) — ODsay LIVE API (지하철+버스, door-to-door)

    첫/끝 도보 구간을 포함한 totalTime을 반환. OPT=4(최소시간)로 조회하고
    복수 경로 중 최소값 사용 (최단시간 추천 정책과 일치).
    ODSAY_API_KEY 없으면 None → 추천 후보에서 제외.

    Note: ODsay 서버 키는 호출 IP 화이트리스트 등록 필요 (https://lab.odsay.com).
    """
    api_key = config.get("ODSAY_API_KEY", "")
    if not api_key:
        return None
    try:
        resp = requests.get(
            _ODSAY_PUBTRANS,
            params={
                "apiKey": api_key,
                "SX": o_lng, "SY": o_lat,      # 출발 경도,위도
                "EX": d_lng, "EY": d_lat,      # 도착 경도,위도
                "OPT": 4,                       # 최소시간
                "SearchPathType": 0,            # 지하철+버스
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            logger.warning(f"ODsay 대중교통 오류: {data.get('error')}")
            return None

        paths = data.get("result", {}).get("path", [])
        times = [p.get("info", {}).get("totalTime", 0) for p in paths]
        times = [t for t in times if t]
        if not times:
            return None
        return max(1, min(times))

    except Exception as e:
        logger.warning(f"ODsay 대중교통 실패: {e}")
        return None


# ── 도보/자전거 — 카카오 제휴 API 전용 (제휴 계약 전까지 미구현) ──
# 2026-07-21 확인: 카카오 대중교통/도보/자전거 길찾기는 공개 REST가 아니라
# 제휴(파트너십) API. 계약 후 스펙 확보 시 아래 두 함수 구현 + KAKAO_MULTIMODAL=1.
# 상세: docs/kakao_newapi_spec_probe.md

def _get_walk_time(
    o_lat: float, o_lng: float, d_lat: float, d_lng: float, departure_dt: datetime,
) -> int | None:
    """도보 소요시간 (분) — 카카오 제휴 API 확보 전 미구현"""
    return None


def _get_bike_time(
    o_lat: float, o_lng: float, d_lat: float, d_lng: float, departure_dt: datetime,
) -> int | None:
    """자전거 소요시간 (분) — 카카오 제휴 API 확보 전 미구현"""
    return None


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
