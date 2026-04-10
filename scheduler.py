# scheduler.py
"""Jarvis 마켓 스케줄러 — 모든 자동화 작업 오케스트레이션"""

import atexit
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import schedule
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from core import notifier

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
PID_FILE = LOG_DIR / "scheduler.pid"
BOT_PID_FILE = LOG_DIR / "bot.pid"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "scheduler.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ── 단일 인스턴스 보장 ────────────────────────────────────────────────────────
def acquire_pid_lock() -> None:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            logger.warning(f"스케줄러 이미 실행 중 (PID {old_pid}) — 종료")
            sys.exit(0)
        except (ProcessLookupError, ValueError, OSError):
            pass
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))


# ── 봇 프로세스 관리 ──────────────────────────────────────────────────────────
def start_bot() -> None:
    """bot.py를 백그라운드 프로세스로 시작"""
    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "bot.py")],
        stdout=open(LOG_DIR / "bot.log", "a"),
        stderr=subprocess.STDOUT,
    )
    BOT_PID_FILE.write_text(str(proc.pid))
    logger.info(f"Telegram 봇 시작 (PID {proc.pid})")
    atexit.register(lambda: proc.terminate())


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def is_weekday() -> bool:
    return datetime.now().weekday() < 5


def run_script(script: str, *args: str) -> None:
    """스크립트를 subprocess로 실행"""
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / script), *args],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            logger.info(f"✅ {script} 완료")
        else:
            logger.error(f"❌ {script} 실패:\n{result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        logger.error(f"❌ {script} 타임아웃 (120초)")
    except Exception as e:
        logger.error(f"❌ {script} 예외: {e}")


# ── 스케줄 작업 ───────────────────────────────────────────────────────────────
def job_realtime() -> None:
    if not is_weekday():
        return
    now = datetime.now()
    if now.replace(hour=9, minute=0, second=0, microsecond=0) <= now <= \
       now.replace(hour=15, minute=30, second=0, microsecond=0):
        logger.info("⚡ 장중 실시간 수집")
        run_script("collector.py")


def job_closing() -> None:
    if not is_weekday():
        logger.info("⏭️ 주말 — 마감 수집 스킵")
        return
    logger.info("🇰🇷 한국 장 마감 수집")
    run_script("collector.py", "--closing")


def job_price_alert() -> None:
    if not is_weekday():
        return
    logger.info("⚡ 급등락 알림 체크")
    run_script("price_alert.py")


def job_us_alert() -> None:
    if not is_weekday():
        return
    logger.info("🇺🇸 미국 장 마감 알림")
    notifier.send_us_market_alert()


def job_health_check() -> None:
    logger.info(f"💚 Health Check OK — {datetime.now().strftime('%Y-%m-%d %H:%M')}")


# ── 스케줄 등록 및 메인 루프 ─────────────────────────────────────────────────
def main() -> None:
    acquire_pid_lock()
    start_bot()

    schedule.every(5).minutes.do(job_realtime)
    schedule.every().day.at("06:05").do(job_us_alert)
    schedule.every().day.at("09:00").do(job_health_check)
    schedule.every().day.at("10:00").do(job_price_alert)
    schedule.every().day.at("13:00").do(job_price_alert)
    schedule.every().day.at("14:30").do(job_price_alert)
    schedule.every().day.at("15:35").do(job_closing)

    logger.info("🚀 Jarvis 스케줄러 시작")
    logger.info("  06:05 미국장 마감 알림 | 09:00 헬스체크")
    logger.info("  10:00/13:00/14:30 급등락 알림 | 15:35 마감 수집")
    logger.info("  5분 간격 장중 실시간 수집")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
