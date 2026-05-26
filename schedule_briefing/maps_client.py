# schedule_briefing/maps_client.py
"""Google Maps Places API — 장소 정보 및 리뷰 조회 (캐시 내장)"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

from core import config

logger = logging.getLogger(__name__)

PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# 리뷰 캐시 (당일 유지, 장소명 → 데이터)
_CACHE_FILE = Path(__file__).parent.parent / "data" / "maps_review_cache.json"
_CACHE_TTL_HOURS = 24


def _api_key() -> str:
    key = config.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise ValueError("GOOGLE_MAPS_API_KEY 환경변수가 설정되지 않았습니다.")
    return key


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"맵 캐시 저장 실패: {e}")


def get_place_info(location_text: str) -> dict:
    """장소명/주소 → 장소 정보 (캐시 우선)

    Returns:
        {
            name: str,
            address: str,
            rating: float | None,
            user_ratings_total: int,
            opening_hours: str | None,   # "영업 중" | "영업 종료" | None
            top_reviews: list[str],      # 상위 3개 리뷰 요약
            types: list[str],            # 장소 카테고리
            place_url: str,
        }
    """
    cache = _load_cache()
    cache_key = location_text.strip()

    # 캐시 히트 확인 (24시간 이내)
    if cache_key in cache:
        cached = cache[cache_key]
        cached_at = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at < timedelta(hours=_CACHE_TTL_HOURS):
            logger.debug(f"맵 캐시 히트: {cache_key}")
            return cached

    try:
        key = _api_key()
    except ValueError:
        logger.warning("Google Maps API 키 없음 — 장소 리뷰 생략")
        return _empty_place(location_text)

    try:
        # 1단계: 장소 검색
        search_resp = requests.get(
            PLACES_SEARCH_URL,
            params={
                "query": location_text,
                "language": "ko",
                "region": "kr",
                "key": key,
            },
            timeout=8,
        )
        search_resp.raise_for_status()
        search_data = search_resp.json()

        results = search_data.get("results", [])
        if not results:
            logger.warning(f"Google Maps 장소 없음: {location_text}")
            return _empty_place(location_text)

        place = results[0]
        place_id = place["place_id"]

        # 2단계: 장소 상세 정보 (리뷰 포함)
        detail_resp = requests.get(
            PLACES_DETAIL_URL,
            params={
                "place_id": place_id,
                "fields": "name,formatted_address,rating,user_ratings_total,"
                          "opening_hours,reviews,types,url",
                "language": "ko",
                "key": key,
            },
            timeout=8,
        )
        detail_resp.raise_for_status()
        detail_data = detail_resp.json().get("result", {})

        # 리뷰 텍스트 상위 3개 (한국어 우선)
        reviews_raw = detail_data.get("reviews", [])
        reviews_raw.sort(key=lambda r: r.get("rating", 0), reverse=True)
        top_reviews = [r.get("text", "")[:200] for r in reviews_raw[:3] if r.get("text")]

        # 영업 시간 현황
        oh = detail_data.get("opening_hours", {})
        if "open_now" in oh:
            opening_status = "영업 중" if oh["open_now"] else "현재 영업 종료"
        else:
            opening_status = None

        info = {
            "name": detail_data.get("name", location_text),
            "address": detail_data.get("formatted_address", ""),
            "rating": detail_data.get("rating"),
            "user_ratings_total": detail_data.get("user_ratings_total", 0),
            "opening_hours": opening_status,
            "top_reviews": top_reviews,
            "types": detail_data.get("types", []),
            "place_url": detail_data.get("url", ""),
            "_cached_at": datetime.now().isoformat(),
        }

        cache[cache_key] = info
        _save_cache(cache)
        return info

    except Exception as e:
        logger.warning(f"Google Maps 장소 조회 실패 ({location_text}): {e}")
        return _empty_place(location_text)


def _empty_place(location_text: str) -> dict:
    return {
        "name": location_text,
        "address": location_text,
        "rating": None,
        "user_ratings_total": 0,
        "opening_hours": None,
        "top_reviews": [],
        "types": [],
        "place_url": "",
    }


def describe_place_type(types: list[str]) -> str:
    """장소 타입 리스트 → 한국어 설명"""
    mapping = {
        "restaurant": "식당",
        "cafe": "카페",
        "hospital": "병원",
        "gym": "헬스장",
        "school": "학교",
        "university": "대학교",
        "bank": "은행",
        "shopping_mall": "쇼핑몰",
        "supermarket": "마트",
        "hotel": "호텔",
        "subway_station": "지하철역",
        "bus_station": "버스터미널",
        "train_station": "기차역",
        "airport": "공항",
        "park": "공원",
        "museum": "박물관",
        "movie_theater": "영화관",
        "beauty_salon": "미용실",
        "pharmacy": "약국",
        "convenience_store": "편의점",
        "gas_station": "주유소",
        "church": "교회",
        "point_of_interest": "명소",
        "establishment": "업체",
    }
    for t in types:
        if t in mapping:
            return mapping[t]
    return "장소"
