# tests/test_kis_client.py
"""KISClient.get_prices() fail-fast 및 get_top_trade_value 위임 회귀 테스트

실행: python3 -m unittest tests.test_kis_client -v
네트워크·환경변수 불필요 (KISClient.__new__로 __init__ 우회).
"""

import unittest
from unittest.mock import patch

from core.kis_client import KISClient


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


if __name__ == "__main__":
    unittest.main()
