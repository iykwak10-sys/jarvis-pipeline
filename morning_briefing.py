# morning_briefing.py
"""아침 브리핑 (06:30 KST) — 8블록: 시장요약 / 주도주 / 유니버스 / 국제뉴스 / 경제주식뉴스 / 국내뉴스 / 미국분석 / AI전략"""

import asyncio
import csv
import logging
import math
import re
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from typing import List, Optional

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

# ── 뉴스 RSS 피드 소스 (동작 검증된 URL만 수록) ──────────────────
INTL_FEEDS = [
    ("https://feeds.bbci.co.uk/korean/rss.xml",                  "BBC코리아"),
    ("https://www.yonhapnewstv.co.kr/browse/feed/?cat=71",       "연합뉴스TV-국제"),
]

ECON_FEEDS = [
    ("https://www.mk.co.kr/rss/40300001/",                       "매일경제"),
    ("https://www.mk.co.kr/rss/30100041/",                       "매일경제-경제"),
    ("https://www.mk.co.kr/rss/50200011/",                       "매일경제-주식"),
    ("https://rss.donga.com/economy.xml",                        "동아일보-경제"),
]

KR_FEEDS = [
    ("https://www.yonhapnewstv.co.kr/browse/feed/",              "연합뉴스TV"),
    ("https://rss.donga.com/total.xml",                          "동아일보"),
    ("https://rss.donga.com/politics.xml",                       "동아일보-정치"),
]


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


def _last_two(close, sym) -> tuple:
    """심볼의 마지막 2개 유효(non-NaN) 종가 (prev, curr) 반환.

    yfinance가 최신 행을 간헐적으로 NaN으로 반환해도(WTI·VIX 등)
    유효한 직전 값으로 폴백하도록 dropna 후 마지막 2개를 사용한다.
    값이 부족하면 (None, None).
    """
    try:
        s = close[sym].dropna()
    except Exception:
        return None, None
    if len(s) < 2:
        return None, None
    prev, curr = float(s.iloc[-2]), float(s.iloc[-1])
    if math.isnan(prev) or math.isnan(curr):
        return None, None
    return prev, curr


# ════════════════════════════════════════════════════════════════
# 블록 1 — 시장 요약 (미국+국내+환율+원자재)
# ════════════════════════════════════════════════════════════════

def get_market_summary() -> str:
    """미국/국내 증시 + 환율 + 원자재 통합 테이블"""
    rows: list[str] = []

    # 미국 증시
    us = {"^GSPC": "S&P500 ", "^IXIC": "나스닥 ", "^DJI": "다우   ", "^SOX": "반도체 "}
    try:
        d = yf.download(list(us), period="5d", interval="1d", progress=False, auto_adjust=True)
        for sym, label in us.items():
            pv, cv = _last_two(d["Close"], sym)
            if cv is None:
                rows.append(f"  {label}  -")
                continue
            pct = (cv - pv) / pv * 100
            rows.append(f"{_arrow(pct)} {label} {cv:>10,.2f}   {pct:>+6.2f}%")
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
        d = yf.download(list(fx_commod), period="5d", interval="1d", progress=False, auto_adjust=True)
        for sym, (label, mult, unit) in fx_commod.items():
            pv_raw, cv_raw = _last_two(d["Close"], sym)
            if cv_raw is None:
                rows.append(f"  {label}  -")
                continue
            cv, pv = cv_raw * mult, pv_raw * mult
            pct = (cv - pv) / pv * 100
            rows.append(f"{_arrow(pct)} {label} {cv:>10,.2f}{unit}  {pct:>+6.2f}%")
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
            min_score=9,
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
            min_score=9,
        )
        return format_universe_telegram(result)
    except Exception as e:
        logger.error(f"유니버스 스캔 실패: {e}")
        return f"🌐 <b>시장 유니버스 스캔</b>\n• 조회 실패: {e}"


# ════════════════════════════════════════════════════════════════
# 블록 3 — 미국장 분석 데이터 (뉴스 + 섹터 + 주요 종목)
# ════════════════════════════════════════════════════════════════

def _strip_html(text: str) -> str:
    """HTML 태그 및 개행 제거 후 공백 정규화"""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)  # HTML 엔티티
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _fetch_news_detailed(url: str, label: str, n: int = 6) -> list[dict]:
    """RSS 피드에서 제목 + 요약 수집. 실패 시 빈 리스트."""
    try:
        feed = feedparser.parse(url)
        results = []
        for e in feed.entries[:n]:
            title = _strip_html(e.get("title", "")).strip()
            raw = e.get("summary", e.get("description", ""))
            summary = _strip_html(raw).strip()
            # 요약 100자 제한 (단어 경계 유지)
            if len(summary) > 100:
                summary = summary[:100].rsplit(' ', 1)[0].rstrip('.,') + "…"
            if title:
                results.append({"title": title, "summary": summary, "source": label})
        return results
    except Exception as e:
        logger.error(f"{label} RSS 실패: {e}")
        return []


def _fetch_category_news(feeds: list, target_n: int = 10) -> list[dict]:
    """여러 피드 수집 → 제목 중복 제거 → target_n개 반환"""
    seen: set[str] = set()
    items: list[dict] = []
    per_feed = max(5, (target_n // max(len(feeds), 1)) + 3)
    for url, label in feeds:
        for item in _fetch_news_detailed(url, label, per_feed):
            key = item["title"][:25].lower()
            if key not in seen:
                seen.add(key)
                items.append(item)
        if len(items) >= target_n * 2:
            break
    return items[:target_n]


# ════════════════════════════════════════════════════════════════
# 뉴스 블록 — 국제 / 경제·주식 / 국내  (각 10건, 상세 요약 포함)
# ════════════════════════════════════════════════════════════════

def _fmt_news_section(items: list[dict], emoji: str, title: str) -> str:
    """카테고리 뉴스 목록 → Telegram HTML 포맷"""
    lines = [f"{emoji} <b>{title}</b>", ""]
    if not items:
        lines.append("  • 뉴스를 가져올 수 없습니다.")
        return "\n".join(lines)
    for i, item in enumerate(items, 1):
        src = item.get("source", "")
        lines.append(f"<b>{i}.</b> {item['title']}  <i>({src})</i>")
        if item.get("summary"):
            lines.append(f"   └ {item['summary']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def get_news_blocks() -> tuple[str, str, str, list[str]]:
    """
    3개 뉴스 블록 생성.
    Returns:
        (intl_block, econ_block, kr_block, ai_news_list)
    """
    logger.info("뉴스 수집 시작 (국제 / 경제·주식 / 국내)")
    intl  = _fetch_category_news(INTL_FEEDS,  10)
    econ  = _fetch_category_news(ECON_FEEDS,  10)
    kr    = _fetch_category_news(KR_FEEDS,    10)

    intl_block = _fmt_news_section(intl,  "🌍", "국제 뉴스")
    econ_block = _fmt_news_section(econ,  "💹", "경제·주식 뉴스")
    kr_block   = _fmt_news_section(kr,    "🇰🇷", "국내 뉴스")

    # AI 프롬프트용 핵심 뉴스 (카테고리별 상위 4건)
    ai_news = (
        [f"[국제] {i['title']}" for i in intl[:4]]
        + [f"[경제] {i['title']}" for i in econ[:4]]
        + [f"[국내] {i['title']}" for i in kr[:4]]
    )
    logger.info(f"뉴스 수집 완료 — 국제 {len(intl)} / 경제 {len(econ)} / 국내 {len(kr)}건")
    return intl_block, econ_block, kr_block, ai_news


def _get_sector_rows() -> list[str]:
    """섹터 ETF 등락 테이블 행 반환"""
    rows = []
    try:
        d = yf.download(list(SECTOR_ETFS), period="5d", interval="1d", progress=False, auto_adjust=True)
        pairs = []
        for sym, label in SECTOR_ETFS.items():
            pv, cv = _last_two(d["Close"], sym)
            if cv is None:
                continue
            pct = (cv - pv) / pv * 100
            pairs.append((label, pct))
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
        d = yf.download(list(US_TOP10), period="5d", interval="1d", progress=False, auto_adjust=True)
        for sym, name in US_TOP10.items():
            pv, cv = _last_two(d["Close"], sym)
            if cv is None:
                rows.append(f"  {sym:<5} {name:<10}  -")
                continue
            pct = (cv - pv) / pv * 100
            rows.append(f"{_arrow(pct)} {sym:<5} {name:<10} {cv:>9,.2f}  {pct:>+6.2f}%")
            summary[sym] = {"name": name, "close": cv, "pct": pct}
    except Exception as e:
        logger.error(f"주요종목 10선: {e}")
        rows.append("  데이터 조회 실패")
    return rows, summary


def get_us_data_block(ai_news: Optional[List[str]] = None) -> tuple[str, dict]:
    """
    미국장 분석 블록 (섹터 성과 + 주요 종목 10선).
    뉴스는 get_news_blocks()에서 별도 처리하므로 여기선 제외.

    Args:
        ai_news: AI 프롬프트에 주입할 뉴스 헤드라인 리스트
    Returns:
        (telegram_text, ctx_dict)
    """
    sector_rows = _get_sector_rows()
    top10_rows, top10_summary = _get_top10_rows()

    lines = [
        "📌 <b>미국장 분석</b>",
        "",
        "📊 <b>섹터 성과</b>",
        "<pre>" + "\n".join(sector_rows) + "</pre>",
        "",
        "💹 <b>주요 종목 10선</b>",
        "<pre>" + "\n".join(top10_rows) + "</pre>",
    ]

    ctx = {
        "news": ai_news or [],
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

    # 데이터 수집 (순서 유지 — KIS API 레이트리밋 고려)
    market_block                          = get_market_summary()
    leading_block                         = get_leading_stocks(portfolio)
    universe_block                        = get_universe_scan(portfolio)
    intl_block, econ_block, kr_block, ai_news = get_news_blocks()
    us_block, ctx                         = get_us_data_block(ai_news=ai_news)
    ai_block                              = get_ai_strategy_block(ctx, portfolio)

    header = (
        f"🌅 <b>모닝 브리핑 │ {today_str} ({day_of_week})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    footer = "━━━━━━━━━━━━━━━━━━━━\n✅ <b>브리핑 완료</b> │ 좋은 하루 되세요!"

    # 8블록 순서: 시장요약 / 포트폴리오 주도주 / 유니버스 / 국제뉴스 / 경제뉴스 / 국내뉴스 / 미국분석 / AI전략
    blocks = [
        f"{header}\n\n{market_block}",
        leading_block,
        universe_block,
        intl_block,
        econ_block,
        kr_block,
        us_block,
        f"{ai_block}\n\n{footer}",
    ]

    for i, block in enumerate(blocks, 1):
        ok = send(block)
        logger.info(f"브리핑 블록 {i}/{len(blocks)} 전송: {'성공' if ok else '실패'}")


if __name__ == "__main__":
    run()
