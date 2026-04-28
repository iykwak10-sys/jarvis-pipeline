# bot_webhook.py
"""Telegram bot webhook listener for Jarvis (no polling)"""

import logging
import os
from pathlib import Path
import atexit
import fcntl

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core import portfolio, notifier
from core.kis_client import KISClient

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


# Load environment variables from .env if present
BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot_webhook.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

ALLOWED_CHAT_ID = int(os.environ.get("JARVIS_CHAT_ID", 0))

# Start with single-instance lock to avoid multi-bot collisions
lock_fd = acquire_lock()
if lock_fd is None:
    logger.error("Jarvis 봇이 이미 실행 중입니다. 다른 인스턴스를 종료하고 재시작해주세요.")
    raise SystemExit(1)
atexit.register(release_lock)

# Handlers (copy of conversational commands from legacy bot.py)
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "사용법: /add 종목코드 종목명 [섹터] [수량] [매입가]\n예: /add 005930 삼성전자 반도체 6 75000"
        )
        return
    code = args[0].zfill(6)
    name = args[1]
    sector = args[2] if len(args) > 2 else "기타"
    quantity = int(args[3]) if len(args) > 3 else 0
    buy_price = int(args[4]) if len(args) > 4 else None
    ok = portfolio.add(code, name, sector, quantity, buy_price)
    if ok:
        await update.message.reply_text(f"✅ 추가됨: {name} ({code}) {quantity}주")
    else:
        await update.message.reply_text(f"⚠️ 이미 존재: {name} ({code})")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("사용법: /remove 종목코드\n예: /remove 005930")
        return
    code = context.args[0].zfill(6)
    stocks = portfolio.load()
    name = next((s["name"] for s in stocks if s["code"] == code), code)
    ok = portfolio.remove(code)
    if ok:
        await update.message.reply_text(f"✅ 삭제됨: {name} ({code})")
    else:
        await update.message.reply_text(f"⚠️ 존재하지 않음: {code}")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    stocks = portfolio.load()
    lines = ["📋 <b>포트폴리오 목록</b>\n"]
    for s in stocks:
        qty = s.get("quantity", 0)
        buy = f" | 매입가 {s['buy_price']:,}원" if s.get("buy_price") else ""
        lines.append(f"{s['name']} ({s['code']}) {qty}주{buy}")
    lines.append(f"\n총 {len(stocks)}종목")
    notifier.send("\n".join(lines))
    await update.message.reply_text("목록 전송 완료")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await update.message.reply_text("조회 중...")
    client = KISClient()
    if context.args:
        code = context.args[0].zfill(6)
        stocks = portfolio.load()
        stock_map = {s["code"]: s for s in stocks}
        price = client.get_price(code)
        name = stock_map.get(code, {}).get("name", code)
        change_pct = price["change_pct"]
        arrow = "▲" if change_pct >= 0 else "▼"
        await update.message.reply_text(
            f"{name} ({code})\n" +
            f"현재가: {price['close']:,}원\n" +
            f"등락: {arrow}{abs(change_pct):.2f}%"
        )
    else:
        stocks = portfolio.load()
        prices = client.get_prices([s["code"] for s in stocks])
        stock_map = {s["code"]: s for s in stocks}
        stocks_data = []
        for p in prices:
            s = stock_map.get(p["code"], {})
            stocks_data.append({**p, "name": s.get("name", p["code"]),
                                "quantity": s.get("quantity", 0),
                                "buy_price": s.get("buy_price")})
        notifier.send_portfolio_report(stocks_data)
        await update.message.reply_text("현재가 리포트 전송 완료")

def main() -> None:
    token = os.environ.get("JARVIS_BOT_TOKEN")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("p", cmd_price))
    logger.info("Jarvis webhook bot 시작— /add /remove /list /p 대기 중")
    port = int(os.environ.get("PORT", 8443))
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        logger.error("WEBHOOK_URL이 설정되지 않았습니다. 공개 URL을 설정해 주세요.")
        return
    app.run_webhook(listen="0.0.0.0", port=port, url_path=token, webhook_url=webhook_url.rstrip('/') + "/" + token)

if __name__ == "__main__":
    main()
