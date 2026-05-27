# core/universe_scanner.py
"""시장 전체 주도주 스캔 (Phase 2 — Universe Expansion)

KOSPI 거래대금 상위 50종목 + KOSDAQ 상위 30종목을 자동 수집,
포트폴리오 보유 여부를 플래그하여 12-Condition 스캐너에 투입.

결과:
  - portfolio_hits : 포트폴리오 보유 종목 중 주도주 조건 충족
  - new_candidates : 신규 발굴 종목 (미보유)
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from core.leading_stock_scanner import (
    MAX_SCORE,
    StockScore,
    scan,
    format_telegram,
)

logger = logging.getLogger(__name__)

# KOSPI 상위 N + KOSDAQ 상위 M 합산으로 유니버스 구성
# ⚠️ KIS volume-rank API 응답 상한 = 30건/호출, 페이지네이션 미지원
#    → 실질 최대 유니버스: KOSPI 30 + KOSDAQ 30 = 60종목
KOSPI_TOP_N = 30
KOSDAQ_TOP_M = 30


@dataclass
class UniverseScanResult:
    portfolio_hits: List[StockScore]
    new_candidates: List[StockScore]
    universe_size: int          # 실제 스캔한 총 종목 수
    kospi_fetched: int
    kosdaq_fetched: int


def _fetch_universe(kis) -> Tuple[List[str], int, int]:
    """KOSPI + KOSDAQ 거래대금 상위 종목 코드 수집"""
    kospi_codes: List[str] = []
    kosdaq_codes: List[str] = []

    try:
        kospi_codes = kis.get_top_trade_value_codes(market="J", top_n=KOSPI_TOP_N)
        logger.info(f"KOSPI 유니버스 {len(kospi_codes)}종목 수집")
    except Exception as e:
        logger.warning(f"KOSPI 유니버스 수집 실패: {e}")

    time.sleep(0.3)

    try:
        kosdaq_codes = kis.get_top_trade_value_codes(market="Q", top_n=KOSDAQ_TOP_M)
        logger.info(f"KOSDAQ 유니버스 {len(kosdaq_codes)}종목 수집")
    except Exception as e:
        logger.warning(f"KOSDAQ 유니버스 수집 실패: {e}")

    # 중복 제거 (KOSPI 우선)
    seen: Set[str] = set()
    combined: List[str] = []
    for code in kospi_codes + kosdaq_codes:
        if code not in seen:
            seen.add(code)
            combined.append(code)

    return combined, len(kospi_codes), len(kosdaq_codes)


def scan_market(
    portfolio_codes: Optional[Set[str]] = None,
    name_map: Optional[Dict[str, str]] = None,
    min_score: int = 9,
) -> UniverseScanResult:
    """시장 전체 주도주 스캔 실행

    Args:
        portfolio_codes: 포트폴리오 보유 코드 집합 (포트폴리오 플래그용)
        name_map:        {code: name} 종목명 매핑
        min_score:       최소 가중 점수 (기본 9/15)

    Returns:
        UniverseScanResult (portfolio_hits, new_candidates 분리)
    """
    from core.kis_client import KISClient
    kis = KISClient()
    pf = portfolio_codes or set()

    universe, kospi_n, kosdaq_m = _fetch_universe(kis)

    if not universe:
        logger.warning("유니버스 수집 실패 — 빈 결과 반환")
        return UniverseScanResult(
            portfolio_hits=[],
            new_candidates=[],
            universe_size=0,
            kospi_fetched=kospi_n,
            kosdaq_fetched=kosdaq_m,
        )

    logger.info(f"유니버스 {len(universe)}종목 대상 주도주 스캔 시작")
    all_results = scan(
        codes=universe,
        name_map=name_map,
        min_score=min_score,
        portfolio_codes=pf,
    )

    portfolio_hits = [s for s in all_results if s.is_portfolio]
    new_candidates = [s for s in all_results if not s.is_portfolio]

    logger.info(
        f"스캔 완료 — 포트폴리오 적중 {len(portfolio_hits)}종목, "
        f"신규 후보 {len(new_candidates)}종목"
    )

    return UniverseScanResult(
        portfolio_hits=portfolio_hits,
        new_candidates=new_candidates,
        universe_size=len(universe),
        kospi_fetched=kospi_n,
        kosdaq_fetched=kosdaq_m,
    )


def format_universe_telegram(result: UniverseScanResult) -> str:
    """유니버스 스캔 결과 Telegram 메시지 생성

    포트폴리오 보유 종목과 신규 후보를 분리하여 2개 섹션으로 구성.
    """
    parts: List[str] = []
    header = (
        f"🌐 <b>시장 유니버스 스캔</b> "
        f"(KOSPI {result.kospi_fetched} + KOSDAQ {result.kosdaq_fetched} = "
        f"{result.universe_size}종목 대상)"
    )
    parts.append(header)

    if result.portfolio_hits:
        parts.append(
            format_telegram(result.portfolio_hits, title="★ 포트폴리오 주도주 확인")
        )
    else:
        parts.append("★ <b>포트폴리오 주도주 확인</b>\n• 조건 충족 보유 종목 없음")

    if result.new_candidates:
        parts.append(
            format_telegram(result.new_candidates, title="🆕 신규 주도주 후보")
        )
    else:
        parts.append("🆕 <b>신규 주도주 후보</b>\n• 금일 신규 포착 없음")

    return "\n\n".join(parts)
