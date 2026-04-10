"""
price_alert.py
────────────────────────────────────────────────────────
장중 포트폴리오 급등/급락 알림
- 보유종목 전체의 당일 등락률을 KIS API로 조회
- 절대등락률 ±5% 이상 → 텔레그램 즉시 알림
- 거래량 급증 (당일 거래대금 상위권) → 추가 알림

실행: python3 price_alert.py [--threshold 5.0]
스케줄: 10:00, 13:00, 14:30 KST (장중 3회)
────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import json
import time
import logging
import argparse
import requests
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / "jarvis-pipeline" / ".env")
except ImportError:
    pass

# ─── 설정 ────────────────────────────────────────────────────────────────────
BASE_URL       = "https://openapi.koreainvestment.com:9443"
TOKEN_URL      = f"{BASE_URL}/oauth2/tokenP"
STOCK_API_URL  = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
PORTFOLIO_FILE = Path.home() / "kis-mcp" / "portfolio.json"
TOKEN_CACHE    = Path.home() / "jarvis-pipeline" / "data" / ".kis_token.json"
LOG_DIR        = Path.home() / "jarvis-pipeline" / "logs"

TELEGRAM_BOT_TOKEN = os.environ.get("JARVIS_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("JARVIS_CHAT_ID", "8663369518")

# ─── 로깅 ────────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"price_alert_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# ─── 토큰 관리 ───────────────────────────────────────────────────────────────
class TokenManager:
    def __init__(self, app_key: str, app_secret: str):
        self.app_key    = app_key
        self.app_secret = app_secret
        self._token     = None

    def get_token(self) -> str:
        # 캐시 파일 확인
        if TOKEN_CACHE.exists():
            try:
                cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
                from datetime import timedelta
                exp = datetime.fromisoformat(cache["expires_at"])
                if datetime.now() < exp - timedelta(minutes=10):
                    return cache["token"]
            except Exception:
                pass
        return self._issue_token()

    def _issue_token(self) -> str:
        resp = requests.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "appkey":     self.app_key,
            "appsecret":  self.app_secret,
        }, timeout=10)
        resp.raise_for_status()
        data  = resp.json()
        token = data["access_token"]
        from datetime import timedelta
        expires_at = datetime.now() + timedelta(seconds=int(data.get("expires_in", 86400)))
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(
            json.dumps({"token": token, "expires_at": expires_at.isoformat()}, ensure_ascii=False),
            encoding="utf-8"
        )
        return token


# ─── KIS API ─────────────────────────────────────────────────────────────────
def get_stock_price(token: str, app_key: str, app_secret: str, ticker: str) -> dict | None:
    headers = {
        "Content-Type":  "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey":        app_key,
        "appsecret":     app_secret,
        "tr_id":         "FHKST01010100",
        "custtype":      "P",
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd":         ticker,
    }
    try:
        resp = requests.get(STOCK_API_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        d = data.get("output", {})

        def to_float(v):
            try:
                return float(str(v).replace(",", "").replace("+", "").strip())
            except (ValueError, TypeError):
                return 0.0

        def to_int(v):
            try:
                return int(str(v).replace(",", "").replace("+", "").strip())
            except (ValueError, TypeError):
                return 0

        return {
            "ticker":      ticker,
            "name":        d.get("hts_kor_isnm", ticker),
            "current":     to_int(d.get("stck_prpr", 0)),
            "change_pct":  to_float(d.get("prdy_ctrt", 0)),
            "change_amt":  to_int(d.get("prdy_vrss", 0)),
            "volume":      to_int(d.get("acml_vol", 0)),
            "trade_val":   to_int(d.get("acml_tr_pbmn", 0)),
            "high":        to_int(d.get("stck_hgpr", 0)),
            "low":         to_int(d.get("stck_lwpr", 0)),
        }
    except Exception as e:
        logger.warning(f"⚠️ {ticker} 조회 실패: {e}")
        return None


# ─── 텔레그램 ─────────────────────────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("⚠️ 텔레그램 토큰 없음")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"❌ 텔레그램 전송 실패: {e}")
        return False


# ─── 포트폴리오 로드 ──────────────────────────────────────────────────────────
def load_portfolio() -> list[dict]:
    try:
        data = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
        return data.get("holdings", [])
    except Exception as e:
        logger.error(f"❌ portfolio.json 로드 실패: {e}")
        return []


# ─── 메인 로직 ───────────────────────────────────────────────────────────────
def run_price_alert(threshold: float = 5.0):
    now = datetime.now()
    logger.info(f"🔍 가격 알림 체크 시작 ({now.strftime('%H:%M')} KST, 임계값: ±{threshold}%)")

    app_key    = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    if not app_key or not app_secret:
        logger.error("❌ KIS_APP_KEY / KIS_APP_SECRET 미설정")
        return

    holdings = load_portfolio()
    if not holdings:
        logger.warning("⚠️ 포트폴리오 종목 없음")
        return

    tm    = TokenManager(app_key, app_secret)
    token = tm.get_token()

    alerts_up   = []  # 급등
    alerts_down = []  # 급락
    all_prices  = []

    for h in holdings:
        ticker = h["ticker"]
        price  = get_stock_price(token, app_key, app_secret, ticker)
        if not price:
            continue

        # 포트폴리오의 종목명을 우선 사용 (없으면 API 응답 사용)
        price["name"]      = h.get("name", price.get("name", ticker))
        price["avg_price"] = h.get("avg_price", 0)
        price["quantity"]  = h.get("quantity", 0)
        price["sector"]    = h.get("sector", "기타")

        # 누적 손익률 (매입단가 대비)
        if price["avg_price"] > 0:
            price["cum_gain_pct"] = round(
                (price["current"] - price["avg_price"]) / price["avg_price"] * 100, 2
            )
        else:
            price["cum_gain_pct"] = 0.0

        all_prices.append(price)

        pct = price["change_pct"]
        if pct >= threshold:
            alerts_up.append(price)
            logger.info(f"🔺 급등 감지: {price['name']} ({ticker}) {pct:+.2f}%")
        elif pct <= -threshold:
            alerts_down.append(price)
            logger.info(f"🔻 급락 감지: {price['name']} ({ticker}) {pct:+.2f}%")

        time.sleep(0.15)  # API Rate limit

    if not alerts_up and not alerts_down:
        logger.info(f"✅ 임계값 초과 종목 없음 (±{threshold}% 기준)")
        return

    # ─── 알림 메시지 생성 ────────────────────────────────────────────────────
    time_str = now.strftime("%H:%M")
    lines = [f"⚡ *장중 가격 알림 — {time_str} KST*", "━━━━━━━━━━━━━━━━━━━━━"]

    if alerts_up:
        lines.append(f"\n🔺 *급등 (±{threshold}% 이상)*")
        for a in sorted(alerts_up, key=lambda x: x["change_pct"], reverse=True):
            cum = a["cum_gain_pct"]
            cum_icon = "📈" if cum >= 0 else "📉"
            lines.append(
                f"• *{a['name']}* `{a['ticker']}`\n"
                f"  현재가: {a['current']:,}원  일간: *{a['change_pct']:+.2f}%*\n"
                f"  {cum_icon} 누적손익: {cum:+.2f}%  섹터: {a['sector']}"
            )

    if alerts_down:
        lines.append(f"\n🔻 *급락 (-{threshold}% 이하)*")
        for a in sorted(alerts_down, key=lambda x: x["change_pct"]):
            cum = a["cum_gain_pct"]
            cum_icon = "📈" if cum >= 0 else "📉"
            lines.append(
                f"• *{a['name']}* `{a['ticker']}`\n"
                f"  현재가: {a['current']:,}원  일간: *{a['change_pct']:+.2f}%*\n"
                f"  {cum_icon} 누적손익: {cum:+.2f}%  섹터: {a['sector']}"
            )

    lines.append("\n⚠️ _투자 참고용 / 투자 권유 아님_")
    message = "\n".join(lines)

    if send_telegram(message):
        logger.info(f"📱 알림 전송 완료 (급등 {len(alerts_up)}개, 급락 {len(alerts_down)}개)")
    else:
        logger.error("❌ 알림 전송 실패")


# ─── 엔트리포인트 ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="장중 포트폴리오 급등/급락 알림")
    parser.add_argument("--threshold", type=float, default=5.0, help="등락률 임계값 %% (기본: 5.0)")
    args = parser.parse_args()

    # 장 시간 체크 (09:00 ~ 15:30 KST)
    now = datetime.now()
    if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
        logger.info(f"⏭️ 장 마감 시간 — 알림 스킵 ({now.strftime('%H:%M')})")
        return

    # 주말 체크
    if now.weekday() >= 5:
        logger.info("⏭️ 주말 — 알림 스킵")
        return

    run_price_alert(args.threshold)


if __name__ == "__main__":
    main()
