# price_alert.py
"""장중 급등/급락 알림 (±5% 기준, 10:00/13:00/14:30 KST 실행)"""

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import LOG_DIR
from core import portfolio, notifier
from core.kis_client import KISClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "price_alert.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

THRESHOLD = 5.0  # 알림 기준 등락률 (%)


def is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def check_alerts() -> None:
    if not is_market_open():
        logger.info("장외 시간 — 알림 체크 스킵")
        return

    stocks = portfolio.load()
    client = KISClient()
    prices = client.get_prices([s["code"] for s in stocks])
    stock_map = {s["code"]: s for s in stocks}

    alert_count = 0
    for price in prices:
        code = price["code"]
        change_pct = price.get("change_pct", 0.0)
        if abs(change_pct) >= THRESHOLD:
            name = stock_map.get(code, {}).get("name", code)
            notifier.send_alert(code, name, change_pct, price.get("close", 0))
            logger.info(f"알림 전송: {name} ({code}) {change_pct:+.2f}%")
            alert_count += 1

    logger.info(f"알림 체크 완료: {alert_count}건 발송 / {len(prices)}종목 확인")


if __name__ == "__main__":
    check_alerts()
