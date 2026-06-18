#!/usr/bin/env python3
"""미국 증시 마감 블록 단독 출력 (시장 요약 + 섹터 성과 + 주요 종목 10선).

Hermes 통합 모닝 브리핑(cron 52f01a1aaab2)이 이 stdout(HTML)을 그대로
임베드한다. morning_briefing.py의 결정론(yfinance) 함수를 재사용해
LLM 재현 없이 정확한 수치를 제공한다.

실행: <jarvis venv>/python3 scripts/us_market_block.py
출력: Telegram HTML(<b>, <pre>) — Hermes가 HTML 자동 감지로 렌더.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from morning_briefing import get_market_summary, get_us_data_block


def main() -> None:
    blocks = [get_market_summary()]
    us_block, _ctx = get_us_data_block()
    blocks.append(us_block)
    print("\n\n".join(blocks))


if __name__ == "__main__":
    main()
