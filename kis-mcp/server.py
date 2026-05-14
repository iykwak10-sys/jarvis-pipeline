#!/usr/bin/env python3
"""KIS (한국투자증권) MCP 서버 - Claude Code에서 KIS API 직접 호출"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# .env 로드: 서버 디렉토리 -> 부모(jarvis-pipeline) -> 절대경로 순서로 탐색
_env_paths = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
    Path.home() / "jarvis-pipeline" / ".env",
]
for _p in _env_paths:
    if _p.exists():
        load_dotenv(_p, override=True)
        break

BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_URL = f"{BASE_URL}/oauth2/tokenP"
PRICE_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
INDEX_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
TOKEN_CACHE = Path(__file__).parent / ".kis_token.json"

mcp = FastMCP("kis-api")

# ── 토큰 관리 ─────────────────────────────────────────────────────────────────

_token: Optional[str] = None
_expires: Optional[datetime] = None


def _load_cached_token() -> bool:
    global _token, _expires
    if not TOKEN_CACHE.exists():
        return False
    try:
        cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(cache["expires_at"])
        if datetime.now() < exp - timedelta(minutes=10):
            _token = cache["token"]
            _expires = exp
            return True
    except Exception:
        pass
    return False


def _issue_token() -> str:
    global _token, _expires
    app_key = os.environ["KIS_APP_KEY"]
    app_secret = os.environ["KIS_APP_SECRET"]
    resp = requests.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _expires = datetime.now() + timedelta(seconds=86400)
    TOKEN_CACHE.write_text(json.dumps({
        "token": _token,
        "expires_at": _expires.isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    return _token


def get_token() -> str:
    global _token, _expires
    if _token and _expires and datetime.now() < _expires - timedelta(minutes=10):
        return _token
    if _load_cached_token():
        return _token
    return _issue_token()


def _headers(tr_id: str = "FHKST01010100") -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_token()}",
        "appkey": os.environ["KIS_APP_KEY"],
        "appsecret": os.environ["KIS_APP_SECRET"],
        "tr_id": tr_id,
    }


# ── MCP 도구 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_stock_price(code: str) -> dict:
    """한국 주식 현재가 조회.

    Args:
        code: 종목코드 (예: '005930' = 삼성전자, '000660' = SK하이닉스)

    Returns:
        close, change, change_pct, volume, high, low, open 포함 딕셔너리
    """
    resp = requests.get(PRICE_URL, headers=_headers(), params={
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


@mcp.tool()
def get_stock_prices(codes: list[str]) -> list[dict]:
    """여러 한국 주식 현재가 일괄 조회 (초당 20건 제한 준수).

    Args:
        codes: 종목코드 리스트 (예: ['005930', '000660', '035720'])

    Returns:
        각 종목의 가격 정보 리스트
    """
    results = []
    for code in codes:
        try:
            results.append(get_stock_price(code))
        except Exception as e:
            results.append({
                "code": code, "error": str(e),
                "close": 0, "change": 0, "change_pct": 0.0,
                "volume": 0, "high": 0, "low": 0, "open": 0,
            })
        time.sleep(0.1)
    return results


@mcp.tool()
def get_index_price(iscd: str = "0001") -> dict:
    """국내 주가지수 현재가 조회.

    Args:
        iscd: 지수코드
            '0001' = KOSPI (기본값)
            '1001' = KOSDAQ
            '2001' = KOSPI200

    Returns:
        current(현재값), change(전일대비), change_pct(등락률%), sign(방향) 포함 딕셔너리
        sign: '1'=상한 '2'=상승 '3'=보합 '4'=하한 '5'=하락
    """
    today = datetime.now().strftime("%Y%m%d")
    headers = _headers("FHKUP03500100")
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
    index_names = {"0001": "KOSPI", "1001": "KOSDAQ", "2001": "KOSPI200"}
    return {
        "iscd": iscd,
        "name": index_names.get(iscd, iscd),
        "current": float(output.get("bstp_nmix_prpr", 0)),
        "change": float(output.get("bstp_nmix_prdy_vrss", 0)),
        "change_pct": float(output.get("bstp_nmix_prdy_ctrt", 0)),
        "sign": output.get("prdy_vrss_sign", "3"),
        "high": float(output.get("bstp_nmix_hgpr", 0)),
        "low": float(output.get("bstp_nmix_lwpr", 0)),
        "open": float(output.get("bstp_nmix_oprc", 0)),
    }


@mcp.tool()
def get_market_overview() -> dict:
    """KOSPI + KOSDAQ 지수 동시 조회 (시장 전체 현황).

    Returns:
        kospi와 kosdaq 각각의 지수 정보
    """
    results = {}
    for iscd, name in [("0001", "kospi"), ("1001", "kosdaq")]:
        try:
            results[name] = get_index_price(iscd)
        except Exception as e:
            results[name] = {"iscd": iscd, "name": name.upper(), "error": str(e)}
        time.sleep(0.2)
    return results


@mcp.tool()
def check_token_status() -> dict:
    """KIS API 토큰 상태 확인 (유효성 및 만료 시간).

    Returns:
        valid(유효여부), expires_at(만료시간), cached(캐시사용여부)
    """
    global _token, _expires
    cached = _load_cached_token() if not (_token and _expires) else True
    if _token and _expires:
        return {
            "valid": datetime.now() < _expires - timedelta(minutes=10),
            "expires_at": _expires.isoformat(),
            "cached": cached,
        }
    return {"valid": False, "expires_at": None, "cached": False}


if __name__ == "__main__":
    mcp.run()
