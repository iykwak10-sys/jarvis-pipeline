# core/leading_stock_scanner.py
"""주도주 포착 알고리즘 (12-Condition Weighted Leading Stock Scanner)

조건 및 가중치:
  [핵심 ×2점]
  1. 거래대금 ≥ 1,000억원          — 시장 관심도 폭발
  2. 기관+외국인 양매수             — 메이저 수급 동시 유입 (당일)
  3. 등락률 ≥ 5%                   — 강세 종목 필터

  [일반 ×1점]
  4. 거래량 급증 ≥ 전일 150%       — 유동성 폭발 신호
  5. 시가총액 ≥ 2,500억원           — 소형주 노이즈 제거
  6. 장중 강도 ≥ 70%               — 종가가 당일 고저 범위 상위권
  7. 외국인 연속 순매수 ≥ 5일       — 스마트머니 지속 유입
  8. 기관 연속 순매수 ≥ 5일         — 기관 집중 매집 확인
  9. 52주 신고가 대비 90% 이상      — 모멘텀 구간 진입
  10. 신고가 실제 돌파 (close ≥ 52주고가) — 신고가 갱신 강세
  11. MA 정배열 (20일 > 60일)        — 중기 상승 추세 확인
  12. RSI 40~70 구간                 — 과매수·과매도 없는 건전한 모멘텀

만점: 핵심 3개×2 + 일반 9개×1 = 15점
포트폴리오 및 시장 전체 유니버스 대상 스캔 지원.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ─── 조건 임계값 ─────────────────────────────────────────────────
THRESHOLD = {
    "trade_value_100m": 1000,   # 거래대금 ≥ 1,000억원 (단위: 억원)
    "change_pct": 5.0,          # 등락률 ≥ 5% (핵심조건 강화)
    "vol_rate": 150.0,          # 거래량 ≥ 전일 150%
    "high52_ratio": 0.90,       # 현재가 ÷ 52주고가 ≥ 90%
    "market_cap_100m": 2500,    # 시가총액 ≥ 2,500억원
    "intraday_strength": 0.70,  # 장중강도 ≥ 70%
    "consec_frgn_days": 5,      # 외국인 연속 순매수 ≥ 5일
    "consec_orgn_days": 5,      # 기관 연속 순매수 ≥ 5일
    "rsi_low": 40.0,            # RSI 하한
    "rsi_high": 70.0,           # RSI 상한
}

MAX_SCORE = 15  # 핵심 3×2 + 일반 9×1


@dataclass
class StockScore:
    code: str
    name: str
    score: int                          # 가중 점수 (0~15)
    passed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    is_portfolio: bool = False          # 포트폴리오 보유 여부


# ─── 유틸 함수 ────────────────────────────────────────────────────

def _consecutive_days(history: list, key: str) -> int:
    """히스토리(최신순)에서 key 값이 양수인 연속 일수 반환"""
    count = 0
    for item in history:
        if item.get(key, 0) > 0:
            count += 1
        else:
            break
    return count


def _calc_ma(closes: list, period: int) -> float:
    """단순이동평균 계산. closes는 oldest→newest 순."""
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period


def _calc_rsi(closes: list, period: int = 14) -> float:
    """RSI(period일) 계산. closes는 oldest→newest 순."""
    if len(closes) < period + 1:
        return 50.0  # 데이터 부족 시 중립값
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = changes[-period:]
    gains = [max(c, 0) for c in recent]
    losses = [max(-c, 0) for c in recent]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


# ─── 핵심 스코어링 ────────────────────────────────────────────────

def _score_stock(price: dict, history: list, closes: list) -> StockScore:
    """단일 종목에 12개 조건 적용 후 가중 점수 반환

    Args:
        price:   get_price_full() 결과
        history: get_investor_history() 결과 (최신순)
        closes:  get_daily_close_prices() 결과 (oldest→newest)
    """
    code = price["code"]
    name = price.get("name", code)
    passed, failed = [], []
    metrics = {}
    weighted_score = 0

    today_inv = history[0] if history else {}

    # ── 핵심 조건 (×2점) ──────────────────────────────────────────

    # ① 거래대금 ≥ 1,000억원
    trade_value_100m = price.get("trade_value_m", 0) / 1e8
    metrics["거래대금(억)"] = f"{trade_value_100m:,.0f}"
    if trade_value_100m >= THRESHOLD["trade_value_100m"]:
        passed.append("거래대금")
        weighted_score += 2
    else:
        failed.append("거래대금")

    # ② 기관+외국인 양매수 (당일)
    frgn_today = today_inv.get("frgn_qty", 0)
    orgn_today = today_inv.get("orgn_qty", 0)
    metrics["외국인(주)"] = f"{frgn_today:+,}"
    metrics["기관(주)"] = f"{orgn_today:+,}"
    if frgn_today > 0 and orgn_today > 0:
        passed.append("양매수")
        weighted_score += 2
    else:
        failed.append("양매수")

    # ③ 등락률 ≥ 5%
    chg = price.get("change_pct", 0.0)
    metrics["등락률"] = f"{chg:+.2f}%"
    if chg >= THRESHOLD["change_pct"]:
        passed.append("등락률")
        weighted_score += 2
    else:
        failed.append("등락률")

    # ── 일반 조건 (×1점) ──────────────────────────────────────────

    # ④ 거래량 급증 ≥ 전일 150%
    vol_rate = price.get("vol_rate", 0.0)
    metrics["거래량비율"] = f"{vol_rate:.0f}%"
    if vol_rate >= THRESHOLD["vol_rate"]:
        passed.append("거래량급증")
        weighted_score += 1
    else:
        failed.append("거래량급증")

    # ⑤ 시가총액 ≥ 2,500억원
    mktcap = price.get("market_cap_100m", 0)
    metrics["시총(억)"] = f"{mktcap:,}"
    if mktcap >= THRESHOLD["market_cap_100m"]:
        passed.append("시총필터")
        weighted_score += 1
    else:
        failed.append("시총필터")

    # ⑥ 장중 강도 ≥ 70%
    close = price.get("close", 0)
    high = price.get("high", 0)
    low = price.get("low", 0)
    strength = ((close - low) / (high - low)) if high > low else 0.0
    metrics["장중강도"] = f"{strength:.1%}"
    if strength >= THRESHOLD["intraday_strength"]:
        passed.append("장중강도")
        weighted_score += 1
    else:
        failed.append("장중강도")

    # ⑦ 외국인 연속 순매수 ≥ 5일
    frgn_consec = _consecutive_days(history, "frgn_qty")
    metrics["외국인연속"] = f"{frgn_consec}일"
    if frgn_consec >= THRESHOLD["consec_frgn_days"]:
        passed.append("외국인연속")
        weighted_score += 1
    else:
        failed.append("외국인연속")

    # ⑧ 기관 연속 순매수 ≥ 5일
    orgn_consec = _consecutive_days(history, "orgn_qty")
    metrics["기관연속"] = f"{orgn_consec}일"
    if orgn_consec >= THRESHOLD["consec_orgn_days"]:
        passed.append("기관연속")
        weighted_score += 1
    else:
        failed.append("기관연속")

    # ⑨ 52주 신고가 대비 90% 이상
    high52 = price.get("high52", 0)
    ratio52 = (close / high52) if high52 > 0 else 0.0
    metrics["52주고가비"] = f"{ratio52:.1%}"
    if ratio52 >= THRESHOLD["high52_ratio"]:
        passed.append("신고가근접")
        weighted_score += 1
    else:
        failed.append("신고가근접")

    # ⑩ 신고가 실제 돌파 (close ≥ 52주고가)
    metrics["신고가돌파"] = "✓" if (high52 > 0 and close >= high52) else "✗"
    if high52 > 0 and close >= high52:
        passed.append("신고가돌파")
        weighted_score += 1
    else:
        failed.append("신고가돌파")

    # ⑪ MA 정배열 (20일MA > 60일MA)
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    metrics["MA20"] = f"{ma20:,.0f}" if ma20 else "N/A"
    metrics["MA60"] = f"{ma60:,.0f}" if ma60 else "N/A"
    if ma20 > 0 and ma60 > 0 and ma20 > ma60:
        passed.append("MA정배열")
        weighted_score += 1
    else:
        failed.append("MA정배열")

    # ⑫ RSI 40~70 구간 (건전한 모멘텀)
    rsi = _calc_rsi(closes)
    metrics["RSI"] = f"{rsi:.1f}"
    if THRESHOLD["rsi_low"] <= rsi <= THRESHOLD["rsi_high"]:
        passed.append("RSI적정")
        weighted_score += 1
    else:
        failed.append("RSI적정")

    return StockScore(
        code=code,
        name=name,
        score=weighted_score,
        passed=passed,
        failed=failed,
        metrics=metrics,
    )


# ─── 스캔 진입점 ──────────────────────────────────────────────────

def scan(
    codes: List[str],
    name_map: Optional[dict] = None,
    min_score: int = 6,
    portfolio_codes: Optional[set] = None,
) -> List[StockScore]:
    """종목 코드 리스트를 스캔해 min_score 이상 종목 반환.

    Args:
        codes:           6자리 KRX 종목코드 리스트
        name_map:        {code: name} 매핑 (없으면 코드로 표시)
        min_score:       최소 가중 점수 (기본 6/15)
        portfolio_codes: 포트폴리오 보유 코드 집합 (is_portfolio 플래그용)
    """
    from core.kis_client import KISClient
    kis = KISClient()
    nm = name_map or {}
    pf = portfolio_codes or set()

    results: List[StockScore] = []
    for code in codes:
        try:
            price = kis.get_price_full(code)
            # 종목명 고정: name_map(순위 API·포트폴리오)이 있으면 항상 우선,
            # 없을 때만 시세 API 종목명 → 그래도 없으면 코드로 폴백
            mapped = nm.get(code)
            if mapped:
                price["name"] = mapped
            elif not price.get("name"):
                price["name"] = code

            history = kis.get_investor_history(code)
            closes = kis.get_daily_close_prices(code, n=70)  # MA60+여유분

            s = _score_stock(price, history, closes)
            s.is_portfolio = code in pf

            if s.score >= min_score:
                results.append(s)

            logger.debug(
                f"{code} {price['name']} {s.score}/{MAX_SCORE}: {s.passed}"
            )
        except Exception as e:
            logger.warning(f"종목 {code} 스캔 실패: {e}")
        time.sleep(0.2)  # get_price + get_investor + get_daily = 3 API 호출

    return sorted(results, key=lambda x: x.score, reverse=True)


# ─── Telegram 포맷터 ──────────────────────────────────────────────

def format_telegram(results: List[StockScore], title: str = "주도주 스캐너") -> str:
    """스캔 결과를 Telegram HTML 테이블로 변환"""
    if not results:
        return f"🔍 <b>{title}</b>\n• 오늘 조건 충족 종목 없음"

    header = f"🔍 <b>{title}</b> — <b>{len(results)}종목 포착</b>"
    medal = {15: "🥇", 14: "🥇", 13: "🥇", 12: "🥈", 11: "🥈", 10: "🥉"}

    col = "종목        점수   등락    거래량  외국인  기관"
    sep = "─" * len(col)
    rows = [col, sep]

    for s in results:
        m = s.metrics
        name = s.name[:7]
        pf_mark = "★" if s.is_portfolio else " "
        rows.append(
            f"{pf_mark}{name:<8} {s.score:>2}/{MAX_SCORE}"
            f"  {m.get('등락률', '?'):>6}"
            f"  {m.get('거래량비율', '?'):>5}"
            f"  {m.get('외국인연속', '?'):>4}"
            f"  {m.get('기관연속', '?'):>4}"
        )

    detail_lines = []
    for s in results:
        icon = medal.get(s.score, "⭐")
        pf_tag = " <b>[보유]</b>" if s.is_portfolio else ""
        rsi_str = s.metrics.get("RSI", "?")
        ma_ok = "MA↑" if "MA정배열" in s.passed else "MA↓"
        new_high = "신고가✓" if "신고가돌파" in s.passed else ""
        extras = " ".join(filter(None, [ma_ok, new_high, f"RSI{rsi_str}"]))
        detail_lines.append(
            f"{icon} <b>{s.name}</b>{pf_tag}: "
            f"{' '.join(s.passed[:6])} | {extras}"
        )

    table = "<pre>" + "\n".join(rows) + "</pre>"
    details = "\n".join(detail_lines)
    return f"{header}\n{table}\n{details}"
