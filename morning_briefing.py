# morning_briefing.py
"""아침 브리핑 (06:30 KST) — 8블록: 시장요약 / 주도주 / 유니버스 / 국제뉴스 / 경제주식뉴스 / 국내뉴스 / 미국분석 / AI전략"""

import asyncio
import csv
import logging
import re
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

import feedparser
import holidays
import yfinance as yf

from core.config import LOG_DIR, OPENROUTER_MODEL, PORTFOLIO_FILE
from core.kis_client import KISClient
from core.leading_stock_scanner import scan as scan_leading, format_telegram as fmt_leading
from core.universe_scanner import scan_market, format_universe_telegram
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

# ── 미국 주요 종목 10선 ──────────────────────────────────────────
US_TOP10 = {
    "NVDA":  "엔비디아",
    "AAPL":  "애플",
    "MSFT":  "마이크로소프트",
    "GOOGL": "알파벳",
    "AMZN":  "아마존",
    "META":  "메타",
    "TSLA":  "테슬라",
    "AVGO":  "브로드컴",
    "AMD":   "AMD",
    "JPM":   "JP모건",
}

# ── 섹터 ETF ────────────────────────────────────────────────────
SECTOR_ETFS = {
    "XLK":  "기술",
    "XLC":  "커뮤니케이션",
    "XLY":  "임의소비재",
    "XLF":  "금융",
    "XLI":  "산업재",
    "XLV":  "헬스케어",
    "XLE":  "에너지",
    "XLB":  "소재",
    "XLP":  "필수소비재",
    "XLRE": "부동산",
}


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


# ════════════════════════════════════════════════════════════════
# 블록 1 — 시장 요약 (미국+국내+환율+원자재)
# ════════════════════════════════════════════════════════════════

def get_market_summary() -> str:
    """미국/국내 증시 + 환율 + 원자재 통합 테이블"""
    rows: list[str] = []

    # 미국 증시
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
        logger.error(f"미국증시: {e}")
        rows.append("  미국증시 조회 실패")

    rows.append("")

    # 국내 증시 (KIS API)
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
        logger.error(f"국내증시: {e}")
        rows.append("  국내증시 조회 실패")

    rows.append("")

    # 환율 + 원자재
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
        logger.error(f"환율/원자재: {e}")
        rows.append("  환율/원자재 조회 실패")

    table = "<pre>" + "\n".join(rows) + "</pre>"
    return f"📊 <b>시장 요약</b>\n{table}"


# ════════════════════════════════════════════════════════════════
# 블록 2 — 주도주 스캐너 (leading_stock_scanner.py)
# ════════════════════════════════════════════════════════════════

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


def get_leading_stocks(portfolio: dict) -> str:
    """포트폴리오 종목 대상 12-Condition 주도주 스캔"""
    if not portfolio:
        return "🔍 <b>주도주 스캐너 [포트폴리오]</b>\n• 포트폴리오 종목 없음"
    logger.info(f"포트폴리오 주도주 스캔 대상: {len(portfolio)}종목")
    try:
        results = scan_leading(
            list(portfolio.keys()),
            name_map=portfolio,
            min_score=6,
            portfolio_codes=set(portfolio.keys()),
        )
        return fmt_leading(results, title="주도주 스캐너 [포트폴리오]")
    except Exception as e:
        logger.error(f"포트폴리오 주도주 스캔 실패: {e}")
        return f"🔍 <b>주도주 스캐너 [포트폴리오]</b>\n• 조회 실패: {e}"


def get_universe_scan(portfolio: dict) -> str:
    """시장 전체 유니버스 주도주 스캔 (KOSPI + KOSDAQ 상위 80종목)"""
    logger.info("시장 유니버스 주도주 스캔 시작")
    try:
        result = scan_market(
            portfolio_codes=set(portfolio.keys()),
            name_map=portfolio,
            min_score=6,
        )
        return format_universe_telegram(result)
    except Exception as e:
        logger.error(f"유니버스 스캔 실패: {e}")
        return f"🌐 <b>시장 유니버스 스캔</b>\n• 조회 실패: {e}"


# ════════════════════════════════════════════════════════════════
# 블록 3 — 미국장 분석 데이터 (뉴스 + 섹터 + 주요 종목)
# ════════════════════════════════════════════════════════════════

def _fetch_news(url: str, label: str, n: int = 3) -> list[str]:
    try:
        entries = feedparser.parse(url).entries[:n]
        return [e.get("title", "").strip() for e in entries if e.get("title")]
    except Exception as e:
        logger.error(f"{label} RSS 실패: {e}")
        return []


def _get_sector_rows() -> list[str]:
    """섹터 ETF 등락 테이블 행 반환"""
    rows = []
    try:
        d = yf.download(list(SECTOR_ETFS), period="2d", interval="1d", progress=False, auto_adjust=True)
        c, p = d["Close"].iloc[-1], d["Close"].iloc[-2]
        pairs = []
        for sym, label in SECTOR_ETFS.items():
            try:
                cv, pv = float(c[sym]), float(p[sym])
                pct = (cv - pv) / pv * 100
                pairs.append((label, pct))
            except Exception:
                pass
        pairs.sort(key=lambda x: x[1], reverse=True)
        for label, pct in pairs:
            rows.append(f"{_arrow(pct)} {label:<8} {pct:>+6.2f}%")
    except Exception as e:
        logger.error(f"섹터 ETF: {e}")
        rows.append("  섹터 데이터 조회 실패")
    return rows


def _get_top10_rows() -> tuple[list[str], dict]:
    """주요 종목 10선 테이블 행 + AI용 요약 dict 반환"""
    rows = []
    summary = {}
    try:
        d = yf.download(list(US_TOP10), period="2d", interval="1d", progress=False, auto_adjust=True)
        c, p = d["Close"].iloc[-1], d["Close"].iloc[-2]
        for sym, name in US_TOP10.items():
            try:
                cv, pv = float(c[sym]), float(p[sym])
                pct = (cv - pv) / pv * 100
                rows.append(f"{_arrow(pct)} {sym:<5} {name:<10} {cv:>9,.2f}  {pct:>+6.2f}%")
                summary[sym] = {"name": name, "close": cv, "pct": pct}
            except Exception:
                rows.append(f"  {sym:<5} {name:<10}  -")
    except Exception as e:
        logger.error(f"주요종목 10선: {e}")
        rows.append("  데이터 조회 실패")
    return rows, summary


def get_us_data_block() -> tuple[str, dict]:
    """
    블록 3 텍스트 + AI에 넘길 컨텍스트 dict 반환.
    컨텍스트: { news, sectors, top10 }
    """
    # 뉴스
    news_items = _fetch_news(
        "https://feeds.bbci.co.uk/korean/rss.xml", "BBC Korea"
    ) + _fetch_news(
        "https://www.hankyung.com/feed/economy", "한국경제"
    )
    news_lines = [f"  {i+1}. {t}" for i, t in enumerate(news_items[:5])]

    # 섹터 테이블
    sector_rows = _get_sector_rows()

    # 주요 종목 테이블
    top10_rows, top10_summary = _get_top10_rows()

    lines = [
        "📌 <b>미국장 분석</b>",
        "",
        "📰 <b>주요 뉴스</b>",
    ] + news_lines + [
        "",
        "📊 <b>섹터 성과</b>",
        "<pre>" + "\n".join(sector_rows) + "</pre>",
        "",
        "💹 <b>주요 종목 10선</b>",
        "<pre>" + "\n".join(top10_rows) + "</pre>",
    ]

    ctx = {
        "news": news_items[:5],
        "sectors": {sym: {"label": lbl} for sym, lbl in SECTOR_ETFS.items()},
        "top10": top10_summary,
    }
    return "\n".join(lines), ctx


# ════════════════════════════════════════════════════════════════
# 블록 4 — AI 전략 분석 (한국영향 + 포트폴리오 + 시장전망)
# ════════════════════════════════════════════════════════════════

def _build_ai_prompt(ctx: dict, portfolio: dict) -> str:
    news_str = "\n".join(f"- {n}" for n in ctx.get("news", []))
    top10_str = "\n".join(
        f"- {v['name']}({k}): {v['pct']:+.2f}%"
        for k, v in ctx.get("top10", {}).items()
    )
    portfolio_str = ", ".join(portfolio.values()) if portfolio else "포트폴리오 없음"

    return f"""당신은 한국 주식 투자 전문가입니다. 다음 미국 증시 마감 데이터를 바탕으로 분석해주세요.

[미국 주요 뉴스]
{news_str}

[미국 주요 종목 등락]
{top10_str}

[내 포트폴리오 종목]
{portfolio_str}

다음 3가지를 각각 간결하게 분석해주세요. 각 항목은 최대 3-4문장. 볼드(**) 사용 금지, 핵심 수치만 포함:

1. 오늘 한국 주식시장에 미치는 영향
분석:

2. 내 포트폴리오 섹터/종목별 대응전략 (보유 종목 기준으로 구체적으로)
분석:

3. 오늘 시장전망 (강세/보합/약세 중 하나로 판단 + 핵심 근거 2가지)
판단:
"""


async def _call_ai(prompt: str) -> str:
    from ai_client import ai_chat
    return await ai_chat(prompt, model=OPENROUTER_MODEL)


def get_ai_strategy_block(ctx: dict, portfolio: dict) -> str:
    prompt = _build_ai_prompt(ctx, portfolio)
    try:
        raw = asyncio.run(_call_ai(prompt))
    except Exception as e:
        logger.error(f"AI 분석 실패: {e}")
        raw = "AI 분석을 불러올 수 없습니다."

    return f"🤖 <b>AI 전략 분석</b>\n\n{raw}"


# ════════════════════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════════════════════

def run() -> None:
    if not is_business_day():
        return

    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    day_of_week = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]

    logger.info("아침 브리핑 수집 시작")

    portfolio = _load_portfolio()

    market_block    = get_market_summary()
    leading_block   = get_leading_stocks(portfolio)
    universe_block  = get_universe_scan(portfolio)
    us_block, ctx   = get_us_data_block()
    ai_block        = get_ai_strategy_block(ctx, portfolio)

    header = (
        f"🌅 <b>모닝 브리핑 │ {today_str} ({day_of_week})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    footer = "━━━━━━━━━━━━━━━━━━━━\n✅ <b>브리핑 완료</b> │ 좋은 하루 되세요!"

    blocks = [
        f"{header}\n\n{market_block}",
        leading_block,
        universe_block,
        us_block,
        f"{ai_block}\n\n{footer}",
    ]

    for i, block in enumerate(blocks, 1):
        ok = send(block)
        logger.info(f"브리핑 블록 {i}/{len(blocks)} 전송: {'성공' if ok else '실패'}")


if __name__ == "__main__":
    run()
