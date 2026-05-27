# news_bot.py
"""뉴스 발송 모듈 — 텔레그램으로 뉴스 브리핑 전송"""

import logging
from datetime import datetime

import feedparser
import requests

from core.config import JARVIS_BOT_TOKEN, JARVIS_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

FEEDS = {
    "World News": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko",
    "Iran News": "https://news.google.com/rss/search?q=Iran&hl=ko&gl=KR&ceid=KR:ko",
    "Business/Economy": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko",
    "Korean News": "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko",
}


def fetch_news() -> str:
    """RSS 피드에서 뉴스 수집"""
    parts = []
    for cat, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
            header = f"{cat}\n\n"
            items = []
            for entry in feed.entries[:3]:
                items.append(f"• {entry.title}")
            parts.append(header + "\n".join(items))
        except Exception as e:
            logger.warning(f"뉴스 피드 오류 ({cat}): {e}")
            parts.append(header + "오류 발생")
    return "\n\n".join(parts)


def send_news() -> bool:
    """뉴스 브리핑을 텔레그램으로 전송"""
    token = JARVIS_BOT_TOKEN or ""
    chat_id = JARVIS_CHAT_ID or ""

    if not token or not chat_id:
        logger.warning("텔레그램 토큰 또는 CHAT_ID가 설정되지 않음")
        return False

    now = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    text = f"📰 뉴스 브리핑\n{now}\n\n{fetch_news()}"

    try:
        resp = requests.post(
            TELEGRAM_URL.format(token=token),
            json={"chat_id": str(chat_id), "text": text},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("뉴스 브리핑 전송 완료")
        return True
    except Exception as e:
        logger.error(f"뉴스 브리핑 전송 실패: {e}")
        return False


def send_daily_briefing() -> None:
    """아침 브리핑 전송 (scheduler에서 호출)"""
    logger.info("아침 뉴스 브리핑 전송")
    send_news()
