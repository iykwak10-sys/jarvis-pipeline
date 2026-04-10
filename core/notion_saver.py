# core/notion_saver.py
"""Notion API DB 저장 모듈"""

import logging
import os
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def save_stock_prices(date_str: str, stocks_data: list) -> int:
    """종목 주가 DB에 날짜별 저장.
    stocks_data: {code, name, sector, close, change_pct, volume, quantity} 리스트
    반환: 저장 성공 종목 수
    """
    db_id = os.environ.get("NOTION_STOCK_DB_ID", "")
    if not db_id:
        logger.warning("NOTION_STOCK_DB_ID 미설정 — Notion 저장 스킵")
        return 0

    date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    success = 0

    for s in stocks_data:
        if not s.get("close"):
            continue
        payload = {
            "parent": {"database_id": db_id},
            "properties": {
                "종목명":  {"title":     [{"text": {"content": s.get("name", s["code"])}}]},
                "날짜":    {"date":      {"start": date_iso}},
                "종목코드": {"rich_text": [{"text": {"content": s["code"]}}]},
                "현재가":  {"number":    s["close"]},
                "등락률":  {"number":    s.get("change_pct", 0)},
                "거래량":  {"number":    s.get("volume", 0)},
                "섹터":    {"select":    {"name": s.get("sector", "기타")}},
            },
        }
        try:
            resp = requests.post(f"{NOTION_API}/pages", headers=_headers(),
                                 json=payload, timeout=30)
            resp.raise_for_status()
            success += 1
            logger.info(f"  Notion 저장: {s.get('name')} ({s['code']})")
        except Exception as e:
            logger.error(f"  Notion 저장 실패 {s['code']}: {e}")

    logger.info(f"Notion 주가 저장 완료: {success}/{len(stocks_data)}종목")
    return success


def save_analysis_report(date_str: str, report_text: str,
                          kospi_close: Optional[float] = None,
                          kospi_change_pct: Optional[float] = None) -> Optional[str]:
    """분석리포트 DB에 저장. 반환: 저장된 Notion 페이지 URL"""
    db_id = os.environ.get("NOTION_ANALYSIS_DB_ID", "")
    if not db_id:
        logger.warning("NOTION_ANALYSIS_DB_ID 미설정 — 분석리포트 저장 스킵")
        return None

    date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    properties: dict = {
        "이름": {"title": [{"text": {"content": f"마감분석_{date_str}"}}]},
        "날짜": {"date": {"start": date_iso}},
    }
    if kospi_close is not None:
        properties["KOSPI"] = {"number": kospi_close}
    if kospi_change_pct is not None:
        properties["KOSPI등락률"] = {"number": kospi_change_pct}

    # 리포트 텍스트를 1900자 단위 블록으로 분할
    chunk_size = 1900
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": report_text[i:i+chunk_size]}}]
            },
        }
        for i in range(0, len(report_text), chunk_size)
    ]

    try:
        resp = requests.post(f"{NOTION_API}/pages", headers=_headers(),
                             json={"parent": {"database_id": db_id},
                                   "properties": properties,
                                   "children": children},
                             timeout=30)
        resp.raise_for_status()
        url = resp.json().get("url", "")
        logger.info(f"분석리포트 Notion 저장: {url}")
        return url
    except Exception as e:
        logger.error(f"분석리포트 Notion 저장 실패: {e}")
        return None
