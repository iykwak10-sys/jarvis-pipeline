# tests/test_travel_recommend.py
"""tmap_client.get_travel_time 이동수단 추천 로직 테스트 (API mock)

정책: 성공한 수단 중 무조건 최단시간. 실패(None) 수단은 후보 제외.
전부 실패 시 기본값 30분.
대중교통은 ODsay 키로 자기-게이팅(항상 시도), 도보/자전거는 KAKAO_MULTIMODAL 플래그.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from schedule_briefing import tmap_client

ARGS = (36.4761, 127.2520, 36.3504, 127.3845, datetime(2026, 7, 21, 9, 0))


def _patch(monkeypatch, car=None, transit=None, walk=None, bike=None, multimodal=True):
    monkeypatch.setattr(tmap_client, "_get_car_time", lambda *a: car)
    monkeypatch.setattr(tmap_client, "_get_transit_time", lambda *a: transit)
    monkeypatch.setattr(tmap_client, "_get_walk_time", lambda *a: walk)
    monkeypatch.setattr(tmap_client, "_get_bike_time", lambda *a: bike)
    monkeypatch.setattr(tmap_client, "_multimodal_enabled", lambda: multimodal)


def test_car_only_no_transit_no_flag(monkeypatch):
    """ODsay 키 없음(transit=None) + 도보/자전거 플래그 꺼짐 → 자동차 단독.
    도보/자전거 fetcher는 호출되면 안 됨."""
    def _forbidden(*a):
        raise AssertionError("walk/bike 플래그 꺼짐 상태에서 호출됨")

    monkeypatch.setattr(tmap_client, "_get_car_time", lambda *a: 37)
    monkeypatch.setattr(tmap_client, "_get_transit_time", lambda *a: None)
    monkeypatch.setattr(tmap_client, "_get_walk_time", _forbidden)
    monkeypatch.setattr(tmap_client, "_get_bike_time", _forbidden)
    monkeypatch.setattr(tmap_client, "_multimodal_enabled", lambda: False)

    r = tmap_client.get_travel_time(*ARGS)
    assert r["mode"] == "자동차"
    assert r["recommended_minutes"] == 37
    assert r["options"] == {"자동차": 37}
    assert r["transit_minutes"] is None


def test_transit_without_flag(monkeypatch):
    """대중교통은 플래그와 무관하게 동작 (ODsay 자기-게이팅)"""
    monkeypatch.setattr(tmap_client, "_get_car_time", lambda *a: 40)
    monkeypatch.setattr(tmap_client, "_get_transit_time", lambda *a: 33)
    monkeypatch.setattr(tmap_client, "_multimodal_enabled", lambda: False)
    r = tmap_client.get_travel_time(*ARGS)
    assert r["mode"] == "대중교통"
    assert r["recommended_minutes"] == 33
    assert r["transit_ok"] is True


def test_transit_fastest(monkeypatch):
    """대중교통이 최단이면 대중교통 추천"""
    _patch(monkeypatch, car=50, transit=35, walk=120, bike=60)
    r = tmap_client.get_travel_time(*ARGS)
    assert r["mode"] == "대중교통"
    assert r["recommended_minutes"] == 35
    assert r["transit_ok"] is True


def test_failed_mode_excluded(monkeypatch):
    """실패(None) 수단은 후보에서 제외 — 남은 것 중 최단"""
    _patch(monkeypatch, car=None, transit=45, walk=None, bike=40)
    r = tmap_client.get_travel_time(*ARGS)
    assert r["mode"] == "자전거"
    assert r["recommended_minutes"] == 40
    assert r["car_ok"] is False
    assert "자동차" not in r["options"]


def test_all_failed_fallback(monkeypatch):
    """전부 실패 → 기본값 30분"""
    _patch(monkeypatch, car=None, transit=None, walk=None, bike=None)
    r = tmap_client.get_travel_time(*ARGS)
    assert r["mode"] == "기본값"
    assert r["recommended_minutes"] == 30
    assert r["options"] == {}


def test_tie_prefers_car(monkeypatch):
    """동률이면 자동차 우선 (삽입 순서)"""
    _patch(monkeypatch, car=30, transit=30, walk=30, bike=30)
    r = tmap_client.get_travel_time(*ARGS)
    assert r["mode"] == "자동차"
    assert r["recommended_minutes"] == 30


def test_backward_compat_keys(monkeypatch):
    """기존 호출부(planner/dispatcher)가 쓰는 키 유지"""
    _patch(monkeypatch, car=37, multimodal=False)
    r = tmap_client.get_travel_time(*ARGS)
    for key in ("car_minutes", "transit_minutes", "recommended_minutes",
                "mode", "car_ok", "transit_ok", "options"):
        assert key in r
