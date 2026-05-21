"""Raphael stock analysis payload builder for Anthropic."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from core.config import RAPHAEL_CLAUDE_SKILL_DIR, RAPHAEL_INSTRUCTION_DIR

TRACK_RULES = {
    "track1": {"max_news": 3, "max_dart": 2, "max_consensus": 0},
    "track2": {"max_news": 5, "max_dart": 3, "max_consensus": 3},
    "track3": {"max_news": 5, "max_dart": 5, "max_consensus": 3},
}


class RaphaelPayloadError(ValueError):
    """Raised when the request payload is invalid."""


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _instruction_path(filename: str) -> Path:
    return Path(RAPHAEL_INSTRUCTION_DIR) / filename


def _skill_path(filename: str) -> Path:
    return Path(RAPHAEL_CLAUDE_SKILL_DIR) / filename


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RaphaelPayloadError(message)


def _limit_items(items: Any, max_items: int) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return items[:max_items]


def validate_and_normalize_request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(request_data)

    _require(isinstance(payload, dict), "request_data는 dict여야 합니다.")
    _require(isinstance(payload.get("request"), dict), "request 블록이 필요합니다.")
    _require(isinstance(payload.get("instrument"), dict), "instrument 블록이 필요합니다.")
    _require(isinstance(payload.get("market_data"), dict), "market_data 블록이 필요합니다.")
    _require(isinstance(payload.get("compressed_context"), dict), "compressed_context 블록이 필요합니다.")

    track = payload["request"].get("track")
    _require(track in TRACK_RULES, "request.track은 track1, track2, track3 중 하나여야 합니다.")

    code = str(payload["instrument"].get("code", ""))
    market = payload["instrument"].get("market")
    _require(code.isdigit() and len(code) == 6, "instrument.code는 6자리 숫자여야 합니다.")
    _require(market in ("KOSPI", "KOSDAQ"), "instrument.market은 KOSPI 또는 KOSDAQ이어야 합니다.")

    rules = TRACK_RULES[track]
    context = payload["compressed_context"]
    context["news_events"] = _limit_items(context.get("news_events"), rules["max_news"])
    context["dart_events"] = _limit_items(context.get("dart_events"), rules["max_dart"])
    context["consensus_changes"] = _limit_items(context.get("consensus_changes"), rules["max_consensus"])

    if track == "track1":
        context["quant_snapshot"] = {}
    else:
        context["quant_snapshot"] = context.get("quant_snapshot") or {}

    return payload


def build_raphael_payload(
    request_data: Dict[str, Any],
    model: str,
    max_tokens: int = 4000,
    system_prompt: str | None = None,
) -> Dict[str, Any]:
    normalized = validate_and_normalize_request(request_data)

    system_prompt = system_prompt or (
        "너는 한국 주식 분석 엔진이다. "
        "KIS API와 DART 기반 숫자를 우선 사용하고, 추정치는 [ASSUMED]로 표시한다."
    )

    skill_text = _read_text(_instruction_path("raphael-stock-analysis SKILL.md"))
    framework_text = _read_text(_skill_path("ANALYSIS-FRAMEWORK.md"))
    template_text = _read_text(_instruction_path("raphael-stock-analysis-prompt-caching-template.md"))

    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {"type": "text", "text": system_prompt},
            {"type": "text", "text": skill_text},
            {"type": "text", "text": framework_text},
            {
                "type": "text",
                "text": template_text,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(normalized, ensure_ascii=False, indent=2),
                    }
                ],
            }
        ],
    }
