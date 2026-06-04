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

# ── 위치 캐시 연동 ──
from schedule_briefing.location_cache import save_location, get_current_location

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

# httpx/urllib3 라이브러리 로그 침묵 (중복 로그 방지)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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


# ── update_id 기반 중복 업데이트 방지 ──
_processed_updates = set()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    # update_id 기반 중복 제거
    if hasattr(update, 'update_id') and update.update_id:
        if update.update_id in _processed_updates:
            logger.warning(f"중복 업데이트 차단: update_id={update.update_id}")
            return
        _processed_updates.add(update.update_id)

    chat_id = update.effective_chat.id
    user_text = update.message.text
    logger.info(f"메시지 수신: chat_id={chat_id}, update_id={update.update_id}, text={user_text[:50]}...")

    # 타이핑 루프 시작 ('점 세 개' 표시 시작)
    typing_task = asyncio.create_task(keep_typing(context, chat_id))

    response = None
    try:
        import subprocess

        def _call_hermes(model: str) -> str:
            proc = subprocess.run(
                ["/Users/kwaksmacmini/.local/bin/hermes", "chat",
                 "-q", user_text, "-m", model, "-Q"],
                capture_output=True, text=True, timeout=180,
            )
            return proc.stdout.strip()

        primary_model = "deepseek/deepseek-chat-v4-pro-v2"
        fallback_model = "deepseek/deepseek-v4-flash"
        raw = _call_hermes(primary_model)
        if not raw:
            logger.warning(f"⚠️ {primary_model} 응답 없음 → {fallback_model} 롤백")
            raw = _call_hermes(fallback_model)

        # Hermes CLI 불필요 출력 제거
        import re
        lines = raw.split('\n')
        cleaned_lines = []
        for line in lines:
            s = line.strip()
            # 준비과정 제거: "┊ 💻 preparing terminal…" / "┊ 📖 preparing read_file…"
            if re.match(r'^┊\s', s):
                continue
            # 배너 프레임 시작/끝 라인 제거
            if re.match(r'^[╭╰╮╯─]', s):
                continue
            # session_id 라인 제거
            if s.startswith('session_id:'):
                continue
            cleaned_lines.append(line)
        # 빈 줄 2개 이상을 1개로 압축
        cleaned = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned_lines)).strip()
        # 최종 응답이 중복될 경우 첫 번째만 사용 (중복 제거)
        # 여러 단락이 동일하면 하나로 합침
        paragraphs = [p.strip() for p in re.split(r'\n\n+', cleaned) if p.strip()]
        unique_paragraphs = []
        for p in paragraphs:
            if p not in unique_paragraphs:
                unique_paragraphs.append(p)
        response = '\n\n'.join(unique_paragraphs) if unique_paragraphs else raw.strip()
        logger.info(f"Hermes 응답: {response[:100]}...")

    except subprocess.TimeoutExpired:
        logger.warning(f"{primary_model} 타임아웃 → {fallback_model} 롤백")
        try:
            raw = _call_hermes(fallback_model)
            logger.info(f"롤백 응답: {raw[:100] if raw else '없음'}...")
        except Exception as fe:
            logger.error(f"롤백 모델도 실패: {fe}")
            raw = None

        if raw:
            # rollback 성공시에도 동일 정제 로직 적용
            lines = raw.split('\n')
            cleaned_lines = []
            for line in lines:
                s = line.strip()
                if re.match(r'^┊\s', s): continue
                if re.match(r'^[╭╰╮╯─]', s): continue
                if s.startswith('session_id:'): continue
                cleaned_lines.append(line)
            cleaned = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned_lines)).strip()
            paragraphs = [p.strip() for p in re.split(r'\n\n+', cleaned) if p.strip()]
            unique_paragraphs = []
            for p in paragraphs:
                if p not in unique_paragraphs:
                    unique_paragraphs.append(p)
            response = '\n\n'.join(unique_paragraphs)
        else:
            response = "⏳ 백업 모델도 응답 실패. 잠시 후 다시 시도해주세요."

    except Exception as e:
        logger.error(f"Hermes 호출 오류: {e}")

    finally:
        typing_task.cancel()
        if response:
            await update.message.reply_text(response[:4000])


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """iOS 단축어 등에서 전송된 실시간 위치 저장"""
    loc = update.message.location
    lat = loc.latitude
    lng = loc.longitude
    chat_id = update.effective_chat.id

    logger.info(f"📍 위치 수신: chat_id={chat_id}, ({lat:.5f}, {lng:.5f})")
    try:
        save_location(lat, lng, source="telegram")
        await update.message.reply_text(
            f"📍 위치 저장 완료\n"
            f"위도: {lat:.5f}\n"
            f"경도: {lng:.5f}\n"
            f"일정 브리핑에 반영됩니다."
        )
        logger.info(f"위치 저장 완료: ({lat:.5f}, {lng:.5f})")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 위치 저장 실패: {e}")
        logger.error(f"위치 저장 오류: {e}")


# 운영 머신 LocalHostName. 빈 문자열이면 가드 비활성화.
# 활성화하려면 예: "kwak-mac-mini" — `scutil --get LocalHostName` 결과와 동일하게.
OPERATING_HOST = ""


def _host_guard() -> None:
    """봇은 OPERATING_HOST와 일치하는 머신에서만 폴링한다.

    같은 토큰을 두 머신이 동시에 폴링하면 Telegram이 Conflict를 영구 발생시킨다.
    JARVIS_OPERATING_HOST 환경변수 또는 OPERATING_HOST 상수로 운영 머신을 지정.
    """
    import subprocess as _sp
    expected = os.environ.get("JARVIS_OPERATING_HOST", OPERATING_HOST).strip()
    if not expected:
        return
    try:
        actual = _sp.check_output(
            ["scutil", "--get", "LocalHostName"], text=True, timeout=2
        ).strip()
    except Exception:
        actual = os.uname().nodename.split(".")[0]
    if actual != expected:
        logger.error(
            "Host guard: this is '%s' but JARVIS_OPERATING_HOST='%s'. Refusing to start.",
            actual, expected,
        )
        raise SystemExit(2)


def main() -> None:
    # ── 머신 가드 (잘못된 기기에서 폴링 방지) ──
    _host_guard()

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

    # 일반 메시지 핸들러 (Hermes AI 응답) — 봇 자신의 메시지는 무시
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE,
        handle_message
    ))

    # 위치 메시지 핸들러 (iOS 단축어 연동)
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    logger.info("Jarvis 봇 시작 — /add /remove /list /p /일반메시지 대기 중")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
