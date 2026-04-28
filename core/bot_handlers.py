# core/bot_handlers.py
"""Telegram 봇 공통 명령어 핸들러 — bot.py와 bot_webhook.py에서 공유"""

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from core import portfolio, notifier
from core.kis_client import KISClient

logger = logging.getLogger(__name__)


def auth(update: Update, allowed_chat_id: int) -> bool:
    """허가된 채팅 ID만 명령 수락"""
    return update.effective_chat.id == allowed_chat_id


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  allowed_chat_id: int) -> None:
    """/add 종목코드 종목명 [섹터] [수량] [매입가]"""
    if not auth(update, allowed_chat_id):
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


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     allowed_chat_id: int) -> None:
    """/remove 종목코드"""
    if not auth(update, allowed_chat_id):
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


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   allowed_chat_id: int) -> None:
    """/list — 현재 포트폴리오 목록"""
    if not auth(update, allowed_chat_id):
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


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    allowed_chat_id: int) -> None:
    """/p [종목코드] — 현재가 즉시 조회"""
    if not auth(update, allowed_chat_id):
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


def register_handlers(app, allowed_chat_id: int) -> None:
    """Application에 모든 핸들러 등록 (allowed_chat_id 고정)"""
    from telegram.ext import CommandHandler

    def _wrap(handler):
        """allowed_chat_id를 closure로 캡처"""
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await handler(update, context, allowed_chat_id)
        return wrapped

    app.add_handler(CommandHandler("add", _wrap(cmd_add)))
    app.add_handler(CommandHandler("remove", _wrap(cmd_remove)))
    app.add_handler(CommandHandler("list", _wrap(cmd_list)))
    app.add_handler(CommandHandler("p", _wrap(cmd_price)))
    logger.info("공통 핸들러 등록 완료: /add /remove /list /p")
