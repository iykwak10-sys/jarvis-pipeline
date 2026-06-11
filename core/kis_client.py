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
EXPIRED_TOKEN_MSG_CD = "EGW00123"  # "기간이 만료된 token 입니다" — 서버측 무효화 신호

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

    def invalidate(self) -> None:
        """메모리·파일 캐시 폐기 — 다음 get_token()이 새로 발급한다.

        같은 app key를 쓰는 다른 프로세스가 새 토큰을 발급하면 KIS가
        기존 토큰을 서버측에서 즉시 무효화하므로, 로컬 expires_at이
        미래여도 토큰이 죽어 있을 수 있다 (2026-06-11 마감수집 0/17 장애).
        """
        self._token = None
        self._expires = None
        try:
            TOKEN_CACHE.unlink(missing_ok=True)
        except OSError:
            pass

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
        # 커넥션 풀·DNS 캐시 재사용 — 매 호출 DNS 재조회로 인한
        # NameResolutionError 연쇄(2026-06 collector 타임아웃 원인) 완화
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token_mgr.get_token()}",
            "appkey": os.environ["KIS_APP_KEY"],
            "appsecret": os.environ["KIS_APP_SECRET"],
            "tr_id": "FHKST01010100",
        }

    def _raise_for_status(self, resp) -> None:
        """오류 응답 처리 + 서버측 토큰 만료(EGW00123) 자가 치유.

        같은 app key로 다른 프로세스가 새 토큰을 발급하면 KIS는 기존
        토큰을 즉시 무효화한다. 이때 로컬 캐시 expires_at은 여전히
        미래이므로 TTL 검사로는 감지할 수 없다 — 응답 msg_cd로 감지해
        캐시를 폐기하면 @retry 재시도가 _headers()→get_token()에서
        새 토큰을 발급받아 자동 복구된다.
        """
        if resp.status_code >= 400:
            try:
                msg_cd = resp.json().get("msg_cd")
            except ValueError:
                msg_cd = None
            if msg_cd == EXPIRED_TOKEN_MSG_CD:
                logger.warning(
                    "서버측 토큰 만료(EGW00123) 감지 — 캐시 폐기, 재시도에서 재발급"
                )
                self._token_mgr.invalidate()
        resp.raise_for_status()

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_price(self, code: str) -> dict:
        """단일 종목 현재가 조회. 반환: {code, close, change, change_pct, volume, high, low, open}"""
        resp = self._session.get(PRICE_URL, headers=self._headers(), params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        self._raise_for_status(resp)
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
        resp = self._session.get(INDEX_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "U",
            "fid_input_iscd": iscd,
            "fid_input_date_1": today,
            "fid_input_date_2": today,
            "fid_period_div_code": "D",
        }, timeout=10)
        self._raise_for_status(resp)
        data = resp.json()
        output = data.get("output1") or data.get("output") or {}
        return {
            "iscd": iscd,
            "current": _sf(output.get("bstp_nmix_prpr")),
            "change": _sf(output.get("bstp_nmix_prdy_vrss")),
            "change_pct": _sf(output.get("bstp_nmix_prdy_ctrt")),
            "sign": output.get("prdy_vrss_sign", "3"),  # 3=보합
        }

    @staticmethod
    def _dummy_price(code: str) -> dict:
        """조회 실패 종목의 close=0 더미 (기존 폴백 형식 유지)"""
        return {"code": code, "close": 0, "change": 0,
                "change_pct": 0.0, "volume": 0,
                "high": 0, "low": 0, "open": 0}

    def get_prices(self, codes: list, fail_fast_after: int = 3) -> list:
        """여러 종목 배치 조회 (0.1초 간격, 초당 20건 제한 준수)

        연속 fail_fast_after개 종목 실패 시 조기 중단한다 — 네트워크 장애 시
        종목당 재시도(3회×10초)가 직렬 누적되어 scheduler 타임아웃을 유발하는
        것을 방지 (2026-06-05~10 collector 120초 타임아웃 5회 관측).
        중단 시 잔여 종목은 close=0 더미로 채워 결과 길이를 보존한다.
        """
        results = []
        consec_fail = 0
        for i, code in enumerate(codes):
            try:
                results.append(self.get_price(code))
                consec_fail = 0
            except Exception as e:
                logger.error(f"종목 {code} 조회 실패: {e}")
                results.append(self._dummy_price(code))
                consec_fail += 1
                if consec_fail >= fail_fast_after:
                    remaining = codes[i + 1:]
                    logger.error(
                        f"연속 {consec_fail}종목 실패 — 조기 중단, "
                        f"잔여 {len(remaining)}종목 더미 처리"
                    )
                    results.extend(self._dummy_price(c) for c in remaining)
                    break
            time.sleep(0.1)
        return results

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_price_full(self, code: str) -> dict:
        """단일 종목 전체 지표 조회 (주도주 스캐너용)
        추가 필드: acml_tr_pbmn(거래대금), prdy_vol(전일거래량),
                   d250_hgpr(52주고가), hts_avls(시가총액억), name(종목명)
        """
        resp = self._session.get(PRICE_URL, headers=self._headers(), params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        self._raise_for_status(resp)
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
        resp = self._session.get(INVESTOR_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        self._raise_for_status(resp)
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
        resp = self._session.get(DAILY_PRICE_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
            "fid_input_date_1": "",
            "fid_input_date_2": "",
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "1",
        }, timeout=10)
        self._raise_for_status(resp)
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
        return [r["code"] for r in self.get_top_trade_value(market=market, top_n=top_n)]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(ConnectionError, Timeout, requests.RequestException))
    def get_top_trade_value(self, market: str = "J", top_n: int = 30) -> list:
        """거래대금 상위 종목 [{code, name}] 반환 (유니버스 이름 고정용)

        volume-rank 응답에 포함된 hts_kor_isnm(종목명)을 함께 반환해,
        장중 주도주 모니터가 종목코드 대신 종목명으로 고정 송출하도록 보장한다.

        Args:
            market: "J"=KOSPI, "Q"=KOSDAQ
            top_n: 반환할 최대 종목 수

        Returns:
            list[dict]: [{"code": 6자리코드, "name": 종목명}] (거래대금 내림차순)
        """
        _iscd_map = {"J": "0001", "Q": "1001"}
        fid_input_iscd = _iscd_map.get(market, "0001")

        headers = self._headers()
        headers["tr_id"] = "FHPST01710000"
        resp = self._session.get(VOLUME_RANK_URL, headers=headers, params={
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20171",
            "fid_input_iscd": fid_input_iscd,
            "fid_div_cls_code": "0",
            "fid_blng_cls_code": "3",
            "fid_trgt_cls_code": "111111111",
            "fid_trgt_exls_cls_code": "0000000000",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_date_1": "",
        }, timeout=10)
        self._raise_for_status(resp)
        items = resp.json().get("output", [])
        items_sorted = sorted(
            items,
            key=lambda x: _si(x.get("acml_tr_pbmn", 0)),
            reverse=True,
        )
        result = []
        for item in items_sorted[:top_n]:
            code = item.get("mksc_shrn_iscd")
            if not code:
                continue
            name = (item.get("hts_kor_isnm") or "").strip() or code
            result.append({"code": code, "name": name})
        return result
