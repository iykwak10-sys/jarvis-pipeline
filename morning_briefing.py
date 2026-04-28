# morning_briefing.py
"""아침 브리핑 (06:30 KST) — 미국 증시 마감 + 환율 + 유가/VIX + 뉴스"""

import logging
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import feedparser
import holidays
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from core.kis_client import KISClient
from core.notifier import send

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "morning_briefing.log", maxBytes=5*1024*1024,
                            backupCount=3, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

KR_HOLIDAYS = holidays.KR()


def is_business_day() -> bool:
    """한국 기준 영업일 여부 (주말 + 공휴일 제외)"""
    today = date.today()
    if today.weekday() >= 5:
        return False
    if today in KR_HOLIDAYS:
        holiday_name = KR_HOLIDAYS.get(today, "공휴일")
        logger.info(f"오늘은 공휴일({holiday_name}) — 브리핑 스킵")
        return False
    return True


def _pct_arrow(pct: float) -> str:
    return "🔺" if pct >= 0 else "🔽"


def get_kr_market() -> str:
    """국내 증시 지수 (KIS API — KOSPI, KOSDAQ). 장 마감 후 또는 장중 모두 최신값 반환."""
    indices = [
        ("0001", "KOSPI  "),
        ("1001", "KOSDAQ "),
    ]
    lines = ["🇰🇷 <b>국내 증시 (KIS API)</b>"]
    try:
        kis = KISClient()
        for iscd, label in indices:
            try:
                d = kis.get_index_price(iscd)
                current = d["current"]
                chg = d["change"]
                chg_pct = d["change_pct"]
                sign = d["sign"]
                # sign: 2=상승, 1=상한, 3=보합, 5=하락, 4=하한
                arrow = "🔺" if sign in ("1", "2") else ("🔽" if sign in ("4", "5") else "—")
                lines.append(
                    f"• {label}  {current:,.2f}  {arrow} {chg:+.2f} ({chg_pct:+.2f}%)"
                )
            except Exception as e:
                logger.error(f"KIS 지수 조회 실패 ({iscd}): {e}")
                lines.append(f"• {label}  데이터 없음")
    except Exception as e:
        logger.error(f"KIS 클라이언트 초기화 실패: {e}")
        lines.append("• KIS API 연결 실패")
    return "\n".join(lines)


def get_us_market() -> str:
    """미국 증시 마감 데이터 (S&P500, 나스닥, 다우, SOX)"""
    tickers = {
        "^GSPC":  "S&P 500    ",
        "^IXIC":  "나스닥     ",
        "^DJI":   "다우존스   ",
        "^SOX":   "필라델피아반도체",
    }
    lines = ["🇺🇸 <b>미국 증시 마감 (전일 기준)</b>"]
    try:
        data = yf.download(
            list(tickers.keys()),
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        closes = data["Close"].iloc[-1]
        prev    = data["Close"].iloc[-2]
        for sym, label in tickers.items():
            try:
                close = float(closes[sym])
                prev_c = float(prev[sym])
                chg_pct = (close - prev_c) / prev_c * 100
                arrow = _pct_arrow(chg_pct)
                lines.append(
                    f"• {label}  {close:,.2f}  {arrow} {chg_pct:+.2f}%"
                )
            except Exception:
                lines.append(f"• {label}  데이터 없음")
    except Exception as e:
        logger.error(f"미국 증시 조회 실패: {e}")
        lines.append("• 데이터 조회 실패")
    return "\n".join(lines)


def get_fx() -> str:
    """환율 (USD/KRW, EUR/KRW, JPY/KRW)"""
    pairs = {
        "USDKRW=X": ("달러/원  ", 1.0),
        "EURKRW=X": ("유로/원  ", 1.0),
        "JPYKRW=X": ("100엔/원 ", 100.0),
    }
    lines = ["💱 <b>환율</b>"]
    try:
        data = yf.download(
            list(pairs.keys()),
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        closes = data["Close"].iloc[-1]
        prev    = data["Close"].iloc[-2]
        for sym, (label, mult) in pairs.items():
            try:
                close = float(closes[sym]) * mult
                prev_c = float(prev[sym]) * mult
                chg = close - prev_c
                chg_pct = chg / prev_c * 100
                arrow = _pct_arrow(chg_pct)
                lines.append(
                    f"• {label}  {close:,.2f}원  {arrow} {chg:+.2f} ({chg_pct:+.2f}%)"
                )
            except Exception:
                lines.append(f"• {label}  데이터 없음")
    except Exception as e:
        logger.error(f"환율 조회 실패: {e}")
        lines.append("• 데이터 조회 실패")
    return "\n".join(lines)


def get_commodities() -> str:
    """국제유가(WTI) + VIX"""
    items = {
        "CL=F":  ("WTI 원유  ", "$", ".2f"),
        "^VIX":  ("VIX 공포지수", "", ".2f"),
    }
    lines = ["🛢️ <b>국제유가 &amp; VIX</b>"]
    try:
        data = yf.download(
            list(items.keys()),
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        closes = data["Close"].iloc[-1]
        prev    = data["Close"].iloc[-2]
        for sym, (label, unit, fmt) in items.items():
            try:
                close = float(closes[sym])
                prev_c = float(prev[sym])
                chg_pct = (close - prev_c) / prev_c * 100
                arrow = _pct_arrow(chg_pct)
                val_str = f"{unit}{close:{fmt}}"
                lines.append(f"• {label}  {val_str}  {arrow} {chg_pct:+.2f}%")
            except Exception:
                lines.append(f"• {label}  데이터 없음")
    except Exception as e:
        logger.error(f"유가/VIX 조회 실패: {e}")
        lines.append("• 데이터 조회 실패")
    return "\n".join(lines)


def get_news_rss(feed_url: str, label: str, max_items: int = 3) -> str:
    """RSS 피드에서 최신 뉴스 헤드라인 수집"""
    lines = [f"<b>{label}</b>"]
    try:
        feed = feedparser.parse(feed_url)
        entries = feed.entries[:max_items]
        if not entries:
            lines.append("• 뉴스 없음")
        for i, e in enumerate(entries, 1):
            title = e.get("title", "제목 없음").strip()
            lines.append(f"{i}. {title}")
    except Exception as e:
        logger.error(f"{label} RSS 조회 실패: {e}")
        lines.append("• 데이터 조회 실패")
    return "\n".join(lines)


def run() -> None:
    if not is_business_day():
        return

    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    day_of_week = weekdays[datetime.now().weekday()]

    logger.info("아침 브리핑 수집 시작")

    kr_market = get_kr_market()   # KIS API 고정
    market    = get_us_market()
    fx        = get_fx()
    commod    = get_commodities()

    # 뉴스 RSS
    intl_news = get_news_rss(
        "https://feeds.bbci.co.uk/korean/rss.xml",
        "🌍 국제뉴스 (BBC 코리아)",
    )
    econ_news = get_news_rss(
        "https://www.hankyung.com/feed/economy",
        "📈 경제/주식 (한국경제)",
    )
    kr_news = get_news_rss(
        "https://www.yonhapnewstv.co.kr/browse/feed/",
        "🇰🇷 한국뉴스 (연합뉴스TV)",
    )

    header = (
        f"🌅 <b>모닝 브리핑 │ {today_str} ({day_of_week})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    footer = "━━━━━━━━━━━━━━━━━━━━\n✅ <b>브리핑 완료</b> │ 좋은 하루 되세요!"

    # 메시지 분할 전송 (Telegram 4096자 제한)
    blocks = [
        f"{header}\n\n{kr_market}\n\n{market}\n\n{fx}\n\n{commod}",
        f"{intl_news}",
        f"{econ_news}",
        f"{kr_news}\n\n{footer}",
    ]

    for i, block in enumerate(blocks, 1):
        ok = send(block)
        logger.info(f"브리핑 블록 {i}/4 전송: {'성공' if ok else '실패'}")


if __name__ == "__main__":
    run()
