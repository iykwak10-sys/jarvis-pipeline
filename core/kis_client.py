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
INVESTOR_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
DAILY_PRICE_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
VOLUME_RANK_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
TOKEN_CACHE=Path(__file__).parent.parent / "data" / ".kis_token.json"

logger = logging.getLogger(__name__)


def _si(val, default: int = 0) -> int:
    """빈 문자열·None을 0으로 폴백하는 안전한 int 변환 (장중 KIS API 빈 문자열 대응)"""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _sf(val, default: float = 0.0) -> float:
    """빈 문자열·None을 0.0으로 폴백하는 안전한 float 변환"""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


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
            "close": _si(output.get("stck_prpr")),
            "change": _si(output.get("prdy_vrss")),
            "change_pct": _sf(output.get("prdy_ctrt")),
            "volume": _si(output.get("acml_vol")),
            "high": _si(output.get("stck_hgpr")),
            "low": _si(output.get("stck_lwpr")),
            "open": _si(output.get("stck_oprc")),
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
            "current": _sf(output.get("bstp_nmix_prpr")),
            "change": _sf(output.get("bstp_nmix_prdy_vrss")),
            "change_pct": _sf(output.get("bstp_nmix_prdy_ctrt")),
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

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_price_full(self, code: str) -> dict:
        """단일 종목 전체 지표 조회 (주도주 스캐너용)
        추가 필드: acml_tr_pbmn(거래대금), prdy_vol(전일거래량),
                   d250_hgpr(52주고가), hts_avls(시가총액억), name(종목명)
        """
        resp = requests.get(PRICE_URL, headers=self._headers(), params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        resp.raise_for_status()
        o = resp.json().get("output", {})
        return {
            "code": code,
            "name": o.get("hts_kor_isnm", code),
            "close": _si(o.get("stck_prpr")),
            "change_pct": _sf(o.get("prdy_ctrt")),
            "volume": _si(o.get("acml_vol")),
            "vol_rate": _sf(o.get("prdy_vrss_vol_rate")),  # 전일 대비 거래량 비율(%)
            "trade_value_m": _si(o.get("acml_tr_pbmn")),  # 단위: 원
            "high52": _si(o.get("d250_hgpr")),
            "market_cap_100m": _si(o.get("hts_avls")),    # 단위: 억원
            "foreign_ratio": _sf(o.get("hts_frgn_ehrt")),
            "high": _si(o.get("stck_hgpr")),
            "low": _si(o.get("stck_lwpr")),
        }

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_investor_history(self, code: str) -> list:
        """외국인·기관·개인 순매수 히스토리 반환 (최근 30일, 최신순)
        각 항목: {date, frgn_qty, orgn_qty, indv_qty}
        """
        headers = self._headers()
        headers["tr_id"] = "FHKST01010900"
        resp = requests.get(INVESTOR_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("output", [])
        return [
            {
                "date": item.get("stck_bsop_date", ""),
                "frgn_qty": _si(item.get("frgn_ntby_qty")),
                "orgn_qty": _si(item.get("orgn_ntby_qty")),
                "indv_qty": _si(item.get("indv_ntby_qty")),
            }
            for item in items
        ]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_daily_close_prices(self, code: str, n: int = 120) -> list:
        """일봉 종가 리스트 반환 (oldest→newest 정렬, MA/RSI 계산용)

        Returns:
            list[float]: 최대 n개 종가 (오래된 순 → 최신 순)
        """
        headers = self._headers()
        headers["tr_id"] = "FHKST01010400"
        resp = requests.get(DAILY_PRICE_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
            "fid_input_date_1": "",
            "fid_input_date_2": "",
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "1",
        }, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("output", [])[:n]
        closes = [_sf(item.get("stck_clpr")) for item in items]
        closes = [c for c in closes if c > 0]
        closes.reverse()  # KIS는 최신순 반환 → oldest first로 뒤집기
        return closes

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_top_trade_value_codes(self, market: str = "J", top_n: int = 30) -> list:
        """거래대금 상위 종목 코드 리스트 반환 (유니버스 확장용)

        Args:
            market: "J"=KOSPI, "Q"=KOSDAQ
            top_n: 반환할 최대 종목 수 (KIS API 응답 상한 30개 고정 — 페이지네이션 미지원)

        Returns:
            list[str]: 6자리 종목코드 리스트 (거래대금 내림차순, ETF/레버리지 제외)

        Notes:
            KIS volume-rank 엔드포인트는 fid_cond_mrkt_div_code="J" 만 허용.
            시장 구분은 fid_input_iscd로 수행: 0001=KOSPI 전체, 1001=KOSDAQ 전체.
            fid_blng_cls_code=3 → 순수 주식만 반환 (ETF·레버리지·인버스 제외).
        """
        # KIS API는 항상 J, 시장 구분은 fid_input_iscd 로
        _iscd_map = {"J": "0001", "Q": "1001"}
        fid_input_iscd = _iscd_map.get(market, "0001")

        headers = self._headers()
        headers["tr_id"] = "FHPST01710000"
        resp = requests.get(VOLUME_RANK_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "J",        # 항상 "J" (KOSPI/KOSDAQ 공통)
            "fid_cond_scr_div_code": "20171",
            "fid_input_iscd": fid_input_iscd,      # 0001=KOSPI, 1001=KOSDAQ
            "fid_div_cls_code": "0",
            "fid_blng_cls_code": "3",              # 순수 주식만 (ETF 제외)
            "fid_trgt_cls_code": "111111111",
            "fid_trgt_exls_cls_code": "0000000000",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_date_1": "",
        }, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("output", [])
        # acml_tr_pbmn(거래대금) 기준 내림차순 정렬
        items_sorted = sorted(
            items,
            key=lambda x: _si(x.get("acml_tr_pbmn", 0)),
            reverse=True,
        )
        return [
            item["mksc_shrn_iscd"]
            for item in items_sorted[:top_n]
            if item.get("mksc_shrn_iscd")
        ]
