# core/portfolio.py
"""portfolio.json 단일 소스 관리 — 로드/저장/추가/삭제"""

import json
import logging
from pathlib import Path
from typing import Optional

PORTFOLIO_FILE = Path(__file__).parent.parent / "portfolio.json"
logger = logging.getLogger(__name__)


def load() -> list:
    """portfolio.json에서 종목 목록 로드"""
    with open(PORTFOLIO_FILE, encoding="utf-8") as f:
        return json.load(f)["stocks"]


def save(stocks: list) -> None:
    """종목 목록을 portfolio.json에 저장"""
    PORTFOLIO_FILE.write_text(
        json.dumps({"stocks": stocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"portfolio.json 저장 완료 ({len(stocks)}종목)")


def add(code: str, name: str, sector: str = "기타",
        quantity: int = 0, buy_price: Optional[int] = None) -> bool:
    """종목 추가. 이미 존재하면 False 반환"""
    stocks = load()
    if any(s["code"] == code for s in stocks):
        logger.warning(f"이미 존재하는 종목: {code} {name}")
        return False
    entry: dict = {"code": code, "name": name, "sector": sector, "quantity": quantity}
    if buy_price is not None:
        entry["buy_price"] = buy_price
    stocks.append(entry)
    save(stocks)
    logger.info(f"종목 추가: {code} {name}")
    return True


def remove(code: str) -> bool:
    """종목 삭제. 존재하지 않으면 False 반환"""
    stocks = load()
    new_stocks = [s for s in stocks if s["code"] != code]
    if len(new_stocks) == len(stocks):
        logger.warning(f"존재하지 않는 종목: {code}")
        return False
    save(new_stocks)
    logger.info(f"종목 삭제: {code}")
    return True


def codes() -> list:
    """종목 코드 목록만 반환"""
    return [s["code"] for s in load()]
