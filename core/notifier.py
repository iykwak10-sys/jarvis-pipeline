# core/notifier.py
"""Telegram 알림 전송 모듈"""

import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
logger = logging.getLogger(__name__)


def _token() -> str:
    return os.environ["JARVIS_BOT_TOKEN"]


def _chat_id() -> str:
    return os.environ["JARVIS_CHAT_ID"]


def send(message: str) -> bool:
    """텍스트 메시지 전송. HTML parse_mode 사용."""
    try:
        resp = requests.post(
            TELEGRAM_URL.format(token=_token()),
            json={"chat_id": _chat_id(), "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram 전송 실패: {e}")
        return False


def send_portfolio_report(stocks_data: list) -> bool:
    """포트폴리오 현황 리포트 전송.
    stocks_data: KISClient.get_prices() 결과 + portfolio 정보 병합 리스트
    각 항목: {code, name, close, change_pct, quantity, buy_price(optional)}
    """
    lines = ["📊 <b>포트폴리오 현황</b>\n"]
    total_value = 0
    total_profit = 0
    has_profit_data = False

    for s in stocks_data:
        close = s.get("close", 0)
        qty = s.get("quantity", 0)
        change_pct = s.get("change_pct", 0.0)
        arrow = "▲" if change_pct >= 0 else "▼"
        value = close * qty
        total_value += value

        line = f"{s['name']} {close:,}원 {arrow}{abs(change_pct):.2f}%"

        buy_price = s.get("buy_price")
        if buy_price and close:
            profit_pct = (close - buy_price) / buy_price * 100
            profit_amt = (close - buy_price) * qty
            total_profit += profit_amt
            has_profit_data = True
            sign = "+" if profit_pct >= 0 else ""
            line += f" | 수익 {sign}{profit_pct:.1f}%"

        lines.append(line)

    lines.append(f"\n💰 평가금액: {total_value:,}원")
    if has_profit_data:
        sign = "+" if total_profit >= 0 else ""
        lines.append(f"📈 평가손익: {sign}{total_profit:,}원")

    return send("\n".join(lines))


def send_alert(code: str, name: str, change_pct: float, close: int) -> bool:
    """급등/급락 알림 전송"""
    emoji = "🚀" if change_pct >= 0 else "📉"
    sign = "+" if change_pct >= 0 else ""
    message = (
        f"{emoji} <b>급등락 알림</b>\n"
        f"종목: {name} ({code})\n"
        f"등락률: {sign}{change_pct:.2f}%\n"
        f"현재가: {close:,}원"
    )
    return send(message)


def send_us_market_alert() -> bool:
    """미국 장 마감 알림 (06:05 KST)"""
    return send(
        "🇺🇸 <b>미국 장 마감 알림</b>\n"
        "Claude Cowork에서 '미국 마감 분석해줘'를 입력하세요."
    )
