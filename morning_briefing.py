# morning_briefing.py
"""아침 브리핑 (06:30 KST) — 시장요약 + 주도주 + 뉴스 (3블록 간결 버전)"""

import logging
import csv
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

import feedparser
import holidays
import yfinance as yf

from core.config import LOG_DIR, PORTFOLIO_FILE
from core.kis_client import KISClient
from core.leading_stock_scanner import scan as scan_leading, format_telegram as fmt_leading
from core.notifier import send

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
    today = date.today()
    if today.weekday() >= 5:
        return False
    if today in KR_HOLIDAYS:
        logger.info(f"오늘은 공휴일({KR_HOLIDAYS.get(today)}) — 브리핑 스킵")
        return False
    return True


def _arrow(pct: float) -> str:
    return "🔺" if pct >= 0 else "🔽"


def get_market_summary() -> str:
    """미국/국내 증시 + 환율 + 원자재 통합 테이블 (1블록)"""
    rows: list[str] = []

    # ── 미국 증시 ──────────────────────────────────────
    us = {"^GSPC": "S&P500 ", "^IXIC": "나스닥 ", "^DJI": "다우   ", "^SOX": "반도체 "}
    try:
        d = yf.download(list(us), period="2d", interval="1d", progress=False, auto_adjust=True)
        c, p = d["Close"].iloc[-1], d["Close"].iloc[-2]
        for sym, label in us.items():
            try:
                cv, pv = float(c[sym]), float(p[sym])
                pct = (cv - pv) / pv * 100
                rows.append(f"{_arrow(pct)} {label} {cv:>10,.2f}   {pct:>+6.2f}%")
            except Exception:
                rows.append(f"  {label}  -")
    except Exception as e:
        logger.error(f"미국증시 조회 실패: {e}")
        rows.append("  미국증시 조회 실패")

    rows.append("")

    # ── 국내 증시 (KIS API) ────────────────────────────
    try:
        kis = KISClient()
        for iscd, label in [("0001", "KOSPI  "), ("1001", "KOSDAQ ")]:
            try:
                d = kis.get_index_price(iscd)
                arrow = "🔺" if d["sign"] in ("1", "2") else ("🔽" if d["sign"] in ("4", "5") else "—")
                rows.append(f"{arrow} {label} {d['current']:>10,.2f}   {d['change_pct']:>+6.2f}%")
            except Exception:
                rows.append(f"  {label}  -")
    except Exception as e:
        logger.error(f"국내증시 조회 실패: {e}")
        rows.append("  국내증시 조회 실패")

    rows.append("")

    # ── 환율 + 원자재 ──────────────────────────────────
    fx_commod = {
        "USDKRW=X": ("달러/원 ", 1.0,   "원"),
        "JPYKRW=X": ("100엔/원", 100.0, "원"),
        "CL=F":     ("WTI($)  ", 1.0,   " "),
        "^VIX":     ("VIX     ", 1.0,   " "),
    }
    try:
        d = yf.download(list(fx_commod), period="2d", interval="1d", progress=False, auto_adjust=True)
        c, p = d["Close"].iloc[-1], d["Close"].iloc[-2]
        for sym, (label, mult, unit) in fx_commod.items():
            try:
                cv = float(c[sym]) * mult
                pv = float(p[sym]) * mult
                pct = (cv - pv) / pv * 100
                rows.append(f"{_arrow(pct)} {label} {cv:>10,.2f}{unit}  {pct:>+6.2f}%")
            except Exception:
                rows.append(f"  {label}  -")
    except Exception as e:
        logger.error(f"환율/원자재 조회 실패: {e}")
        rows.append("  환율/원자재 조회 실패")

    table = "<pre>" + "\n".join(rows) + "</pre>"
    return f"📊 <b>시장 요약</b>\n{table}"


def get_all_news() -> str:
    """3개 RSS 피드 핵심 헤드라인 (피드당 2건)"""
    feeds = [
        ("https://feeds.bbci.co.uk/korean/rss.xml",     "🌍 국제"),
        ("https://www.hankyung.com/feed/economy",        "📈 경제"),
        ("https://www.yonhapnewstv.co.kr/browse/feed/", "🇰🇷 국내"),
    ]
    lines = ["📰 <b>주요 뉴스</b>"]
    for url, label in feeds:
        try:
            entries = feedparser.parse(url).entries[:2]
            lines.append(f"\n<b>{label}</b>")
            for i, e in enumerate(entries, 1):
                lines.append(f"  {i}. {e.get('title', '제목 없음').strip()}")
        except Exception as e:
            logger.error(f"{label} RSS 조회 실패: {e}")
            lines.append(f"  {label} 데이터 없음")
    return "\n".join(lines)


def _load_portfolio() -> dict:
    mapping = {}
    try:
        with open(PORTFOLIO_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("holding_status") == "active" and row.get("currency") == "KRW":
                    ticker = row.get("ticker", "").strip()
                    name = row.get("company_name", ticker).strip()
                    if ticker and len(ticker) == 6 and ticker.isdigit():
                        mapping[ticker] = name
    except Exception as e:
        logger.warning(f"포트폴리오 로딩 실패: {e}")
    return mapping


def get_leading_stocks() -> str:
    portfolio = _load_portfolio()
    if not portfolio:
        return "🔍 <b>주도주 스캐너</b>\n• 포트폴리오 종목 없음"
    logger.info(f"주도주 스캔 대상: {len(portfolio)}종목")
    try:
        results = scan_leading(list(portfolio.keys()), name_map=portfolio, min_score=4)
        return fmt_leading(results)
    except Exception as e:
        logger.error(f"주도주 스캔 실패: {e}")
        return f"🔍 <b>주도주 스캐너</b>\n• 조회 실패: {e}"


def run() -> None:
    if not is_business_day():
        return

    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    day_of_week = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]

    logger.info("아침 브리핑 수집 시작")

    market  = get_market_summary()
    leading = get_leading_stocks()
    news    = get_all_news()

    header = (
        f"🌅 <b>모닝 브리핑 │ {today_str} ({day_of_week})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    footer = "━━━━━━━━━━━━━━━━━━━━\n✅ <b>브리핑 완료</b> │ 좋은 하루 되세요!"

    blocks = [
        f"{header}\n\n{market}",
        leading,
        f"{news}\n\n{footer}",
    ]

    for i, block in enumerate(blocks, 1):
        ok = send(block)
        logger.info(f"브리핑 블록 {i}/{len(blocks)} 전송: {'성공' if ok else '실패'}")


if __name__ == "__main__":
    run()
