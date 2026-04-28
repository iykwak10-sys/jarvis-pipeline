# bot.py
"""Telegram 봇 — 종목 추가/삭제/조회 명령어 처리 (별도 프로세스로 실행)"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import asyncio
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from core import portfolio, notifier
from core.kis_client import KISClient

LOG_DIR = Path(__file__).parent / "logs"
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

ALLOWED_CHAT_ID = int(os.environ["JARVIS_CHAT_ID"])


def auth(update: Update) -> bool:
    """허가된 채팅 ID만 명령 수락"""
    return update.effective_chat.id == ALLOWED_CHAT_ID


# 1. 타이핑 상태를 유지하는 백그라운드 함수
async def keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """답변이 올 때까지 4.5초마다 타이핑 액션을 지속적으로 전송합니다."""
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4.5)  # 텔레그램 타이핑 만료(5초) 전 갱신
    except asyncio.CancelledError:
        # 응답이 완료되어 태스크가 취소되면 조용히 종료합니다.
        pass


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/add 종목코드 종목명 [섹터] [수량] [매입가]
    예: /add 005930 삼성전자 반도체 6 75000
    """
    if not auth(update):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "사용법: /add 종목코드 종목명 [섹터] [수량] [매입가]\n"
            "예: /add 005930 삼성전자 반도체 6 75000"
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
    """/remove 종목코드"""
    if not auth(update):
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
    """/list — 현재 포트폴리오 목록"""
    if not auth(update):
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
    """/p [종목코드] — 현재가 즉시 조회"""
    if not auth(update):
        return
    await update.message.reply_text("조회 중...")

    client = KISClient()

    if context.args:
        # 특정 종목
        code = context.args[0].zfill(6)
        stocks = portfolio.load()
        stock_map = {s["code"]: s for s in stocks}
        price = client.get_price(code)
        name = stock_map.get(code, {}).get("name", code)
        change_pct = price["change_pct"]
        arrow = "▲" if change_pct >= 0 else "▼"
        await update.message.reply_text(
            f"{name} ({code})\n"
            f"현재가: {price['close']:,}원\n"
            f"등락: {arrow}{abs(change_pct):.2f}%"
        )
    else:
        # 전체 포트폴리오
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

        # 답변이 준비되었으므로 타이핑 루프 종료 ('점 세 개' 사라짐)
        typing_task.cancel()
        
        # 텔레그램으로 최종 결과 전송
        await update.message.reply_text(response[:4000])

    except Exception as e:
        # 에러 발생 시에도 타이핑 표시는 확실히 끕니다.
        typing_task.cancel()
        logger.error(f"Hermes Agent 호출 오류: {e}")
        await update.message.reply_text("⚠️ AI 호출 중 시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

def main() -> None:
    token = os.environ["JARVIS_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("p", cmd_price))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Jarvis 봇 시작 — /add /remove /list /p 대기 중")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
