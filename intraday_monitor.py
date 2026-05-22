# intraday_monitor.py
"""장중 주도주 실시간 델타 알림 (Phase 3)

스케줄:
  09:10  --initial  장 시작 초기 스캔 (상태 리셋, 전체 현황 전송)
  10:30             중간 스캔 (변화 종목만 알림)
  13:30             중간 스캔
  15:00             마감 전 스캔

알림 이벤트:
  🆕 신규 포착  — 이전 스캔에 없던 종목이 min_score 이상 진입
  ⬆️ 점수 상승  — 기존 추적 종목 점수 +2 이상 개선
  ⬇️ 레이더 이탈 — 기존 추적 종목 점수 min_score 미만으로 하락

상태 파일: data/intraday_scan_state.json
"""

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import holidays

from core.config import LOG_DIR, PORTFOLIO_FILE
from core.kis_client import KISClient
from core.leading_stock_scanner import MAX_SCORE, StockScore, scan
from core.notifier import send

# ── 상수 ─────────────────────────────────────────────────────────────────────
STATE_FILE = Path(__file__).parent / "data" / "intraday_scan_state.json"
MIN_SCORE = 5           # 추적 진입 최소 점수 (6점보다 낮춰 근접 종목도 감시)
SCORE_UP_DELTA = 2      # ⬆️ 이벤트 발생 최소 점수 상승폭
KOSPI_TOP_N = 40
KOSDAQ_TOP_N = 20

KR_HOLIDAYS = holidays.KR()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_DIR / "intraday_monitor.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def is_trading_day() -> bool:
    today = datetime.now().date()
    if today.weekday() >= 5:
        return False
    if today in KR_HOLIDAYS:
        return False
    return True


def _load_portfolio() -> dict:
    """포트폴리오 CSV → {code: name} 반환"""
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


def _fetch_universe_codes(kis: KISClient, portfolio_codes: set) -> list:
    """KOSPI + KOSDAQ 거래대금 상위 종목 코드 + 포트폴리오 코드 합산"""
    codes = []
    seen = set()

    # 포트폴리오 먼저 (항상 포함)
    for c in portfolio_codes:
        if c not in seen:
            seen.add(c)
            codes.append(c)

    for market, top_n in [("J", KOSPI_TOP_N), ("Q", KOSDAQ_TOP_N)]:
        try:
            fetched = kis.get_top_trade_value_codes(market=market, top_n=top_n)
            for c in fetched:
                if c not in seen:
                    seen.add(c)
                    codes.append(c)
            logger.info(f"{'KOSPI' if market=='J' else 'KOSDAQ'} 유니버스 {len(fetched)}종목 수집")
        except Exception as e:
            logger.warning(f"유니버스 수집 실패 ({market}): {e}")
        time.sleep(0.3)

    return codes


# ── 상태 파일 I/O ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """이전 스캔 상태 로딩. 없거나 오늘 날짜 아니면 빈 상태 반환."""
    today = datetime.now().strftime("%Y-%m-%d")
    if not STATE_FILE.exists():
        return {}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("date") != today:
            logger.info("상태 파일 날짜 불일치 — 초기화")
            return {}
        return state
    except Exception as e:
        logger.warning(f"상태 파일 로딩 실패: {e}")
        return {}


def _save_state(results: list[StockScore], scan_label: str, is_initial: bool) -> None:
    """현재 스캔 결과를 상태 파일로 저장"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    stocks_dict = {
        s.code: {
            "name": s.name,
            "score": s.score,
            "passed": s.passed,
            "is_portfolio": s.is_portfolio,
        }
        for s in results
    }
    state = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "scan_time": datetime.now().strftime("%H:%M"),
        "scan_label": scan_label,
        "is_initial": is_initial,
        "stocks": stocks_dict,
    }
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"상태 저장 완료: {len(stocks_dict)}종목 → {STATE_FILE.name}")


# ── 델타 계산 ────────────────────────────────────────────────────────────────

def _compute_delta(prev_stocks: dict, curr_results: list[StockScore]):
    """이전/현재 상태 비교 → (new_entries, improved, declined, exited) 반환"""
    curr_map = {s.code: s for s in curr_results}
    prev_map = prev_stocks  # {code: {name, score, ...}}

    curr_codes = set(curr_map.keys())
    prev_codes = set(prev_map.keys())

    new_entries = [curr_map[c] for c in (curr_codes - prev_codes)]
    exited = [
        (c, prev_map[c]["name"], prev_map[c]["score"])
        for c in (prev_codes - curr_codes)
    ]
    improved = []
    declined = []

    for code in curr_codes & prev_codes:
        curr_s = curr_map[code]
        prev_score = prev_map[code]["score"]
        delta = curr_s.score - prev_score
        if delta >= SCORE_UP_DELTA:
            improved.append((curr_s, prev_score))
        elif delta <= -SCORE_UP_DELTA:
            declined.append((curr_s, prev_score))

    # 정렬: 점수 높은 순
    new_entries.sort(key=lambda x: x.score, reverse=True)
    improved.sort(key=lambda x: x[0].score, reverse=True)

    return new_entries, improved, declined, exited


# ── Telegram 포맷터 ──────────────────────────────────────────────────────────

def _pf(s: StockScore) -> str:
    return " <b>[보유]</b>" if s.is_portfolio else ""


def _format_initial(results: list[StockScore], scan_label: str) -> str:
    """초기 스캔 — 전체 현황 테이블"""
    now_str = datetime.now().strftime("%H:%M")
    header = (
        f"⚡ <b>장중 주도주 모니터 [{scan_label}]</b> — 장 시작 스캔\n"
        f"🕐 {now_str} | {len(results)}종목 레이더 진입"
    )
    if not results:
        return f"{header}\n• 현재 조건 충족 종목 없음"

    lines = [header, ""]
    pf_hits = [s for s in results if s.is_portfolio]
    new_hits = [s for s in results if not s.is_portfolio]

    if pf_hits:
        lines.append("★ <b>포트폴리오</b>")
        for s in pf_hits:
            bar = "█" * (s.score // 2) + "░" * ((MAX_SCORE - s.score) // 2)
            lines.append(
                f"  {s.name} — <b>{s.score}/{MAX_SCORE}</b>  {bar}\n"
                f"  └ {' · '.join(s.passed[:5])}"
            )

    if new_hits:
        lines.append("\n🆕 <b>신규 후보</b>")
        for s in new_hits[:5]:  # 최대 5종목
            lines.append(
                f"  {s.name} ({s.code}) — <b>{s.score}/{MAX_SCORE}</b>\n"
                f"  └ {' · '.join(s.passed[:5])}"
            )
        if len(new_hits) > 5:
            lines.append(f"  외 {len(new_hits)-5}종목 추가 포착")

    return "\n".join(lines)


def _format_delta(
    new_entries, improved, declined, exited,
    scan_label: str,
) -> str | None:
    """델타 알림 메시지. 변화 없으면 None 반환."""
    if not any([new_entries, improved, declined, exited]):
        return None

    now_str = datetime.now().strftime("%H:%M")
    parts = [f"⚡ <b>장중 주도주 업데이트 [{scan_label}]</b>  🕐 {now_str}"]

    if new_entries:
        parts.append("\n🆕 <b>신규 레이더 진입</b>")
        for s in new_entries[:4]:
            parts.append(f"  • {s.name} ({s.code}){_pf(s)} — {s.score}/{MAX_SCORE}점")

    if improved:
        parts.append("\n⬆️ <b>점수 상승</b>")
        for s, prev in improved[:4]:
            diff = s.score - prev
            parts.append(
                f"  • {s.name}{_pf(s)} — {prev}→{s.score}점 (<b>+{diff}</b>)"
            )

    if declined:
        parts.append("\n⬇️ <b>점수 하락</b>")
        for s, prev in declined[:3]:
            diff = prev - s.score
            parts.append(f"  • {s.name}{_pf(s)} — {prev}→{s.score}점 (-{diff})")

    if exited:
        parts.append("\n🔕 <b>레이더 이탈</b>")
        for code, name, prev_score in exited[:3]:
            parts.append(f"  • {name} ({code}) — {prev_score}점 → 조건 미충족")

    return "\n".join(parts)


# ── 메인 실행 ─────────────────────────────────────────────────────────────────

def run(is_initial: bool, scan_label: str) -> None:
    if not is_trading_day():
        logger.info("오늘은 거래일 아님 — 스킵")
        return

    portfolio = _load_portfolio()
    portfolio_codes = set(portfolio.keys())
    kis = KISClient()

    # 유니버스 구성
    codes = _fetch_universe_codes(kis, portfolio_codes)
    if not codes:
        logger.warning("스캔 대상 종목 없음 — 종료")
        return

    logger.info(f"[{scan_label}] {len(codes)}종목 스캔 시작 (initial={is_initial})")

    # 스캔 실행
    results = scan(
        codes=codes,
        name_map=portfolio,
        min_score=MIN_SCORE,
        portfolio_codes=portfolio_codes,
    )

    if is_initial:
        # 초기 스캔: 전체 현황 전송
        msg = _format_initial(results, scan_label)
        ok = send(msg)
        logger.info(f"초기 알림 전송: {'성공' if ok else '실패'}")
    else:
        # 델타 스캔: 변화 있을 때만 전송
        prev_state = _load_state()
        prev_stocks = prev_state.get("stocks", {})

        new_entries, improved, declined, exited = _compute_delta(prev_stocks, results)
        msg = _format_delta(new_entries, improved, declined, exited, scan_label)

        if msg:
            ok = send(msg)
            logger.info(
                f"델타 알림 전송: {'성공' if ok else '실패'} "
                f"(신규 {len(new_entries)}, 상승 {len(improved)}, "
                f"하락 {len(declined)}, 이탈 {len(exited)})"
            )
        else:
            logger.info(f"[{scan_label}] 변화 없음 — 알림 생략")

    # 상태 저장 (초기/델타 공통)
    _save_state(results, scan_label, is_initial)


def main() -> None:
    parser = argparse.ArgumentParser(description="장중 주도주 실시간 모니터")
    parser.add_argument(
        "--initial", action="store_true",
        help="초기 스캔 모드 (장 시작, 상태 리셋)",
    )
    parser.add_argument(
        "--label", default="장중",
        help="스캔 레이블 (예: 09:10 / 10:30 / 13:30 / 15:00)",
    )
    args = parser.parse_args()
    run(is_initial=args.initial, scan_label=args.label)


if __name__ == "__main__":
    main()
