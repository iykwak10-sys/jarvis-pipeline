# bot.py
"""Telegram 봇 — 종목 추가/삭제/조회 명령어 처리 (별도 프로세스, polling 방식)"""

import asyncio
import atexit
import fcntl
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

# ── 파일 기반 잠금 (중복 인스턴스 방지) ──
LOCK_PATH = "/tmp/jarvis_bot.lock"
_lock_fd = None

def acquire_bot_lock():
    global _lock_fd
    if _lock_fd is not None:
        return _lock_fd
    try:
        fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        _lock_fd = fd
        return fd
    except OSError:
        return None

def release_bot_lock():
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.lockf(_lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(_lock_fd)
        except Exception:
            pass
        _lock_fd = None
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


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
    logger.info(f"메시지 수신: chat_id={chat_id}, text={user_text[:50]}...")

    # 타이핑 루프 시작 ('점 세 개' 표시 시작)
    typing_task = asyncio.create_task(keep_typing(context, chat_id))

    # Hermes CLI 호출 및 응답 처리
    response = None
    try:
        process = await asyncio.create_subprocess_exec(
            "/Users/kwaksmacmini/.local/bin/hermes", "chat",
            "-q", user_text, "-Q",
            "-m", "deepseek-v4-flash",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 최대 180초 대기
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180.0)
        response = stdout.decode().strip() if stdout else ""
        if not response:
            response = stderr.decode().strip() if stderr else "응답 내용이 없습니다."
        logger.info(f"Hermes 응답: {response[:100]}...")

    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        response = "⏳ 응답 시간이 초과되었습니다 (180초). 다시 시도해주세요."
        logger.warning("Hermes 호출 타임아웃 (180초)")

    except Exception as e:
        try:
            process.kill()
        except Exception:
            pass
        response = "⚠️ AI 호출 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        logger.error(f"Hermes Agent 호출 오류: {e}")

    finally:
        typing_task.cancel()
        if response:
            await update.message.reply_text(response[:4000])


def main() -> None:
    # ── 단일 인스턴스 잠금 획득 ──
    lock_fd = acquire_bot_lock()
    if lock_fd is None:
        logger.error("Jarvis bot is already running on this host. Stop the other instance and retry.")
        raise SystemExit("Jarvis bot is already running. Exiting.")
    atexit.register(release_bot_lock)

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
