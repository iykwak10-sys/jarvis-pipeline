# tests/test_kis_client.py
"""KISClient.get_prices() fail-fast 및 get_top_trade_value 위임 회귀 테스트

실행: python3 -m unittest tests.test_kis_client -v
네트워크·환경변수 불필요 (KISClient.__new__로 __init__ 우회).
"""

import json
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import requests

from core.kis_client import BASE_URL, KISClient, TokenManager
from core.kis_token_cache import SharedTokenCache


def _bare_client() -> KISClient:
    """env/토큰 의존 없는 KISClient 인스턴스 (테스트 전용)"""
    return KISClient.__new__(KISClient)


def _ok(code: str) -> dict:
    return {"code": code, "close": 1000, "change": 10,
            "change_pct": 1.0, "volume": 100,
            "high": 1010, "low": 990, "open": 995}


class TestGetPricesFailFast(unittest.TestCase):

    def setUp(self):
        # 0.1초 간격 대기 제거 (테스트 속도)
        patcher = patch("core.kis_client.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_success_preserves_order_and_length(self):
        client = _bare_client()
        codes = ["005930", "000660", "042700"]
        with patch.object(KISClient, "get_price", side_effect=_ok):
            results = client.get_prices(codes)
        self.assertEqual([r["code"] for r in results], codes)
        self.assertTrue(all(r["close"] == 1000 for r in results))

    def test_consecutive_failures_trigger_early_abort(self):
        client = _bare_client()
        codes = [f"{i:06d}" for i in range(10)]
        with patch.object(
            KISClient, "get_price", side_effect=ConnectionError("DNS 실패")
        ) as mock_get:
            results = client.get_prices(codes)
        # 연속 3개 실패 후 조기 중단 — API 호출은 3번만
        self.assertEqual(mock_get.call_count, 3)
        # 잔여 종목은 더미로 채워 결과 길이 보존
        self.assertEqual(len(results), len(codes))
        self.assertTrue(all(r["close"] == 0 for r in results))
        self.assertEqual([r["code"] for r in results], codes)

    def test_success_resets_consecutive_counter(self):
        client = _bare_client()
        codes = ["A", "B", "C", "D", "E", "F"]
        # 실패-실패-성공-실패-실패-성공: 연속 3회에 도달하지 않음
        effects = [ConnectionError(), ConnectionError(), _ok("C"),
                   ConnectionError(), ConnectionError(), _ok("F")]
        with patch.object(
            KISClient, "get_price", side_effect=effects
        ) as mock_get:
            results = client.get_prices(codes)
        # 조기 중단 없이 전 종목 시도
        self.assertEqual(mock_get.call_count, 6)
        self.assertEqual(len(results), 6)
        self.assertEqual(results[2]["close"], 1000)
        self.assertEqual(results[5]["close"], 1000)

    def test_custom_fail_fast_threshold(self):
        client = _bare_client()
        codes = [f"{i:06d}" for i in range(5)]
        with patch.object(
            KISClient, "get_price", side_effect=ConnectionError()
        ) as mock_get:
            results = client.get_prices(codes, fail_fast_after=1)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(results), len(codes))


class TestTopTradeValueDelegation(unittest.TestCase):
    """커밋 114a992a 회귀: get_top_trade_value_codes가 신규 메서드에 위임"""

    def test_codes_delegates_to_named_variant(self):
        client = _bare_client()
        fake = [{"code": "005930", "name": "삼성전자"},
                {"code": "000660", "name": "SK하이닉스"}]
        with patch.object(KISClient, "get_top_trade_value", return_value=fake):
            codes = client.get_top_trade_value_codes(market="J", top_n=2)
        self.assertEqual(codes, ["005930", "000660"])


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")


class _TokenAwareSession:
    """authorization 헤더의 토큰에 따라 응답이 갈리는 가짜 세션.

    stale 토큰 → KIS 실제 장애 응답(HTTP 500 + EGW00123),
    fresh 토큰 → 정상 시세 응답.
    """

    def __init__(self, fresh_token: str):
        self.fresh_token = fresh_token
        self.calls: list = []

    def get(self, url, headers=None, params=None, timeout=None):
        token = (headers or {}).get("authorization", "")
        self.calls.append(token)
        if token == f"Bearer {self.fresh_token}":
            return _FakeResponse(200, {"output": {
                "stck_prpr": "299000", "prdy_vrss": "-3500",
                "prdy_ctrt": "-1.16", "acml_vol": "12345",
                "stck_hgpr": "301000", "stck_lwpr": "297000",
                "stck_oprc": "300000",
            }})
        return _FakeResponse(500, {
            "rt_cd": "1", "msg_cd": "EGW00123",
            "msg1": "기간이 만료된 token 입니다.",
        })


class TestServerSideTokenExpiryRecovery(unittest.TestCase):
    """2026-06-11 마감수집 0/17 장애 회귀.

    같은 app key를 쓰는 다른 프로세스가 새 토큰을 발급하면 KIS가 기존
    토큰을 서버측에서 즉시 무효화한다(EGW00123). 로컬 캐시 expires_at이
    미래라도 토큰은 이미 죽어 있으므로, 클라이언트는 EGW00123 응답을
    감지해 캐시를 버리고 재발급으로 스스로 복구해야 한다.
    """

    def setUp(self):
        patcher = patch("core.retry.time.sleep")  # 재시도 백오프 제거
        patcher.start()
        self.addCleanup(patcher.stop)

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.cache_path = tmp / ".kis_token.json"
        self.cache_path.write_text(
            json.dumps({"token": "stale", "expires_at": "2999-01-01T00:00:00"}),
            encoding="utf-8",
        )
        env_patcher = patch.dict(
            "os.environ", {"KIS_APP_KEY": "k", "KIS_APP_SECRET": "s"}
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _client_with_stale_token(self):
        client = _bare_client()
        tm = TokenManager("k", "s")
        tm._shared_cache = SharedTokenCache(
            "k", "https://openapi.koreainvestment.com:9443",
            lambda: {"access_token": "fresh", "expires_in": 86400},
            cache_path=self.cache_path,
            min_issue_interval=0,
        )
        tm._token = "stale"
        tm._expires = datetime.now() + timedelta(hours=12)  # 로컬상 '유효'

        def _issue_fresh():
            tm._token = "fresh"
            tm._expires = datetime.now() + timedelta(hours=24)
            return "fresh"

        tm._shared_cache.issue_token = lambda: {
            "access_token": _issue_fresh(), "expires_in": 86400
        }
        client._token_mgr = tm
        session = _TokenAwareSession(fresh_token="fresh")
        client._session = session
        return client, session

    def test_get_price_recovers_from_egw00123(self):
        client, session = self._client_with_stale_token()
        result = client.get_price("005930")
        self.assertEqual(result["close"], 299000)
        # stale 토큰으로 실패 → 캐시 폐기 → fresh 토큰 재발급으로 성공
        self.assertIn("Bearer stale", session.calls)
        self.assertEqual(session.calls[-1], "Bearer fresh")

    def test_egw00123_replaces_invalidated_cache_with_fresh_token(self):
        client, _ = self._client_with_stale_token()
        client.get_price("005930")
        document = json.loads(self.cache_path.read_text(encoding="utf-8"))
        cache = tm._shared_cache._entry_unlocked(document)
        self.assertEqual(cache["token"], "fresh")
        self.assertFalse(cache["invalidated"])

    def test_invalidate_clears_memory_and_file(self):
        tm = TokenManager("k", "s")
        tm._shared_cache = SharedTokenCache(
            "k", "https://openapi.koreainvestment.com:9443",
            lambda: {}, cache_path=self.cache_path, min_issue_interval=0,
        )
        tm._token = "stale"
        tm._expires = datetime.now() + timedelta(hours=12)
        tm.invalidate()
        self.assertIsNone(tm._token)
        self.assertIsNone(tm._expires)
        document = json.loads(self.cache_path.read_text())
        self.assertTrue(tm._shared_cache._entry_unlocked(document)["invalidated"])


class TestSharedTokenCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache_path = self.tmp / "token_cache.json"

    def test_concurrent_consumers_issue_only_once(self):
        calls = 0
        calls_lock = threading.Lock()

        def issue():
            nonlocal calls
            with calls_lock:
                calls += 1
            return {"access_token": "shared", "expires_in": 86400}

        managers = [
            SharedTokenCache("k", BASE_URL, issue, self.cache_path, 0)
            for _ in range(4)
        ]
        results = [None] * len(managers)
        threads = [
            threading.Thread(target=lambda i=i: results.__setitem__(i, managers[i].get_token()))
            for i in range(len(managers))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results, ["shared"] * 4)
        self.assertEqual(calls, 1)

    def test_stale_consumer_cannot_invalidate_newer_shared_token(self):
        manager = SharedTokenCache("k", BASE_URL, lambda: {}, self.cache_path, 0)
        manager._write_entry_unlocked({}, {
            "token": "new", "expires_at": "2999-01-01T00:00:00+00:00",
            "issued_at": "2026-06-11T10:00:00+00:00", "base_url": BASE_URL,
            "app_key_hash": manager.app_key_hash, "invalidated": False,
        })
        self.assertFalse(manager.invalidate("old"))
        self.assertEqual(manager.get_token(), "new")

    def test_different_app_keys_do_not_overwrite_each_other(self):
        first = SharedTokenCache(
            "key-a", BASE_URL, lambda: {"access_token": "a", "expires_in": 86400},
            self.cache_path, 0,
        )
        second = SharedTokenCache(
            "key-b", BASE_URL, lambda: {"access_token": "b", "expires_in": 86400},
            self.cache_path, 0,
        )
        self.assertEqual(first.get_token(), "a")
        self.assertEqual(second.get_token(), "b")
        self.assertEqual(first.get_token(), "a")


if __name__ == "__main__":
    unittest.main()
