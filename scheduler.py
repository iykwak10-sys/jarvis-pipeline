"""
scheduler.py
────────────────────────────────────────────────────────
월~금 자동 실행 스케줄러
  - 15:35 KST : 한국 장 마감 → KIS 데이터 수집
  - 06:05 KST : 미국 장 마감 → 미국 데이터 수집 트리거
  - 10:00 KST : 장중 가격 알림 체크 (±5% 급등/급락)
  - 13:00 KST : 장중 가격 알림 체크
  - 14:30 KST : 장중 가격 알림 체크 (마감 전)

실행: python scheduler.py
백그라운드: nohup python scheduler.py > ~/jarvis-pipeline/logs/scheduler.log 2>&1 &
────────────────────────────────────────────────────────
"""

import os
import json
import subprocess
import logging
import sys
import atexit
import signal
from datetime import datetime
from pathlib import Path

import schedule
import time

# pip install schedule requests
# 환경변수 로딩: pip install python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PIPELINE_DIR = Path(__file__).parent


def is_weekday() -> bool:
    """월~금 여부 확인"""
    return datetime.now().weekday() < 5


def is_market_holiday(date_str: str) -> bool:
    """
    간단한 공휴일 체크 (필요시 한국 공휴일 API 연동)
    현재는 주말만 체크, 추후 확장 가능
    """
    return False  # TODO: 공휴일 API 연동


def run_korea_closing():
    """15:35 KST — 한국 장 마감 데이터 수집"""
    if not is_weekday():
        logger.info("⏭️ 주말 — 한국 마감 스킵")
        return

    today = datetime.now().strftime("%Y%m%d")
    logger.info(f"🇰🇷 한국 장 마감 수집 시작: {today}")

    try:
        result = subprocess.run(
            ["python", str(PIPELINE_DIR / "kis_collector.py"), "--date", today, "--mode", "korea"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info("✅ 한국 마감 수집 완료")
            logger.debug(result.stdout)
        else:
            logger.error(f"❌ 한국 마감 수집 실패:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("❌ 한국 마감 수집 타임아웃 (120초)")
    except Exception as e:
        logger.error(f"❌ 예외 발생: {e}")


def run_price_alert():
    """10:00 / 13:00 / 14:30 KST — 장중 급등/급락 알림 (±5%)"""
    if not is_weekday():
        logger.info("⏭️ 주말 — 가격 알림 스킵")
        return

    now = datetime.now()
    logger.info(f"⚡ 장중 가격 알림 체크: {now.strftime('%H:%M')}")

    try:
        result = subprocess.run(
            ["python3", str(PIPELINE_DIR / "price_alert.py"), "--threshold", "5.0"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info("✅ 가격 알림 체크 완료")
            logger.debug(result.stdout)
        else:
            logger.error(f"❌ 가격 알림 실패:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("❌ 가격 알림 타임아웃 (120초)")
    except Exception as e:
        logger.error(f"❌ 가격 알림 예외: {e}")


def run_us_closing():
    """06:05 KST — 미국 장 마감 (전일 날짜)"""
    if not is_weekday():
        logger.info("⏭️ 주말 — 미국 마감 스킵")
        return

    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    logger.info(f"🇺🇸 미국 장 마감 트리거: {yesterday}")

    try:
        # 미국 데이터는 웹 검색 기반이므로 별도 수집기 없이
        # Claude Cowork 스킬에 트리거 신호만 전송 (텔레그램)
        import sys
        sys.path.insert(0, str(PIPELINE_DIR))
        from kis_collector import TelegramNotifier
        notifier = TelegramNotifier(
            os.environ.get("JARVIS_BOT_TOKEN", ""),
            "8663369518"
        )
        notifier.send(
            "🇺🇸 <b>미국 장 마감 알림</b>\n"
            f"📅 {yesterday}\n"
            "📊 Claude Cowork에서 us-stock-analyst 스킬을 실행하세요.\n"
            "또는 '미국 마감 분석해줘'라고 입력하세요."
        )
        logger.info("✅ 미국 마감 알림 전송 완료")
    except Exception as e:
        logger.error(f"❌ 미국 마감 알림 실패: {e}")


def run_health_check():
    """매일 09:00 — 스케줄러 정상 작동 확인"""
    logger.info(f"💚 Health Check — 스케줄러 정상 작동 중 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")


# ──────────────────────────────────────────────
# 스케줄 등록
# ──────────────────────────────────────────────
def setup_schedule():
    """스케줄 등록"""
    
    # 한국 장 마감 (15:35 KST)
    schedule.every().day.at("15:35").do(run_korea_closing)

    # 미국 장 마감 (06:05 KST, 다음날 새벽)
    schedule.every().day.at("06:05").do(run_us_closing)

    # 장중 가격 알림 (10:00 / 13:00 / 14:30 KST)
    schedule.every().day.at("10:00").do(run_price_alert)
    schedule.every().day.at("13:00").do(run_price_alert)
    schedule.every().day.at("14:30").do(run_price_alert)

    # 장중 실시간 수집 (5분 간격, 거래시간 중)
    schedule.every(5).minutes.do(run_korea_realtime)

    # 헬스체크 (09:00)
    schedule.every().day.at("09:00").do(run_health_check)

    logger.info("📅 스케줄 등록 완료:")
    logger.info("  - 06:05 KST: 미국 장 마감 알림 (월~금)")
    logger.info("  - 09:00 KST: 헬스체크")
    logger.info("  - 10:00 KST: 장중 가격 알림 1차 (월~금, ±5%)")
    logger.info("  - 13:00 KST: 장중 가격 알림 2차 (월~금, ±5%)")
    logger.info("  - 14:30 KST: 장중 가격 알림 3차 (월~금, ±5%)")
    logger.info("  - 15:35 KST: 한국 장 마감 수집 (월~금)")


def run_korea_realtime() -> None:
    """장중 실시간 데이터 수집 (5분 간격)"""
    if not is_weekday():
        logger.info("⏭️ 주말 — 실시간 수집 스킵")
        return

    # Check if market is open (09:00-15:30 KST)
    now = datetime.now()
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if not (market_open <= now <= market_close):
        logger.info("⏭️ 장외 시간 — 실시간 수집 스킵")
        return

    if not is_market_holiday(now.strftime("%Y%m%d")):
        logger.info("⚡ 장중 실시간 수집 시작")
        try:
            result = subprocess.run(
                ["python", str(PIPELINE_DIR / "kis_collector.py"), "--mode", "korea"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                logger.info("✅ 장중 실시간 수집 완료")
                logger.debug(result.stdout)
            else:
                logger.error(f"❌ 장중 실시간 수집 실패:\n{result.stderr}")
        except subprocess.TimeoutExpired:
            logger.error("❌ 장중 실시간 수집 타임아웃 (120초)")
        except Exception as e:
            logger.error(f"❌ 장중 실시간 수집 예외: {e}")
    else:
        logger.info("⏭️ 공휴일 — 실시간 수집 스킵")


def main() -> None:
    logger.info("🚀 Jarvis 마켓 스케줄러 시작")
    setup_schedule()
    
    # 다음 실행 예정 출력
    jobs = schedule.get_jobs()
    for job in jobs:
        logger.info(f"  다음 실행: {job.next_run.strftime('%Y-%m-%d %H:%M')} — {job.job_func.__name__}")
    
    # 실행 루프
    while True:
        schedule.run_pending()
        time.sleep(30)  # 30초마다 체크



# ──────────────────────────────────────────────
# 단일 인스턴스 보장 (중복 기동 방지)
# ──────────────────────────────────────────────
PID_FILE = PIPELINE_DIR / "logs" / "scheduler.pid"

def acquire_pid_lock():
    """PID 파일로 단일 인스턴스 보장"""
    if PID_FILE.exists():
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            # 프로세스 살아있는지 확인
            os.kill(old_pid, 0)
            logger.warning(f"⚠️ 이미 스케줄러 실행 중 (PID {old_pid}) — 종료")
            sys.exit(0)
        except (ProcessLookupError, ValueError):
            pass  # 죽은 프로세스, 계속 진행
        except OSError:
            pass
    # 현재 PID 기록
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink() if PID_FILE.exists() else None)

acquire_pid_lock()

if __name__ == "__main__":
    main()
