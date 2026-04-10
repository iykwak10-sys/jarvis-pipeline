"""
kis_collector.py
────────────────────────────────────────────────────────
한국투자증권(KIS) REST API → 관심종목 종가 수집 → JSON 저장 → 텔레그램 알림
실행: python3 kis_collector.py [--date YYYYMMDD] [--mode korea|us]
────────────────────────────────────────────────────────
"""

import os
import json
import time
import logging
import argparse
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# dotenv 로드 (.env 파일에서 환경변수 읽기)
try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / "jarvis-pipeline" / ".env")
except ImportError:
    pass

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

# 한국투자증권 실전투자 API 서버
BASE_URL      = "https://openapi.koreainvestment.com:9443"
TOKEN_URL = f"{BASE_URL}/oauth2/tokenP"
STOCK_API_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"

OUTPUT_DIR    = Path.home() / "jarvis-pipeline" / "data"
LOG_DIR       = Path.home() / "jarvis-pipeline" / "logs"

# 텔레그램 설정 (환경변수에서 로드)
TELEGRAM_BOT_TOKEN = os.environ.get("JARVIS_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("JARVIS_CHAT_ID", "8663369518")

# 관심 포트폴리오
def load_portfolio() -> dict:
    '''portfolio.json에서 보유 종목 로드'''
    portfolio_path = Path.home() / "kis-mcp" / "portfolio.json"
    with open(portfolio_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    kospi_stocks = []
    kosdaq_stocks = []
    
    for holding in data.get("holdings", []):
        code = holding["ticker"].zfill(6)
        # Simple market classification (improve with KRX API)
        if int(code) < 100000:  # Temporary classification
            kospi_stocks.append({"code": code, "name": holding["name"]})
        else:
            kosdaq_stocks.append({"code": code, "name": holding["name"]})
    
    return {
        "KOSPI": kospi_stocks,
        "KOSDAQ": kosdaq_stocks
    }

PORTFOLIO = load_portfolio()

# ──────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"kis_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 토큰 관리
# ──────────────────────────────────────────────
class TokenManager:
    """한국투자증권 REST API 토큰 발급 및 캐싱"""

    TOKEN_CACHE_FILE = OUTPUT_DIR / ".kis_token.json"

    def __init__(self, app_key: str, app_secret: str):
        self.app_key    = app_key
        self.app_secret = app_secret
        self._token     = None
        self._expires   = None

    def get_token(self) -> str:
        """유효한 토큰 반환 (만료 시 재발급)"""
        if self._is_valid():
            return self._token
        return self._issue_token()

    def _is_valid(self) -> bool:
        """토큰 유효성 확인 (캐시 파일 포함)"""
        if not self._token or not self._expires:
            if self.TOKEN_CACHE_FILE.exists():
                try:
                    cache = json.loads(self.TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
                    exp   = datetime.fromisoformat(cache["expires_at"])
                    if datetime.now() < exp - timedelta(minutes=10):
                        self._token   = cache["token"]
                        self._expires = exp
                        logger.info("✅ 캐시된 토큰 사용")
                        return True
                except Exception:
                    pass
            return False
        return datetime.now() < self._expires - timedelta(minutes=10)

    def _issue_token(self) -> str:
        """신규 접근토큰 발급 (한국투자증권 방식)"""
        logger.info("🔑 새 접근토큰 발급 중...")
        payload = {
            "grant_type": "client_credentials",
            "appkey":     self.app_key,
            "appsecret":  self.app_secret
        }
        resp = requests.post(TOKEN_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise ValueError(f"토큰 발급 실패: {data}")

        self._token   = data["access_token"]
        # 한국투자증권 토큰 유효기간: 1일 (86400초)
        expires_in    = int(data.get("expires_in", 86400))
        self._expires = datetime.now() + timedelta(seconds=expires_in)

        # 캐시 저장
        self.TOKEN_CACHE_FILE.write_text(
            json.dumps({
                "token":      self._token,
                "expires_at": self._expires.isoformat()
            }, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"✅ 토큰 발급 완료 (만료: {self._expires.strftime('%Y-%m-%d %H:%M')})")
        return self._token


# ──────────────────────────────────────────────
# 한국투자증권 API 클라이언트
# ──────────────────────────────────────────────
class KISClient:
    """한국투자증권 REST API 래퍼"""

    def __init__(self, token_manager: TokenManager, app_key: str, app_secret: str):
        self.tm         = token_manager
        self.app_key    = app_key
        self.app_secret = app_secret
        self.session    = requests.Session()

    def _headers(self, tr_id: str) -> dict:
        """한국투자증권 API 공통 헤더"""
        return {
            "Content-Type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {self.tm.get_token()}",
            "appkey":        self.app_key,
            "appsecret":     self.app_secret,
            "tr_id":         tr_id,
            "custtype":      "P",  # 개인
        }

    def get_stock_price(self, code: str, retries: int = 3) -> Optional[dict]:
        """
        주식 현재가 조회
        TR_ID: FHKST01010100 (주식현재가 시세)
        """
        params = {
            "fid_cond_mrkt_div_code": "J",   # J=주식
            "fid_input_iscd":         code,  # 종목코드
        }
        for attempt in range(retries):
            try:
                resp = self.session.get(
                    STOCK_API_URL,
                    headers=self._headers("FHKST01010100"),
                    params=params,
                    timeout=10
                )
                resp.raise_for_status()
                data = resp.json()

                rt_cd = data.get("rt_cd", "")
                if rt_cd == "0":
                    return self._parse_stock(code, data)
                else:
                    msg = data.get("msg1", "Unknown")
                    logger.warning(f"⚠️ {code} API 오류 (rt_cd={rt_cd}): {msg}")
                    # 초당 거래건수 초과(EGW00201) 시 재시도
                    if "EGW00201" in msg:
                        logger.info(f"  ⏳ 초당 거래건수 초과 — {attempt+1}초 후 재시도")
                        time.sleep(attempt + 1)
                        continue
                    return None

            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ {code} 요청 실패 ({attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff

        return None

    def _parse_stock(self, code: str, data: dict) -> dict:
        """
        한국투자증권 API 응답 파싱
        output 필드 참조: https://apiportal.koreainvestment.com
        """
        d = data.get("output", {})

        def to_int(val):
            try:
                return int(str(val).replace(",", "").replace("+", "").strip())
            except (ValueError, TypeError):
                return 0

        def to_float(val):
            try:
                return float(str(val).replace(",", "").replace("+", "").strip())
            except (ValueError, TypeError):
                return 0.0

        close_price  = to_int(d.get("stck_prpr", 0))       # 현재가(종가)
        change       = to_int(d.get("prdy_vrss", 0))        # 전일 대비
        change_pct   = to_float(d.get("prdy_ctrt", 0))      # 전일 대비율(%)
        volume       = to_int(d.get("acml_vol", 0))         # 누적 거래량
        trading_val  = to_int(d.get("acml_tr_pbmn", 0))     # 누적 거래대금
        high         = to_int(d.get("stck_hgpr", 0))        # 고가
        low          = to_int(d.get("stck_lwpr", 0))        # 저가
        open_price   = to_int(d.get("stck_oprc", 0))        # 시가

        return {
            "code":          code,
            "close":         close_price,
            "change":        change,
            "change_pct":    change_pct,
            "volume":        volume,
            "trading_value": trading_val,
            "high":          high,
            "low":           low,
            "open":          open_price,
        }


# ──────────────────────────────────────────────
# 데이터 수집기
# ──────────────────────────────────────────────
class MarketDataCollector:
    """관심 포트폴리오 전종목 종가 수집 및 저장"""

    def __init__(self, client: KISClient, notifier: 'TelegramNotifier' = None):
        self.client = client
        self.notifier = notifier

    def collect(self, target_date: str) -> dict:
        """전 종목 수집 후 JSON 구조 반환"""
        logger.info(f"📡 {target_date} 데이터 수집 시작...")
        result = {
            "date":              target_date,
            "collected_at":      datetime.now().isoformat(),
            "source":            "한국투자증권 KIS API",
            "kospi_portfolio":   [],
            "kosdaq_portfolio":  [],
            "kospi_index":       {},
            "kosdaq_index":      {},
            "collection_status": {}
        }

        # Initialize price change detector for real-time alerts
        price_detector = PriceChangeDetector(self.notifier)

        for market, stocks in PORTFOLIO.items():
            key = f"{market.lower()}_portfolio"
            for stock in stocks:
                logger.info(f"  → {stock['name']} ({stock['code']}) 조회 중...")
                data = self.client.get_stock_price(stock["code"])

                if data and data.get("close"):
                    data["name"] = stock["name"]
                    result[key].append(data)
                    result["collection_status"][stock["code"]] = "✅"
                    logger.info(
                        f"    ✅ {stock['name']}: "
                        f"{data['close']:,}원 ({data['change_pct']:+.2f}%)"
                    )
                    
                    # Check for significant price changes (±5%) and send immediate alert
                    price_detector.check_and_alert(
                        code=stock["code"],
                        name=stock["name"],
                        current_price=data["close"],
                        change_pct=data["change_pct"]
                    )
                else:
                    result[key].append({
                        "code":       stock["code"],
                        "name":       stock["name"],
                        "close":      None,
                        "change_pct": None,
                        "error":      "수집 실패"
                    })
                    result["collection_status"][stock["code"]] = "❌"
                    logger.error(f"    ❌ {stock['name']}: 수집 실패")

                # ⚠️ 한국투자증권 API: 초당 20건 제한 → 0.1초 간격
                time.sleep(0.1)

        success = sum(1 for v in result["collection_status"].values() if v == "✅")
        total   = len(result["collection_status"])
        logger.info(f"📊 수집 완료: {success}/{total} 종목 성공")
        return result

    def collect_realtime(self) -> dict:
        """실시간 현재가 수집 (장중 5분 간격)"""
        if not self.is_market_open():
            return {"status": "market_closed"}
        
        logger.info("📡 실시간 데이터 수집 시작...")
        result = {
            "collected_at": datetime.now().isoformat(),
            "source": "한국투자증권 KIS API (실시간)",
            "kospi_portfolio": [],
            "kosdaq_portfolio": [],
            "collection_status": {}
        }

        # Initialize price change detector for real-time alerts
        price_detector = PriceChangeDetector(self.notifier)

        for market, stocks in PORTFOLIO.items():
            key = f"{market.lower()}_portfolio"
            for stock in stocks:
                logger.info(f"  → {stock['name']} ({stock['code']}) 실시간 조회 중...")
                data = self.client.get_stock_price(stock["code"])

                if data and data.get("close"):
                    data["name"] = stock["name"]
                    result[key].append(data)
                    result["collection_status"][stock["code"]] = "✅"
                    logger.info(
                        f"    ✅ {stock['name']}: "
                        f"{data['close']:,}원 ({data['change_pct']:+.2f}%)"
                    )
                    
                    # Check for significant price changes (±5%) and send immediate alert
                    price_detector.check_and_alert(
                        code=stock["code"],
                        name=stock["name"],
                        current_price=data["close"],
                        change_pct=data["change_pct"]
                    )
                else:
                    result[key].append({
                        "code":       stock["code"],
                        "name":       stock["name"],
                        "close":      None,
                        "change_pct": None,
                        "error":      "수집 실패"
                    })
                    result["collection_status"][stock["code"]] = "❌"
                    logger.error(f"    ❌ {stock['name']}: 수집 실패")

                # ⚠️ 한국투자증권 API: 초당 20건 제한 → 0.1초 간격
                time.sleep(0.1)

        success = sum(1 for v in result["collection_status"].values() if v == "✅")
        total   = len(result["collection_status"])
        logger.info(f"📊 실시간 수집 완료: {success}/{total} 종목 성공")
        return result

    def is_market_open(self) -> bool:
        """장중 여부 확인 (09:00-15:30 KST, 주말 제외)"""
        now = datetime.now()
        # 주말 체크
        if now.weekday() >= 5:  # 토요일(5) 또는 일요일(6)
            return False
        
        # 시간 체크 (09:00-15:30)
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        
        return market_open <= now <= market_close

    def get_last_trading_day(self, date_str: str = None) -> str:
        """마지막 거래일 반환 (주말/공휴일 고려)"""
        if date_str is None:
            date = datetime.now()
        else:
            date = datetime.strptime(date_str, "%Y%m%d")
        
        # 주말이면 이전 금요일로 이동
        while date.weekday() >= 5:  # 토요일(5) 또는 일요일(6)
            date -= timedelta(days=1)
        
        # TODO: 실제 공휴일 체크 로직 추가 가능
        # 현재는 주말만 고려
        
        return date.strftime("%Y%m%d")

    def save(self, data: dict, target_date: str) -> Path:
        """JSON 파일 저장"""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"market_data_{target_date}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info(f"💾 저장 완료: {path}")
        return path


# ──────────────────────────────────────────────
# 텔레그램 알림
# ──────────────────────────────────────────────
class TelegramNotifier:
    """Jarvis 텔레그램 봇 알림"""

    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self.url     = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, message: str) -> bool:
        if not self.token:
            logger.warning("⚠️ 텔레그램 토큰 없음 — 알림 스킵")
            return False
        try:
            resp = requests.post(self.url, json={
                "chat_id":    self.chat_id,
                "text":       message,
                "parse_mode": "HTML"
            }, timeout=10)
            resp.raise_for_status()
            logger.info("📱 텔레그램 알림 전송 완료")
            return True
        except Exception as e:
            logger.error(f"❌ 텔레그램 전송 실패: {e}")
            return False
    def format_summary(self, data: dict) -> str:
        """수집 완료 요약 메시지 생성"""
        date       = data.get("date", "")
        all_stocks = data.get("kospi_portfolio", []) + data.get("kosdaq_portfolio", [])

        success    = sum(1 for s in all_stocks if s.get("close"))
        total      = len(all_stocks)

        lines = [
            "📊 <b>KIS API 데이터 수집 완료</b>",
            f"📅 날짜: {date}",
            f"✅ 수집: {success}/{total}종목",
            "",
            "<b>🇰🇷 관심종목 현황</b>",
        ]

        for s in all_stocks:
            if s.get("close"):
                pct  = s.get("change_pct", 0)
                icon = "🔺" if pct > 0 else ("🔻" if pct < 0 else "➡️")
                lines.append(
                    f"{icon} {s['name']}: {s['close']:,}원 ({pct:+.2f}%)"
                )
            else:
                lines.append(f"❌ {s['name']}: 수집 실패")

        lines.append(f"\\n📁 market_data_{date}.json 저장 완료")
        return "\\n".join(lines)


class PriceChangeDetector:
    def __init__(self, notifier=None):
        self.last_prices = {}
        self.alerted_today = set()
        self._last_reset_date = None
        self.notifier = notifier
    
    def check_and_alert(self, code: str, name: str, current_price: int, change_pct: float):
        '''±5% 변동 시 즉시 알림'''
        today = datetime.now().date()
        if self._last_reset_date != today:
            self.alerted_today.clear()
            self._last_reset_date = today
    
        if code in self.alerted_today:
            return
    
        if abs(change_pct) >= 5.0:
            direction = "상승" if change_pct > 0 else "하락"
            message = (
                f"🚨 <b>{name} ({code}) 급{direction} 알림</b>\\n"
                f"💰 현재가: {current_price:,}원\\n"
                f"📈 변동률: {change_pct:+.2f}%\\n"
                f"⏰ 감지시간: {datetime.now().strftime('%H:%M:%S')}"
            )
            if self.notifier:
                self.notifier.send(message)
            self.alerted_today.add(code)
            logger.info(f"🚨 {name} ({code}) {direction} {change_pct:+.2f}% 알림 전송")


# ──────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="한국투자증권 KIS API 주식 데이터 수집기")
    parser.add_argument("--date",    default=None,    help="YYYYMMDD (기본: 오늘)")
    parser.add_argument("--mode",    default="korea", choices=["korea", "us"],
                        help="korea=한국장마감, us=미국장마감")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 구조만 확인")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y%m%d")
    logger.info(f"🚀 KIS 수집기 시작 | 날짜: {target_date} | 모드: {args.mode}")

    # 환경변수에서 키 로드
    app_key    = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")

    if not app_key or not app_secret:
        logger.error("❌ KIS_APP_KEY / KIS_APP_SECRET 환경변수를 설정하세요")
        logger.error("   ~/Downloads/jarvis-pipeline/.env 파일을 확인하세요")
        return

    if args.dry_run:
        logger.info("🧪 DRY-RUN 모드 — API 호출 없이 종료")
        logger.info(f"   APP_KEY: {app_key[:8]}...")
        return

    # 실행
    token_mgr = TokenManager(app_key, app_secret)
    client    = KISClient(token_mgr, app_key, app_secret)
    notifier  = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    collector = MarketDataCollector(client, notifier)

    try:
        data      = collector.collect(target_date)
        save_path = collector.save(data, target_date)

        # 노션 + 구글시트 저장
        try:
            from auto_saver import save_all
            save_results = save_all(
                report_text="",   # 분석 리포트는 별도 스킬에서 생성
                market_data=data,
                date_str=target_date,
            )
            logger.info(f"💾 저장 결과: {save_results}")
        except Exception as e:
            logger.error(f"❌ auto_saver 오류: {e}")

        summary = notifier.format_summary(data)
        notifier.send(summary)

        logger.info(f"🎉 파이프라인 완료: {save_path}")

    except Exception as e:
        err_msg = f"❌ KIS 파이프라인 오류: {e}"
        logger.error(err_msg)
        notifier.send(f"⚠️ <b>Jarvis 경고</b>\n{err_msg}")
        raise


if __name__ == "__main__":
    main()
