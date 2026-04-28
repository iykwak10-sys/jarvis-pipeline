# core/portfolio.py
"""portfolio.json 단일 소스 관리 — 로드/저장/추가/삭제 (fcntl 파일 Lock 포함)"""

import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Optional

PORTFOLIO_FILE = Path(__file__).parent.parent / "portfolio.json"
LOCK_PATH = "/tmp/jarvis_portfolio.lock"
logger = logging.getLogger(__name__)


class PortfolioLock:
    """파일 기반 flock을 통한 portfolio.json 동시 접근 제어.

    모든 load/save 작업이 이 Lock을 통하도록 강제.
    bot.py/bot_webhook.py/scheduler.py 간 race condition 방지.
    """

    def __init__(self):
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        """전역 flock 획득 (블로킹, 최대 10초 대기 후 예외)"""
        if self._fd is not None:
            return  # 이미 보유 중
        fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)  # 블로킹 Lock
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        """flock 해제"""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


# 전역 Lock 인스턴스 (모듈 수준에서 공유)
_portfolio_lock = PortfolioLock()


def _with_lock(func):
    """데코레이터: portfolio.json 작업 시 Lock 자동 획득/해제"""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _portfolio_lock:
            return func(*args, **kwargs)
    return wrapper


@_with_lock
def load() -> list:
    """portfolio.json에서 종목 목록 로드"""
    with open(PORTFOLIO_FILE, encoding="utf-8") as f:
        return json.load(f)["stocks"]


@_with_lock
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
    with _portfolio_lock:
        stocks = _load_unlocked()
    if any(s["code"] == code for s in stocks):
        logger.warning(f"이미 존재하는 종목: {code} {name}")
        return False
    entry: dict = {"code": code, "name": name, "sector": sector, "quantity": quantity}
    if buy_price is not None:
        entry["buy_price"] = buy_price
    stocks.append(entry)
    with _portfolio_lock:
        save(stocks)
    logger.info(f"종목 추가: {code} {name}")
    return True


def remove(code: str) -> bool:
    """종목 삭제. 존재하지 않으면 False 반환"""
    with _portfolio_lock:
        stocks = _load_unlocked()
    new_stocks = [s for s in stocks if s["code"] != code]
    if len(new_stocks) == len(stocks):
        logger.warning(f"존재하지 않는 종목: {code}")
        return False
    with _portfolio_lock:
        save(new_stocks)
    logger.info(f"종목 삭제: {code}")
    return True


def _load_unlocked() -> list:
    """Lock 없이 raw 로드 (add/remove 내부에서 Lock 재진입 방지용)"""
    with open(PORTFOLIO_FILE, encoding="utf-8") as f:
        return json.load(f)["stocks"]


def codes() -> list:
    """종목 코드 목록만 반환"""
    return [s["code"] for s in load()]
