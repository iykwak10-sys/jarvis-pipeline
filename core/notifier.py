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

def _trend_symbol(change_pct: float) -> str:
    """Return a triangle symbol representing market movement.
    상승(positive) -> red upward triangle (represented by 🔺)
    하락(negative) -> blue downward triangle (represented by 🔽)
    Note: Telegram HTML parse_mode does not support CSS-based colored shapes,
    so we rely on Unicode/emoji representations for compatibility.
    """
    try:
        if change_pct is None:
            return ""
        # 상승은 🔺 유지, 하락은 역삼각형(🔽)으로 표기 (파란색 계열 이모지 활용)
        return "🔺" if float(change_pct) >= 0 else "🔽"
    except Exception:
        return ""

def _validate_stock_entry(s: dict) -> bool:
    """Validate a single stock entry looks sane for sending.
    Expected keys: code, name, close, quantity, change_pct (optional), buy_price (optional)
    """
    try:
        if not s:
            return False
        code = str(s.get("code", "")).strip()
        name = str(s.get("name", "")).strip()
        close = float(s.get("close", 0) if s.get("close") not in (None, "") else 0)
        qty = int(s.get("quantity", 0))
        if not code or not name:
            return False
        if close <= 0:
            return False
        if qty < 0:
            return False
        if s.get("change_pct") not in (None, ""):
            cp = float(s.get("change_pct"))
            if not (-1000.0 <= cp <= 1000.0):
                return False
        return True
    except Exception:
        return False


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
    # Mode-based header: 'news' as default, 'diagnostic' to emphasize detailed analysis
    mode = os.environ.get("JARVIS_TELEGRAM_MODE", "news").lower()
    # Normalize mode to a known set to avoid unintended verbose diagnostics
    if mode not in ("news", "diagnostic"):
        mode = "news"
    header = "📊 <b>포트폴리오 현황</b>\n"
    if mode == "diagnostic":
        header = "🧪 <b>진단/분석 - 포트폴리오 현황</b>\n"

    lines = [header]
    total_value = 0
    total_daily_pnl = 0
    total_profit = 0
    has_profit_data = False

    # 1) 우선적으로 KIS 데이터를 검증합니다.
    valid_entries = [_ for _ in stocks_data if _validate_stock_entry(_)]
    data_to_use = valid_entries if valid_entries else []

    # 2) 검증된 데이터가 없으면 백업 소스 사용 시도
    if not data_to_use:
        data_to_use = _fetch_backup_stock_data()

    if not data_to_use:
        lines.append("데이터를 확인할 수 없습니다. 백업 소스에서도 데이터가 없습니다.")
        payload = "\n".join(lines)
        logger.info("Telegram payload (mode=%s):\n%s", mode, payload)
        return send(payload)

    for s in data_to_use:
        close = s.get("close", 0)
        qty = s.get("quantity", 0)
        change_pct = s.get("change_pct", 0.0)
        change = s.get("change", 0)
        trend = _trend_symbol(change_pct)
        value = close * qty
        total_value += value
        total_daily_pnl += change * qty

        line = f"{s['name']} {close:,}원 {trend} {abs(change_pct):.2f}%"

        buy_price = s.get("buy_price")
        if buy_price and close:
            profit_pct = (close - buy_price) / buy_price * 100
            profit_amt = (close - buy_price) * qty
            total_profit += profit_amt
            has_profit_data = True
            sign = "+" if profit_pct >= 0 else ""
            line += f" | 수익 {sign}{profit_pct:.1f}%"

        lines.append(line)

    daily_sign = "+" if total_daily_pnl >= 0 else ""
    daily_arrow = "🔺" if total_daily_pnl >= 0 else "🔽"
    lines.append(f"\n💰 평가금액: {total_value:,}원  {daily_arrow} 전일比 {daily_sign}{total_daily_pnl:,}원")
    if has_profit_data:
        sign = "+" if total_profit >= 0 else ""
        lines.append(f"📈 평가손익: {sign}{total_profit:,}원")

    return send("\n".join(lines))

def _fetch_backup_stock_data() -> list:
    """KIS 데이터에 문제가 있을 때 사용할 백업 소스로부터 데이터 수집 시도.
    실제 구현은 백업 소스(API/피드 등)에서 데이터를 받아 포맷을 stocks_data와 같은 형태로 반환해야 합니다.
    여기서는 초기 배포를 위한 스텁으로 비어있거나 간단한 예시를 반환합니다.
    """
    try:
        # 파일/환경 변수/피드 등에서 백업 소스를 읽어 파싱하는 로직을 여기에 합시니다.
        # 예시 로직: 빈 리스트 반환 -> 상용 환경에서 확장 필요
        return []
    except Exception:
        return []


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
