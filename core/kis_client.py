# core/kis_client.py
"""한국투자증권(KIS) REST API 클라이언트 및 토큰 관리"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from requests.exceptions import ConnectionError, Timeout

from core.retry import retry

BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_URL=f"{BASE_URL}/oauth2/tokenP"
PRICE_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
INDEX_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
TOKEN_CACHE=Path(__file__).parent.parent / "data" / ".kis_token.json"

logger = logging.getLogger(__name__)


class TokenManager:
    """KIS API 토큰 발급 및 캐시 관리"""

    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._expires: Optional[datetime] = None

    def get_token(self) -> str:
        if self._is_valid():
            return self._token
        return self._issue_token()

    def _is_valid(self) -> bool:
        if self._token and self._expires and datetime.now() < self._expires - timedelta(minutes=10):
            return True
        if TOKEN_CACHE.exists():
            try:
                cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
                exp = datetime.fromisoformat(cache["expires_at"])
                if datetime.now() < exp - timedelta(minutes=10):
                    self._token = cache["token"]
                    self._expires = exp
                    logger.info("캐시된 KIS 토큰 사용")
                    return True
            except Exception:
                pass
        return False

    @retry(max_attempts=3, base_delay=1.0, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def _issue_token(self) -> str:
        resp = requests.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._expires = datetime.now() + timedelta(seconds=86400)
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "token": self._token,
            "expires_at": self._expires.isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
        logger.info("KIS 토큰 발급 완료")
        return self._token


class KISClient:
    """KIS REST API 래퍼"""

    def __init__(self):
        from core.config import KIS_APP_KEY, KIS_APP_SECRET
        self._token_mgr = TokenManager(
            KIS_APP_KEY,
            KIS_APP_SECRET,
        )

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token_mgr.get_token()}",
            "appkey": os.environ["KIS_APP_KEY"],
            "appsecret": os.environ["KIS_APP_SECRET"],
            "tr_id": "FHKST01010100",
        }

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_price(self, code: str) -> dict:
        """단일 종목 현재가 조회. 반환: {code, close, change, change_pct, volume, high, low, open}"""
        resp = requests.get(PRICE_URL, headers=self._headers(), params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        resp.raise_for_status()
        output = resp.json().get("output", {})
        return {
            "code": code,
            "close": int(output.get("stck_prpr", 0)),
            "change": int(output.get("prdy_vrss", 0)),
            "change_pct": float(output.get("prdy_ctrt", 0)),
            "volume": int(output.get("acml_vol", 0)),
            "high": int(output.get("stck_hgpr", 0)),
            "low": int(output.get("stck_lwpr", 0)),
            "open": int(output.get("stck_oprc", 0)),
        }

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_index_price(self, iscd: str) -> dict:
        """국내 지수 현재가(직전 영업일 포함) 조회.
        iscd: '0001'=KOSPI, '1001'=KOSDAQ
        반환: {iscd, current, change, change_pct, sign}
          sign: 1=상한, 2=상승, 3=보합, 4=하한, 5=하락
        """
        today = datetime.now().strftime("%Y%m%d")
        headers = self._headers()
        headers["tr_id"] = "FHKUP03500100"
        resp = requests.get(INDEX_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "U",
            "fid_input_iscd": iscd,
            "fid_input_date_1": today,
            "fid_input_date_2": today,
            "fid_period_div_code": "D",
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        output = data.get("output1") or data.get("output") or {}
        return {
            "iscd": iscd,
            "current": float(output.get("bstp_nmix_prpr", 0)),
            "change": float(output.get("bstp_nmix_prdy_vrss", 0)),
            "change_pct": float(output.get("bstp_nmix_prdy_ctrt", 0)),
            "sign": output.get("prdy_vrss_sign", "3"),  # 3=보합
        }

    def get_prices(self, codes: list) -> list:
        """여러 종목 배치 조회 (0.1초 간격, 초당 20건 제한 준수)"""
        results = []
        for code in codes:
            try:
                results.append(self.get_price(code))
            except Exception as e:
                logger.error(f"종목 {code} 조회 실패: {e}")
                results.append({"code": code, "close": 0, "change": 0,
                                 "change_pct": 0.0, "volume": 0,
                                 "high": 0, "low": 0, "open": 0})
            time.sleep(0.1)
        return results
