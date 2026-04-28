# bot.py
"""Telegram 봇 — 종목 추가/삭제/조회 명령어 처리 (별도 프로세스, polling 방식)"""

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from core import portfolio, notifier
from core.bot_handlers import register_handlers
from core.config import LOG_DIR, JARVIS_CHAT_ID

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

ALLOWED_CHAT_ID = JARVIS_CHAT_ID


# 1. 타이핑 상태를 유지하는 백그라운드 함수
async def keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """답변이 올 때까지 4.5초마다 타이핑 액션을 지속적으로 전송합니다."""
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4.5)  # 텔레그램 타이핑 만료(5초) 전 갱신
    except asyncio.CancelledError:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text

    # 타이핑 루프 시작 ('점 세 개' 표시 시작)
    typing_task = asyncio.create_task(keep_typing(context, chat_id))

    try:
        # 비동기로 Hermes CLI 호출 (봇이 얼어붙는 것을 방지)
        process = await asyncio.create_subprocess_exec(
            "hermes", "chat", "-q", user_text, "-Q",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 60초 타임아웃 적용하여 대기
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)

            # 응답 텍스트 디코딩
            response = stdout.decode().strip() if stdout else ""
            if not response:
                response = stderr.decode().strip() if stderr else "응답 내용이 없습니다."

        except asyncio.TimeoutError:
            process.kill()
            response = "⏳ 응답 시간이 초과되었습니다 (60초)."

        # 답변이 준비되었으므로 타이핑 루프 종료
        typing_task.cancel()

        # 텔레그램으로 최종 결과 전송
        await update.message.reply_text(response[:4000])

    except Exception as e:
        typing_task.cancel()
        logger.error(f"Hermes Agent 호출 오류: {e}")
        await update.message.reply_text("⚠️ AI 호출 중 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")


def main() -> None:
    token = os.environ.get("JARVIS_BOT_TOKEN") or JARVIS_BOT_TOKEN
    app = Application.builder().token(token).build()

    # 공통 핸들러 등록 (core/bot_handlers)
    register_handlers(app, ALLOWED_CHAT_ID)

    # 일반 메시지 핸들러 (Hermes AI 응답)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Jarvis 봇 시작 — /add /remove /list /p /일반메시지 대기 중")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
