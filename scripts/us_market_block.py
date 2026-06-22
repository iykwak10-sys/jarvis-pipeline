#!/usr/bin/env python3
"""미국·한국 증시 마감 블록 (마크다운, 모바일 가독성).

Hermes 통합 모닝 브리핑(cron 52f01a1aaab2)이 이 stdout을 그대로 임베드한다.
yfinance 결정론 데이터만 사용하며 jarvis 코드에 의존하지 않는다(자체완결).
실행: ~/.briefing-venv/bin/python3 scripts/us_market_block.py

출력 형식:
  - HTML 태그 없음(Telegram MarkdownV2 변환기와 호환).
  - 섹션 제목은 **굵게**, 지표는 한 줄에 하나(스마트폰 가독성).
  - KOSPI/KOSDAQ는 전일 종가 등락률(close-to-close)로 표기.
"""

import math
import sys

import yfinance as yf

US_INDICES = {
    "^GSPC": "S&P500",
    "^IXIC": "나스닥",
    "^DJI": "다우",
    "^SOX": "반도체 SOX",
}
KR_INDICES = {
    "^KS11": "KOSPI",
    "^KQ11": "KOSDAQ",
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
    """심볼의 마지막 2개 유효(non-NaN) 종가 (prev, curr). 최신행 NaN 폴백."""
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


def _download(symbols):
    return yf.download(
        list(symbols), period="7d", interval="1d",
        progress=False, auto_adjust=True,
    )["Close"]


def _index_lines(close, mapping) -> list:
    lines = []
    for sym, label in mapping.items():
        pv, cv = _last_two(close, sym)
        if cv is None:
            continue
        pct = (cv - pv) / pv * 100
        lines.append(f"{_arrow(pct)} {label} {cv:,.2f} ({pct:+.2f}%)")
    return lines


def market_summary() -> str:
    out = ["**📊 시장 요약**", ""]

    us = _index_lines(_download(US_INDICES), US_INDICES)
    if us:
        out.append("🇺🇸 미국 (전일 마감)")
        out += us
        out.append("")

    kr = _index_lines(_download(KR_INDICES), KR_INDICES)
    if kr:
        out.append("🇰🇷 한국 (전일 종가)")
        out += kr
        out.append("")

    fx_close = _download(FX_COMMOD)
    fx_lines = []
    for sym, (label, mult, unit) in FX_COMMOD.items():
        pv, cv = _last_two(fx_close, sym)
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
    for sym, label in SECTOR_ETFS.items():
        pv, cv = _last_two(close, sym)
        if cv is None:
            continue
        pairs.append((label, (cv - pv) / pv * 100))
    pairs.sort(key=lambda x: x[1], reverse=True)
    lines = [f"{_arrow(p)} {label} ({p:+.2f}%)" for label, p in pairs]
    if not lines:
        return ""
    return "**📊 섹터 성과**\n\n" + "\n".join(lines)


def top10_block() -> str:
    close = _download(US_TOP10)
    lines = []
    for sym, name in US_TOP10.items():
        pv, cv = _last_two(close, sym)
        if cv is None:
            continue
        pct = (cv - pv) / pv * 100
        lines.append(f"{_arrow(pct)} {sym} {name} {cv:,.2f} ({pct:+.2f}%)")
    if not lines:
        return ""
    return "**💹 주요 종목 10선**\n\n" + "\n".join(lines)


def main() -> None:
    blocks = [b for b in (market_summary(), sector_block(), top10_block()) if b]
    if not blocks:
        print("시장 데이터 조회 실패")
        sys.exit(0)
    print("\n\n".join(blocks))


if __name__ == "__main__":
    main()
