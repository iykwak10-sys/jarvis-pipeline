# core/portfolio.py
"""SSOT CSV 포트폴리오 로더 — fcntl 동시성 보호 포함

모든 종목 데이터는 개인투자비서 Agent/data/portfolio.csv (SSOT) 를 읽는다.
이 모듈은 jarvis-pipeline 형식({"stocks": [{code, name, sector, quantity, buy_price}]})으로 변환 제공.
쓰기 작업(매수/매도)은 SSOT CSV를 직접 수정하지 않고 SSOT의 runbook/대시보드가 처리.
"""

import csv
import fcntl
import logging
import os
from pathlib import Path
from typing import Optional

# ── SSOT CSV 경로 ────────────────────────────────────────────────
SSOT_CSV = Path("/Users/kwaksmacmini/개인투자비서 Agent/data/portfolio.csv")
LOCK_PATH = "/tmp/jarvis_portfolio.lock"
logger = logging.getLogger(__name__)


class PortfolioLock:
    """파일 기반 flock을 통한 SSOT CSV 동시 접근 제어 (읽기 전용).

    모든 load 작업이 이 Lock을 통하도록 강제.
    bot.py/scheduler.py 간 race condition 방지.
    """

    def __init__(self):
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)  # 공유 Lock (읽기 전용)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
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


_portfolio_lock = PortfolioLock()


def _with_lock(func):
    import functools
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _portfolio_lock:
            return func(*args, **kwargs)
    return wrapper


# ── CSV 컬럼 ─────────────────────────────────────────────────────
_CSV_COLUMNS = [
    "ticker", "company_name", "market", "sector", "holding_status",
    "quantity", "avg_cost", "currency", "target_weight",
    "thesis", "risk_notes", "priority",
]


@_with_lock
def load() -> list:
    """SSOT CSV에서 active 종목만 로드 → jarvis-pipeline 형식 dict 리스트"""
    if not SSOT_CSV.exists():
        logger.error(f"SSOT CSV 누락: {SSOT_CSV}")
        return []

    stocks = []
    with open(SSOT_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            if not ticker or ticker in ("CASH_KRW", "DEPOSIT_KRW"):
                continue
            stocks.append({
                "code": ticker,
                "name": row.get("company_name", "").strip(),
                "sector": row.get("sector", "").strip(),
                "quantity": int(row.get("quantity", 0) or 0),
                "buy_price": int(float(row.get("avg_cost", 0) or 0)),
            })
    logger.info(f"SSOT CSV 로드: {len(stocks)}종목")
    return stocks


def codes() -> list:
    """종목 코드 목록만 반환"""
    return [s["code"] for s in load()]


# ── 하위 호환: 기존 add/remove/save 함수 (no-op 또는 경고) ──────

def save(stocks: list) -> None:
    """⚠️ SSOT CSV는 이 모듈에서 직접 수정하지 않습니다.
    수정은 개인투자비서 Agent 대시보드나 portfolio.csv 직접 편집으로만 가능.
    """
    logger.warning("SSOT CSV 직접 수정은 지원되지 않습니다. portfolio.csv를 직접 편집하세요.")


def add(code: str, name: str, sector: str = "기타",
        quantity: int = 0, buy_price: Optional[int] = None) -> bool:
    """⚠️ 종목 추가는 SSOT CSV를 통해 해주세요."""
    logger.warning(f"SSOT CSV 직접 수정 불가. portfolio.csv에 '{code} {name}'을 직접 추가하세요.")
    return False


def remove(code: str) -> bool:
    """⚠️ 종목 삭제는 SSOT CSV를 통해 해주세요."""
    logger.warning(f"SSOT CSV 직접 수정 불가. portfolio.csv에서 '{code}'를 직접 삭제하세요.")
    return False
