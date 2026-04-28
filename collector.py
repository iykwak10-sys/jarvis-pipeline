# collector.py
"""KIS 시세 수집 → JSON 저장 → Telegram 알림 → Notion 저장(마감 시)"""

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import DATA_DIR, LOG_DIR

from core import portfolio, notifier, notion_saver
from core.kis_client import KISClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "collector.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def collect(closing: bool = False) -> list:
    """보유 종목 현재가 수집.
    closing=True: 마감 수집 (Notion 저장 + Telegram 마감 리포트)
    closing=False: 장중 수집 (JSON 저장만)
    반환: stocks_data 리스트
    """
    today = datetime.now().strftime("%Y%m%d")
    stocks = portfolio.load()
    client = KISClient()

    logger.info(f"{'마감' if closing else '장중'} 수집 시작: {len(stocks)}종목")

    price_map = {r["code"]: r for r in client.get_prices([s["code"] for s in stocks])}

    stocks_data = []
    for s in stocks:
        price = price_map.get(s["code"], {})
        stocks_data.append({
            "code": s["code"],
            "name": s["name"],
            "sector": s.get("sector", "기타"),
            "quantity": s.get("quantity", 0),
            "buy_price": s.get("buy_price"),
            "close": price.get("close", 0),
            "change": price.get("change", 0),
            "change_pct": price.get("change_pct", 0.0),
            "volume": price.get("volume", 0),
            "high": price.get("high", 0),
            "low": price.get("low", 0),
            "open": price.get("open", 0),
        })

    # JSON 저장
    output_file = DATA_DIR / f"market_data_{today}.json"
    payload = {
        "date": today,
        "collected_at": datetime.now().isoformat(),
        "source": "한국투자증권 KIS API",
        "stocks": stocks_data,
    }
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"JSON 저장: {output_file.name}")

    if closing:
        # Notion 저장
        notion_saver.save_stock_prices(today, stocks_data)
        # Telegram 마감 리포트
        notifier.send_portfolio_report(stocks_data)
        logger.info("마감 수집 완료 (Notion + Telegram)")
    else:
        logger.info("장중 수집 완료")

    return stocks_data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--closing", action="store_true", help="마감 수집 모드")
    args = parser.parse_args()
    collect(closing=args.closing)
