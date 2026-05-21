import json
import httpx
from typing import Optional, List, Dict, Any

from core.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)
from core.raphael_analysis import build_raphael_payload

# 정확한 오픈라우터 엔드포인트로 고정합니다.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

async def ai_chat(prompt: str, model: str = "openai/gpt-5-nano", history: Optional[List[Dict]] = None) -> str:
    api_key = OPENROUTER_API_KEY
    if not api_key:
        return "에러: OPENROUTER_API_KEY가 설정되지 않았습니다."

    messages = history or []
    messages.append({"role": "user", "content": prompt})
    
    payload = {"model": model, "messages": messages}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/raphael/jarvis", # 임의의 리퍼러
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            # 만약 JSON이 아니면 여기서 에러 내용을 텍스트로 찍어줍니다.
            if resp.status_code != 200:
                return f"AI 서버 응답 오류 ({resp.status_code}): {resp.text[:100]}"
            
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"통신 장애 발생: {str(e)}"


async def anthropic_messages(payload: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
    """Anthropic Messages API 호출."""
    effective_key = api_key or ANTHROPIC_API_KEY
    if not effective_key:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    headers = {
        "x-api-key": effective_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        resp = await client.post(ANTHROPIC_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def extract_anthropic_text(response_data: Dict[str, Any]) -> str:
    """Anthropic 응답에서 텍스트 컨텐츠만 추출."""
    texts: List[str] = []
    for block in response_data.get("content", []):
        if block.get("type") == "text" and block.get("text"):
            texts.append(block["text"])
    if texts:
        return "\n".join(texts).strip()
    return json.dumps(response_data, ensure_ascii=False, indent=2)


async def run_raphael_stock_analysis(
    request_data: Dict[str, Any],
    model: Optional[str] = None,
    max_tokens: int = 4000,
) -> str:
    """Raphael stock analysis payload 생성 + Anthropic 호출."""
    payload = build_raphael_payload(
        request_data=request_data,
        model=model or ANTHROPIC_MODEL,
        max_tokens=max_tokens,
    )
    response_data = await anthropic_messages(payload)
    return extract_anthropic_text(response_data)
