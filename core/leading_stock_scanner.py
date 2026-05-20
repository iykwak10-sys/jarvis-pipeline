# core/leading_stock_scanner.py
"""주도주 포착 알고리즘 (9-Condition Leading Stock Scanner)

조건:
  1. 거래대금 ≥ 1,000억원          — 시장 관심도 폭발
  2. 기관+외국인 양매수             — 메이저 수급 동시 유입 (당일)
  3. 등락률 ≥ 4%                   — 강세 종목 필터
  4. 거래량 급증 ≥ 전일 150%       — 유동성 폭발 신호
  5. 52주 신고가 대비 90% 이상      — 모멘텀 구간 진입
  6. 시가총액 ≥ 2,500억원           — 소형주 노이즈 제거
  7. 장중 강도 ≥ 70%               — 종가가 당일 고저 범위 상위권
  8. 외국인 연속 순매수 ≥ 5일       — 스마트머니 지속 유입
  9. 기관 연속 순매수 ≥ 5일         — 기관 집중 매집 확인

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
    "change_pct": 4.0,          # 등락률 ≥ 4%
    "vol_rate": 150.0,          # 거래량 ≥ 전일 150% (prdy_vrss_vol_rate)
    "high52_ratio": 0.90,       # 현재가 ÷ 52주고가 ≥ 90%
    "market_cap_100m": 2500,    # 시가총액 ≥ 2,500억원
    "intraday_strength": 0.70,  # 장중강도 (종가위치) ≥ 70%
    "consec_frgn_days": 5,      # 외국인 연속 순매수 ≥ 5일
    "consec_orgn_days": 5,      # 기관 연속 순매수 ≥ 5일
}

MAX_CONDITIONS = 9


@dataclass
class StockScore:
    code: str
    name: str
    score: int                          # 통과 조건 수 (0~9)
    passed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _consecutive_days(history: list, key: str) -> int:
    """히스토리(최신순)에서 key 값이 양수인 연속 일수를 반환"""
    count = 0
    for item in history:
        if item.get(key, 0) > 0:
            count += 1
        else:
            break
    return count


def _score_stock(price: dict, history: list) -> StockScore:
    """단일 종목에 9개 조건 적용 후 점수 반환

    Args:
        price: get_price_full() 결과
        history: get_investor_history() 결과 (최신순 리스트)
    """
    code = price["code"]
    name = price.get("name", code)
    passed, failed = [], []
    metrics = {}

    # 당일 수급 (히스토리 첫 번째 항목)
    today_inv = history[0] if history else {}

    # ① 거래대금 ≥ 1,000억원
    trade_value_100m = price.get("trade_value_m", 0) / 1e8
    metrics["거래대금(억)"] = f"{trade_value_100m:,.0f}"
    if trade_value_100m >= THRESHOLD["trade_value_100m"]:
        passed.append("거래대금")
    else:
        failed.append("거래대금")

    # ② 기관+외국인 양매수 (당일)
    frgn_today = today_inv.get("frgn_qty", 0)
    orgn_today = today_inv.get("orgn_qty", 0)
    metrics["외국인(주)"] = f"{frgn_today:+,}"
    metrics["기관(주)"] = f"{orgn_today:+,}"
    if frgn_today > 0 and orgn_today > 0:
        passed.append("양매수")
    else:
        failed.append("양매수")

    # ③ 등락률 ≥ 4%
    chg = price.get("change_pct", 0.0)
    metrics["등락률"] = f"{chg:+.2f}%"
    if chg >= THRESHOLD["change_pct"]:
        passed.append("등락률")
    else:
        failed.append("등락률")

    # ④ 거래량 급증 (전일 대비 150% 이상)
    vol_rate = price.get("vol_rate", 0.0)
    metrics["거래량비율"] = f"{vol_rate:.0f}%"
    if vol_rate >= THRESHOLD["vol_rate"]:
        passed.append("거래량급증")
    else:
        failed.append("거래량급증")

    # ⑤ 52주 신고가 대비 90% 이상
    close = price.get("close", 0)
    high52 = price.get("high52", 0)
    ratio52 = (close / high52) if high52 > 0 else 0.0
    metrics["52주고가비"] = f"{ratio52:.1%}"
    if ratio52 >= THRESHOLD["high52_ratio"]:
        passed.append("신고가근접")
    else:
        failed.append("신고가근접")

    # ⑥ 시가총액 ≥ 2,500억원
    mktcap = price.get("market_cap_100m", 0)
    metrics["시총(억)"] = f"{mktcap:,}"
    if mktcap >= THRESHOLD["market_cap_100m"]:
        passed.append("시총필터")
    else:
        failed.append("시총필터")

    # ⑦ 장중 강도 ≥ 70%
    high = price.get("high", 0)
    low = price.get("low", 0)
    strength = ((close - low) / (high - low)) if high > low else 0.0
    metrics["장중강도"] = f"{strength:.1%}"
    if strength >= THRESHOLD["intraday_strength"]:
        passed.append("장중강도")
    else:
        failed.append("장중강도")

    # ⑧ 외국인 연속 순매수 ≥ 5일
    frgn_consec = _consecutive_days(history, "frgn_qty")
    metrics["외국인연속"] = f"{frgn_consec}일"
    if frgn_consec >= THRESHOLD["consec_frgn_days"]:
        passed.append("외국인연속매수")
    else:
        failed.append("외국인연속매수")

    # ⑨ 기관 연속 순매수 ≥ 5일
    orgn_consec = _consecutive_days(history, "orgn_qty")
    metrics["기관연속"] = f"{orgn_consec}일"
    if orgn_consec >= THRESHOLD["consec_orgn_days"]:
        passed.append("기관연속매수")
    else:
        failed.append("기관연속매수")

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
        min_score: 최소 통과 조건 수 (기본 4/9)
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
            history = kis.get_investor_history(code)
            s = _score_stock(price, history)
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
        "🔍 <b>주도주 포착 스캐너 (9-Condition)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    medal = {9: "🥇", 8: "🥇", 7: "🥈", 6: "🥉", 5: "4️⃣"}
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
    lines.append(f"📊 총 {len(results)}종목 포착 (9조건 중 {min(s.score for s in results)}개 이상 충족)")
    return "\n".join(lines)
