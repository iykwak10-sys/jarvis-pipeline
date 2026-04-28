import os
import httpx
from typing import Optional, List, Dict

# 정확한 오픈라우터 엔드포인트로 고정합니다.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = os.getenv("OPENROUTER_API_KEY")

async def ai_chat(prompt: str, model: str = "openai/gpt-5-nano", history: Optional[List[Dict]] = None) -> str:
    if not API_KEY:
        return "에러: OPENROUTER_API_KEY가 설정되지 않았습니다."

    messages = history or []
    messages.append({"role": "user", "content": prompt})
    
    payload = {"model": model, "messages": messages}
    headers = {
        "Authorization": f"Bearer {API_KEY}",
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
