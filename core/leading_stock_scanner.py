# core/leading_stock_scanner.py
"""주도주 포착 알고리즘 (7-Condition Leading Stock Scanner)

조건:
  1. 거래대금 ≥ 1,000억원          — 시장 관심도 폭발
  2. 기관+외국인 양매수             — 메이저 수급 동시 유입
  3. 등락률 ≥ 5%                   — 강세 종목 필터
  4. 거래량 급증 ≥ 전일 200%       — 유동성 폭발 신호
  5. 52주 신고가 대비 90% 이상      — 모멘텀 구간 진입
  6. 시가총액 ≥ 3,000억원           — 소형주 노이즈 제거
  7. 장중 강도 ≥ 70%               — 종가가 당일 고저 범위 상위권

포트폴리오 종목 대상으로 스캔 후 통과 조건 수에 따라 점수화.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ─── 조건 임계값 ─────────────────────────────────────────────────
THRESHOLD = {
    "trade_value_100m": 1000,   # 거래대금 ≥ 1,000억원 (단위: 억원)
    "change_pct": 5.0,          # 등락률 ≥ 5%
    "vol_rate": 200.0,          # 거래량 ≥ 전일 200% (prdy_vrss_vol_rate)
    "high52_ratio": 0.90,       # 현재가 ÷ 52주고가 ≥ 90%
    "market_cap_100m": 3000,    # 시가총액 ≥ 3,000억원
    "intraday_strength": 0.70,  # 장중강도 (종가위치) ≥ 70%
}

MAX_CONDITIONS = 7


@dataclass
class StockScore:
    code: str
    name: str
    score: int                          # 통과 조건 수 (0~7)
    passed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _score_stock(price: dict, investor: dict) -> StockScore:
    """단일 종목에 7개 조건 적용 후 점수 반환"""
    code = price["code"]
    name = price.get("name", code)
    passed, failed = [], []
    metrics = {}

    # ① 거래대금 ≥ 1,000억원
    trade_value_m = price.get("trade_value_m", 0)  # 단위: 원
    trade_value_100m = trade_value_m / 1e8          # → 억원
    metrics["거래대금(억)"] = f"{trade_value_100m:,.0f}"
    if trade_value_100m >= THRESHOLD["trade_value_100m"]:
        passed.append("거래대금")
    else:
        failed.append("거래대금")

    # ② 기관+외국인 양매수
    frgn = investor.get("frgn_qty", 0)
    orgn = investor.get("orgn_qty", 0)
    metrics["외국인(주)"] = f"{frgn:+,}"
    metrics["기관(주)"] = f"{orgn:+,}"
    if frgn > 0 and orgn > 0:
        passed.append("양매수")
    else:
        failed.append("양매수")

    # ③ 등락률 ≥ 5%
    chg = price.get("change_pct", 0.0)
    metrics["등락률"] = f"{chg:+.2f}%"
    if chg >= THRESHOLD["change_pct"]:
        passed.append("등락률")
    else:
        failed.append("등락률")

    # ④ 거래량 급증 (전일 대비 200% 이상 — prdy_vrss_vol_rate 직접 사용)
    vol_rate = price.get("vol_rate", 0.0)
    metrics["거래량비율"] = f"{vol_rate:.0f}%"
    if vol_rate >= THRESHOLD["vol_rate"]:
        passed.append("거래량급증")
    else:
        failed.append("거래량급증")

    # ⑤ 52주 신고가 대비 90% 이상
    close = price.get("close", 0)
    high52 = price.get("high52", 0)
    if high52 > 0:
        ratio52 = close / high52
    else:
        ratio52 = 0.0
    metrics["52주고가비"] = f"{ratio52:.1%}"
    if ratio52 >= THRESHOLD["high52_ratio"]:
        passed.append("신고가근접")
    else:
        failed.append("신고가근접")

    # ⑥ 시가총액 ≥ 3,000억원
    mktcap = price.get("market_cap_100m", 0)
    metrics["시총(억)"] = f"{mktcap:,}"
    if mktcap >= THRESHOLD["market_cap_100m"]:
        passed.append("시총필터")
    else:
        failed.append("시총필터")

    # ⑦ 장중 강도 ≥ 70% (종가가 당일 고저 범위 상위권)
    high = price.get("high", 0)
    low = price.get("low", 0)
    if high > low:
        strength = (close - low) / (high - low)
    else:
        strength = 0.0
    metrics["장중강도"] = f"{strength:.1%}"
    if strength >= THRESHOLD["intraday_strength"]:
        passed.append("장중강도")
    else:
        failed.append("장중강도")

    return StockScore(
        code=code,
        name=name,
        score=len(passed),
        passed=passed,
        failed=failed,
        metrics=metrics,
    )


def scan(
    codes: List[str],
    name_map: Optional[dict] = None,
    min_score: int = 4,
) -> List[StockScore]:
    """포트폴리오 종목 코드 리스트를 스캔해 min_score 이상 종목 반환.

    Args:
        codes: 6자리 KRX 종목코드 리스트
        name_map: {code: name} 종목명 매핑 (없으면 코드로 표시)
        min_score: 최소 통과 조건 수 (기본 4/7)
    """
    from core.kis_client import KISClient
    kis = KISClient()
    nm = name_map or {}

    results: List[StockScore] = []
    for code in codes:
        try:
            price = kis.get_price_full(code)
            if not price.get("name") or price["name"] == code:
                price["name"] = nm.get(code, code)
            investor = kis.get_investor_daily(code)
            s = _score_stock(price, investor)
            if s.score >= min_score:
                results.append(s)
            logger.debug(f"{code} {price['name']} {s.score}/{MAX_CONDITIONS}: {s.passed}")
        except Exception as e:
            logger.warning(f"종목 {code} 스캔 실패: {e}")
        time.sleep(0.15)  # KIS API rate limit 준수

    return sorted(results, key=lambda x: x.score, reverse=True)


def format_telegram(results: List[StockScore]) -> str:
    """스캔 결과를 Telegram HTML 블록으로 변환"""
    if not results:
        return (
            "🔍 <b>주도주 포착 스캐너</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "• 오늘은 조건을 충족한 주도주 후보가 없습니다."
        )

    lines = [
        "🔍 <b>주도주 포착 스캐너 (7-Condition)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    medal = {7: "🥇", 6: "🥈", 5: "🥉", 4: "4️⃣"}
    for s in results:
        icon = medal.get(s.score, "⭐")
        cond_icons = " ".join([f"✅{c}" for c in s.passed])
        metric_str = " | ".join(f"{k}:{v}" for k, v in s.metrics.items())
        lines.append(
            f"\n{icon} <b>{s.name}</b> ({s.code}) — {s.score}/{MAX_CONDITIONS}점\n"
            f"   {cond_icons}\n"
            f"   {metric_str}"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 총 {len(results)}종목 포착 (7조건 중 4개 이상 충족)")
    return "\n".join(lines)
