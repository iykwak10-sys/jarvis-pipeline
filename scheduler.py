# scheduler.py
"""Jarvis 마켓 스케줄러 — 모든 자동화 작업 오케스트레이션"""

import atexit
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import holidays
import schedule

from core import notifier
from core.config import LOG_DIR

KR_HOLIDAYS = holidays.KR()

BASE_DIR = Path(__file__).parent
PID_FILE = LOG_DIR / "scheduler.pid"
BOT_PID_FILE = LOG_DIR / "bot.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "scheduler.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
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
def _terminate_stale_bot() -> None:
    """이전 스케줄러가 남긴 bot.py 인스턴스를 정리.

    launchd keepalive 재시작 시 고아 bot.py가 살아남아 같은 토큰으로 polling하면
    Telegram이 'Conflict: terminated by other getUpdates request'를 반복 발생시킨다.
    새 봇을 띄우기 전 BOT_PID_FILE에 기록된 기존 인스턴스를 종료해 단일 폴러를 보장한다.
    """
    if not BOT_PID_FILE.exists():
        return
    try:
        old_pid = int(BOT_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        BOT_PID_FILE.unlink(missing_ok=True)
        return
    try:
        os.kill(old_pid, 0)  # 살아있는지 확인
    except OSError:
        BOT_PID_FILE.unlink(missing_ok=True)
        return
    # PID 재사용 방지 — 실제 우리 bot.py인지 커맨드라인으로 확인
    try:
        cmd = subprocess.run(
            ["ps", "-p", str(old_pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        cmd = ""
    if "bot.py" not in cmd:
        BOT_PID_FILE.unlink(missing_ok=True)
        return
    logger.warning(f"기존 Telegram 봇(PID {old_pid}) 종료 — Conflict 방지")
    try:
        os.kill(old_pid, signal.SIGTERM)
        for _ in range(10):  # 최대 5초 대기
            time.sleep(0.5)
            try:
                os.kill(old_pid, 0)
            except OSError:
                break
        else:
            os.kill(old_pid, signal.SIGKILL)  # 안 죽으면 강제 종료
    except OSError:
        pass
    BOT_PID_FILE.unlink(missing_ok=True)


def start_bot() -> None:
    """bot.py를 백그라운드 프로세스로 시작 (기존 인스턴스 정리 후)"""
    _terminate_stale_bot()
    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "bot.py")],
        stdout=open(LOG_DIR / "bot.log", "a"),
        stderr=subprocess.STDOUT,
    )
    BOT_PID_FILE.write_text(str(proc.pid))
    logger.info(f"Telegram 봇 시작 (PID {proc.pid})")
    atexit.register(lambda: proc.terminate())


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def is_trading_day() -> bool:
    """한국 증시 개장일 여부 (주말 + 공휴일 제외)"""
    today = datetime.now().date()
    if today.weekday() >= 5:
        return False
    if today in KR_HOLIDAYS:
        logger.info(f"⏭️ 오늘은 공휴일({KR_HOLIDAYS.get(today)}) — 스킵")
        return False
    return True


# 하위 호환성 유지 (기존 코드 참조용)
is_weekday = is_trading_day


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


def job_morning_briefing() -> None:
    if not is_weekday():
        return
    logger.info("🌅 아침 브리핑 실행")
    run_script("morning_briefing.py")


def job_news_briefing() -> None:
    """아침 뉴스 브리핑 전송"""
    logger.info("📰 아침 뉴스 브리핑 전송")
    run_script("news_bot.py")


def job_us_alert() -> None:
    if not is_weekday():
        return
    logger.info("🇺🇸 미국 장 마감 알림")
    notifier.send_us_market_alert()


def job_intraday_monitor(label: str, initial: bool = False) -> None:
    """장중 주도주 델타 알림 — 09:10(초기) / 10:30 / 13:30 / 15:00"""
    if not is_weekday():
        return
    logger.info(f"⚡ 장중 주도주 모니터 [{label}] {'(초기)' if initial else ''}")
    args = ["--label", label]
    if initial:
        args = ["--initial"] + args
    run_script("intraday_monitor.py", *args)


def job_health_check() -> None:
    logger.info(f"💚 Health Check OK — {datetime.now().strftime('%Y-%m-%d %H:%M')}")


def job_schedule_planner() -> None:
    """일정 브리핑 플래너 — 30분마다 실행 (종일)"""
    logger.info("📅 일정 브리핑 플래너 실행")
    run_script("schedule_briefing/planner.py")


def job_schedule_dispatcher() -> None:
    """일정 브리핑 디스패처 — 1분마다 실행 (종일)"""
    pending_check = Path(__file__).parent / "data" / "schedule_alerts.json"
    if not pending_check.exists():
        return
    run_script("schedule_briefing/dispatcher.py")


def job_tomorrow_briefing() -> None:
    """내일 사전 브리핑 — 매일 밤 21시"""
    logger.info("🌙 내일 사전 브리핑 실행")
    run_script("schedule_briefing/planner.py", "--mode", "tomorrow")


# ── 스케줄 등록 및 메인 루프 ─────────────────────────────────────────────────
def main() -> None:
    acquire_pid_lock()
    start_bot()

    schedule.every(5).minutes.do(job_realtime)
    schedule.every().day.at("06:30").do(job_morning_briefing)
    schedule.every().day.at("07:00").do(job_news_briefing)
    schedule.every().day.at("06:05").do(job_us_alert)
    schedule.every().day.at("09:00").do(job_health_check)
    schedule.every().day.at("10:00").do(job_price_alert)
    schedule.every().day.at("13:00").do(job_price_alert)
    schedule.every().day.at("14:30").do(job_price_alert)
    schedule.every().day.at("15:35").do(job_closing)

    # Phase 3 — 장중 주도주 실시간 모니터 (4회)
    schedule.every().day.at("09:10").do(job_intraday_monitor, label="09:10", initial=True)
    schedule.every().day.at("10:30").do(job_intraday_monitor, label="10:30")
    schedule.every().day.at("13:30").do(job_intraday_monitor, label="13:30")
    schedule.every().day.at("15:00").do(job_intraday_monitor, label="15:00")

    # Phase 4 — 능동적 일정 브리핑 (종일 상시)
    schedule.every(30).minutes.do(job_schedule_planner)   # 30분마다 다음 일정 계획
    schedule.every(1).minutes.do(job_schedule_dispatcher) # 1분마다 발송 시각 확인

    # Phase 5 — 내일 사전 브리핑 (매일 밤 22시)
    schedule.every().day.at("22:00").do(job_tomorrow_briefing)

    logger.info("🚀 Jarvis 스케줄러 시작")
    logger.info("  06:30 아침 브리핑 | 06:05 미국장 마감 알림 | 07:00 뉴스 브리핑 | 09:00 헬스체크")
    logger.info("  10:00/13:00/14:30 급등락 알림 | 15:35 마감 수집")
    logger.info("  5분 간격 장중 실시간 수집")
    logger.info("  ⚡ 장중 주도주 모니터: 09:10(초기) / 10:30 / 13:30 / 15:00")
    logger.info("  📅 일정 브리핑: 30분마다 플래너 | 1분마다 디스패처 | 22:00 내일 사전 브리핑")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
