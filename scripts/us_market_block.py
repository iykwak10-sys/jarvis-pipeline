#!/usr/bin/env python3
"""미국·한국 증시 마감 블록 (마크다운, 모바일 가독성).

Hermes 통합 모닝 브리핑(cron 52f01a1aaab2)이 이 stdout을 그대로 임베드한다.
jarvis 코드에 의존하지 않는다(자체완결).
실행: ~/.briefing-venv/bin/python3 scripts/us_market_block.py

데이터 소스:
  - 미국 지수·환율·원자재·섹터·종목: yfinance.
  - 한국 지수(KOSPI/KOSDAQ): Naver 금융 API. yfinance ^KS11/^KQ11는
    한국 지수를 1영업일 지연 제공해 새벽 브리핑에 전일 종가가 아닌
    전전일 종가가 찍히는 버그가 있어 Naver로 대체(토큰 불필요).

출력 형식:
  - HTML 태그 없음(Telegram MarkdownV2 변환기와 호환).
  - 섹션 제목은 **굵게**, 지표는 한 줄에 하나(스마트폰 가독성).
  - KOSPI/KOSDAQ는 직전 영업일 종가 등락률로 표기.

신선도 가드:
  - 각 블록 헤더에 데이터의 실제 거래일을 표기한다(예: "🇺🇸 미국 (6/23 마감)").
  - 데이터 거래일이 직전 평일보다 오래되면 "⚠️지연/휴장 확인"을 붙인다.
    (2026-06-24 KOSPI 1영업일 지연 사고 재발 방지 — 어떤 소스가 지연돼도
    수치만으로는 안 보이던 stale이 헤더 날짜로 즉시 드러난다.)
"""

import json
import math
import sys
import urllib.request
from datetime import date, datetime, timedelta

import yfinance as yf

US_INDICES = {
    "^GSPC": "S&P500",
    "^IXIC": "나스닥",
    "^DJI": "다우",
    "^SOX": "반도체 SOX",
}
KR_INDICES = {  # Naver 인덱스 코드 → 표시명
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
}
FX_COMMOD = {
    "USDKRW=X": ("달러/원", 1.0, "원"),
    "JPYKRW=X": ("100엔/원", 100.0, "원"),
    "CL=F": ("WTI", 1.0, ""),
    "^VIX": ("VIX", 1.0, ""),
}
SECTOR_ETFS = {
    "XLK": "기술", "XLC": "커뮤니케이션", "XLY": "임의소비재",
    "XLF": "금융", "XLI": "산업재", "XLV": "헬스케어",
    "XLE": "에너지", "XLB": "소재", "XLP": "필수소비재", "XLRE": "부동산",
}
US_TOP10 = {
    "NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트",
    "GOOGL": "알파벳", "AMZN": "아마존", "META": "메타",
    "TSLA": "테슬라", "AVGO": "브로드컴", "AMD": "AMD", "JPM": "JP모건",
}


def _arrow(pct: float) -> str:
    if pct > 0.05:
        return "🔺"
    if pct < -0.05:
        return "🔻"
    return "➖"


def _last_two(close, sym):
    """심볼의 마지막 2개 유효(non-NaN) 종가 (prev, curr, bar_date). 최신행 NaN 폴백."""
    try:
        s = close[sym].dropna()
    except Exception:
        return None, None, None
    if len(s) < 2:
        return None, None, None
    prev, curr = float(s.iloc[-2]), float(s.iloc[-1])
    if math.isnan(prev) or math.isnan(curr):
        return None, None, None
    return prev, curr, s.index[-1].date()


def _prev_weekday(today: date) -> date:
    """오늘(KST) 기준 직전 평일 — 새벽 브리핑이 기대하는 '전일' 거래일."""
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _dated_header(base: str, bar_date, suffix: str) -> str:
    """블록 헤더에 데이터 거래일 표기 + 기대일보다 오래되면 지연 배지."""
    if bar_date is None:
        return base
    tag = f"{bar_date.month}/{bar_date.day} {suffix}".rstrip()
    if bar_date < _prev_weekday(date.today()):
        tag += " ⚠️지연/휴장 확인"
    return f"{base} ({tag})"


def _download(symbols):
    return yf.download(
        list(symbols), period="7d", interval="1d",
        progress=False, auto_adjust=True,
    )["Close"]


def _index_lines(close, mapping):
    """(lines, last_bar_date) 반환 — 날짜는 헤더 신선도 표기에 사용."""
    lines = []
    last_date = None
    for sym, label in mapping.items():
        pv, cv, bd = _last_two(close, sym)
        if cv is None:
            continue
        pct = (cv - pv) / pv * 100
        lines.append(f"{_arrow(pct)} {label} {cv:,.2f} ({pct:+.2f}%)")
        if bd and (last_date is None or bd > last_date):
            last_date = bd
    return lines, last_date


def _naver_kr_lines():
    """Naver 금융에서 KOSPI/KOSDAQ 직전 영업일 종가·등락률 조회.

    yfinance의 한국 지수 1영업일 지연 문제를 피하기 위한 기본 소스.
    실패 시 빈 리스트를 반환해 호출측이 yfinance로 폴백한다.
    반환: (lines, trade_date)
    """
    lines = []
    trade_date = None
    for code, label in KR_INDICES.items():
        try:
            req = urllib.request.Request(
                f"https://m.stock.naver.com/api/index/{code}/price?pageSize=2&page=1",
                headers={"User-Agent": "Mozilla/5.0",
                         "Referer": "https://m.stock.naver.com/"},
            )
            rows = json.loads(urllib.request.urlopen(req, timeout=10).read())
            row = rows[0] if isinstance(rows, list) and rows else None
            if not row:
                return [], None
            close = float(str(row["closePrice"]).replace(",", ""))
            pct = float(str(row["fluctuationsRatio"]).replace(",", ""))
            lines.append(f"{_arrow(pct)} {label} {close:,.2f} ({pct:+.2f}%)")
            raw_dt = str(row.get("localTradedAt", ""))[:10]
            try:
                d = datetime.strptime(raw_dt, "%Y-%m-%d").date()
                if trade_date is None or d > trade_date:
                    trade_date = d
            except ValueError:
                pass
        except Exception:
            return [], None
    return lines, trade_date


def market_summary() -> str:
    out = ["**📊 시장 요약**", ""]

    us, us_date = _index_lines(_download(US_INDICES), US_INDICES)
    if us:
        out.append(_dated_header("🇺🇸 미국", us_date, "마감"))
        out += us
        out.append("")

    kr, kr_date = _naver_kr_lines()  # Naver 우선 (yfinance는 한국 지수 1일 지연)
    if not kr:  # 폴백: yfinance ^KS11/^KQ11 (지연 가능)
        kr, kr_date = _index_lines(
            _download({"^KS11": "KOSPI", "^KQ11": "KOSDAQ"}),
            {"^KS11": "KOSPI", "^KQ11": "KOSDAQ"},
        )
    if kr:
        out.append(_dated_header("🇰🇷 한국", kr_date, "종가"))
        out += kr
        out.append("")

    fx_close = _download(FX_COMMOD)
    fx_lines = []
    for sym, (label, mult, unit) in FX_COMMOD.items():
        pv, cv, _bd = _last_two(fx_close, sym)
        if cv is None:
            continue
        cv, pv = cv * mult, pv * mult
        pct = (cv - pv) / pv * 100
        fx_lines.append(f"{_arrow(pct)} {label} {cv:,.2f}{unit} ({pct:+.2f}%)")
    if fx_lines:
        out.append("💱 환율·원자재")
        out += fx_lines

    return "\n".join(out).rstrip()


def sector_block() -> str:
    close = _download(SECTOR_ETFS)
    pairs = []
    last_date = None
    for sym, label in SECTOR_ETFS.items():
        pv, cv, bd = _last_two(close, sym)
        if cv is None:
            continue
        pairs.append((label, (cv - pv) / pv * 100))
        if bd and (last_date is None or bd > last_date):
            last_date = bd
    pairs.sort(key=lambda x: x[1], reverse=True)
    lines = [f"{_arrow(p)} {label} ({p:+.2f}%)" for label, p in pairs]
    if not lines:
        return ""
    return _dated_header("**📊 섹터 성과**", last_date, "") + "\n\n" + "\n".join(lines)


def top10_block() -> str:
    close = _download(US_TOP10)
    lines = []
    last_date = None
    for sym, name in US_TOP10.items():
        pv, cv, bd = _last_two(close, sym)
        if cv is None:
            continue
        pct = (cv - pv) / pv * 100
        lines.append(f"{_arrow(pct)} {sym} {name} {cv:,.2f} ({pct:+.2f}%)")
        if bd and (last_date is None or bd > last_date):
            last_date = bd
    if not lines:
        return ""
    return _dated_header("**💹 주요 종목 10선**", last_date, "") + "\n\n" + "\n".join(lines)


def main() -> None:
    blocks = [b for b in (market_summary(), sector_block(), top10_block()) if b]
    if not blocks:
        print("시장 데이터 조회 실패")
        sys.exit(0)
    print("\n\n".join(blocks))


if __name__ == "__main__":
    main()
