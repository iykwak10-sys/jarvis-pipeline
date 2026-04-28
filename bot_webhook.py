# bot_webhook.py
"""Telegram bot webhook listener for Jarvis (no polling, single-instance)"""

import logging
import os
from pathlib import Path
import atexit
import fcntl

from telegram import Update
from telegram.ext import Application, ContextTypes

from core import portfolio, notifier
from core.bot_handlers import register_handlers
from core.kis_client import KISClient
from core.config import LOG_DIR, JARVIS_CHAT_ID, JARVIS_BOT_TOKEN

LOCK_PATH = "/tmp/jarvis_bot.lock"
_lock_fd = None


def acquire_lock():
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


def release_lock():
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


# Load environment variables from config
LOGGING_DIR = LOG_DIR
LOGGING_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot_webhook.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

ALLOWED_CHAT_ID = JARVIS_CHAT_ID

# Start with single-instance lock to avoid multi-bot collisions
lock_fd = acquire_lock()
if lock_fd is None:
    logger.error("Jarvis 봇이 이미 실행 중입니다. 다른 인스턴스를 종료하고 재시작해주세요.")
    raise SystemExit(1)
atexit.register(release_lock)


def main() -> None:
    token = JARVIS_BOT_TOKEN
    app = Application.builder().token(token).build()

    # 공통 핸들러 등록 (core/bot_handlers)
    register_handlers(app, ALLOWED_CHAT_ID)

    logger.info("Jarvis webhook bot 시작 — /add /remove /list /p 대기 중")
    port = int(os.environ.get("PORT", 8443))
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        logger.error("WEBHOOK_URL이 설정되지 않았습니다. 공개 URL을 설정해 주세요.")
        return
    app.run_webhook(
        listen="0.0.0.0", port=port,
        url_path=token,
        webhook_url=webhook_url.rstrip('/') + "/" + token,
    )


if __name__ == "__main__":
    main()
