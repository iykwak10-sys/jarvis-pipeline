# Jarvis Pipeline 고도화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KIS API 단일화, core/ 모듈 분리, Telegram 봇 종목 관리, Google Sheets 제거로 안정적인 개인 주식 자동화 파이프라인 구축

**Architecture:** 공통 기능(KIS API, 포트폴리오, Telegram, Notion)을 core/ 모듈로 분리하고, collector.py / price_alert.py / bot.py / scheduler.py가 이를 임포트하는 구조. bot.py는 별도 프로세스로 상시 실행, scheduler.py가 나머지 작업을 오케스트레이션.

**Tech Stack:** Python 3.9+, requests, schedule, python-dotenv, python-telegram-bot 20.x, Notion API v1

---

## 파일 맵

| 작업 | 파일 |
|------|------|
| 생성 | `core/__init__.py` |
| 생성 | `core/kis_client.py` |
| 생성 | `core/portfolio.py` |
| 생성 | `core/notifier.py` |
| 생성 | `core/notion_saver.py` |
| 생성 | `portfolio.json` |
| 생성 | `collector.py` |
| 생성 | `bot.py` |
| 수정 | `price_alert.py` |
| 수정 | `scheduler.py` |
| 수정 | `.gitignore` |
| 삭제 | `kis_collector.py`, `kiwoom_collector.py`, `kiwoom_api.py`, `kiwoom_telegram_bot.py`, `auto_saver.py`, `*.backup*` |

---

## Task 1: 정리 — 키움/백업 파일 삭제 및 .gitignore 정비

**Files:**
- Delete: `kis_collector.py`, `kiwoom_collector.py`, `kiwoom_api.py`, `kiwoom_telegram_bot.py`, `auto_saver.py`
- Delete: `kis_collector.py.backup`, `kis_collector.py.backup2`, `kis_collector.py.backup3`, `kis_collector.py.before_enhancement`
- Modify: `.gitignore`

- [ ] **Step 1: 실행 중인 스케줄러 중지**

```bash
kill $(cat ~/jarvis-pipeline/logs/scheduler.pid) 2>/dev/null || pkill -f "scheduler.py"
sleep 2
echo "스케줄러 중지 완료"
```

- [ ] **Step 2: 키움 및 백업 파일 삭제**

```bash
cd ~/jarvis-pipeline
rm -f kiwoom_collector.py kiwoom_api.py kiwoom_telegram_bot.py auto_saver.py
rm -f kis_collector.py.backup kis_collector.py.backup2 kis_collector.py.backup3 kis_collector.py.before_enhancement
echo "삭제 완료"
ls *.py
```

Expected output:
```
삭제 완료
kis_collector.py  price_alert.py  scheduler.py
```

- [ ] **Step 3: .gitignore 작성**

```bash
cat > ~/jarvis-pipeline/.gitignore << 'EOF'
.env
google_credentials.json
credentials/
data/.kis_token.json
logs/
__pycache__/
*.pyc
*.pyo
.DS_Store
EOF
```

- [ ] **Step 4: git에 변경사항 스테이징 및 커밋**

```bash
cd ~/jarvis-pipeline
git add -A
git commit -m "chore: 키움 코드 및 백업 파일 삭제, .gitignore 추가"
```

---

## Task 2: portfolio.json 현행화

**Files:**
- Create: `portfolio.json`

- [ ] **Step 1: portfolio.json 작성 (현재 보유 11종목)**

```bash
cat > ~/jarvis-pipeline/portfolio.json << 'EOF'
{
  "stocks": [
    {"code": "006800", "name": "미래에셋증권", "sector": "금융", "quantity": 30},
    {"code": "005930", "name": "삼성전자", "sector": "반도체", "quantity": 6},
    {"code": "010120", "name": "LS일렉트릭", "sector": "에너지/전력", "quantity": 17},
    {"code": "012450", "name": "한화에어로스페이스", "sector": "방산", "quantity": 16},
    {"code": "034020", "name": "두산에너빌리티", "sector": "에너지/발전", "quantity": 700},
    {"code": "042660", "name": "한화오션", "sector": "방산/조선", "quantity": 100},
    {"code": "052690", "name": "한전기술", "sector": "에너지/전력", "quantity": 70},
    {"code": "207940", "name": "삼성바이오로직스", "sector": "바이오", "quantity": 20},
    {"code": "272210", "name": "한화시스템", "sector": "방산", "quantity": 170},
    {"code": "336260", "name": "두산퓨어셀", "sector": "에너지/수소", "quantity": 90},
    {"code": "469150", "name": "ACE AI반도체TOP3+", "sector": "ETF", "quantity": 110}
  ]
}
EOF
```

- [ ] **Step 2: JSON 유효성 확인**

```bash
python3 -c "import json; d=json.load(open('portfolio.json')); print(f'종목 수: {len(d[\"stocks\"])}'); [print(f'  {s[\"code\"]} {s[\"name\"]} {s[\"quantity\"]}주') for s in d['stocks']]"
```

Expected output:
```
종목 수: 11
  006800 미래에셋증권 30주
  005930 삼성전자 6주
  ...
  469150 ACE AI반도체TOP3+ 110주
```

- [ ] **Step 3: 커밋**

```bash
cd ~/jarvis-pipeline
git add portfolio.json
git commit -m "feat: portfolio.json 현행화 (11종목)"
```

---

## Task 3: core/kis_client.py — KIS API 클라이언트

**Files:**
- Create: `core/__init__.py`
- Create: `core/kis_client.py`

- [ ] **Step 1: core 패키지 초기화**

```bash
mkdir -p ~/jarvis-pipeline/core
touch ~/jarvis-pipeline/core/__init__.py
```

- [ ] **Step 2: core/kis_client.py 작성**

```python
# core/kis_client.py
"""한국투자증권(KIS) REST API 클라이언트 및 토큰 관리"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_URL = f"{BASE_URL}/oauth2/tokenP"
PRICE_URL = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
TOKEN_CACHE = Path(__file__).parent.parent / "data" / ".kis_token.json"

logger = logging.getLogger(__name__)


class TokenManager:
    """KIS API 토큰 발급 및 캐시 관리"""

    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._expires: Optional[datetime] = None

    def get_token(self) -> str:
        if self._is_valid():
            return self._token
        return self._issue_token()

    def _is_valid(self) -> bool:
        if self._token and self._expires and datetime.now() < self._expires - timedelta(minutes=10):
            return True
        if TOKEN_CACHE.exists():
            try:
                cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
                exp = datetime.fromisoformat(cache["expires_at"])
                if datetime.now() < exp - timedelta(minutes=10):
                    self._token = cache["token"]
                    self._expires = exp
                    logger.info("캐시된 KIS 토큰 사용")
                    return True
            except Exception:
                pass
        return False

    def _issue_token(self) -> str:
        resp = requests.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._expires = datetime.now() + timedelta(seconds=86400)
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "token": self._token,
            "expires_at": self._expires.isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
        logger.info("KIS 토큰 발급 완료")
        return self._token


class KISClient:
    """KIS REST API 래퍼"""

    def __init__(self):
        self._token_mgr = TokenManager(
            os.environ["KIS_APP_KEY"],
            os.environ["KIS_APP_SECRET"],
        )

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token_mgr.get_token()}",
            "appkey": os.environ["KIS_APP_KEY"],
            "appsecret": os.environ["KIS_APP_SECRET"],
            "tr_id": "FHKST01010100",
        }

    def get_price(self, code: str) -> dict:
        """단일 종목 현재가 조회. 반환: {code, close, change, change_pct, volume, high, low, open}"""
        resp = requests.get(PRICE_URL, headers=self._headers(), params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }, timeout=10)
        resp.raise_for_status()
        output = resp.json().get("output", {})
        return {
            "code": code,
            "close": int(output.get("stck_prpr", 0)),
            "change": int(output.get("prdy_vrss", 0)),
            "change_pct": float(output.get("prdy_ctrt", 0)),
            "volume": int(output.get("acml_vol", 0)),
            "high": int(output.get("stck_hgpr", 0)),
            "low": int(output.get("stck_lwpr", 0)),
            "open": int(output.get("stck_oprc", 0)),
        }

    def get_prices(self, codes: list[str]) -> list[dict]:
        """여러 종목 배치 조회 (0.1초 간격, 초당 20건 제한 준수)"""
        results = []
        for code in codes:
            try:
                results.append(self.get_price(code))
            except Exception as e:
                logger.error(f"종목 {code} 조회 실패: {e}")
                results.append({"code": code, "close": 0, "change": 0,
                                 "change_pct": 0.0, "volume": 0})
            time.sleep(0.1)
        return results
```

- [ ] **Step 3: 빠른 동작 확인**

```bash
cd ~/jarvis-pipeline
python3 -c "
from core.kis_client import KISClient
client = KISClient()
result = client.get_price('005930')
print(f'삼성전자: {result[\"close\"]:,}원 ({result[\"change_pct\"]:+.2f}%)')
"
```

Expected output:
```
삼성전자: 58400원 (-0.85%)  # 실제 현재가로 출력
```

- [ ] **Step 4: 커밋**

```bash
cd ~/jarvis-pipeline
git add core/
git commit -m "feat: core/kis_client.py — KIS API 클라이언트 (TokenManager, KISClient)"
```

---

## Task 4: core/portfolio.py — 포트폴리오 관리

**Files:**
- Create: `core/portfolio.py`

- [ ] **Step 1: core/portfolio.py 작성**

```python
# core/portfolio.py
"""portfolio.json 단일 소스 관리 — 로드/저장/추가/삭제"""

import json
import logging
from pathlib import Path
from typing import Optional

PORTFOLIO_FILE = Path(__file__).parent.parent / "portfolio.json"
logger = logging.getLogger(__name__)


def load() -> list[dict]:
    """portfolio.json에서 종목 목록 로드"""
    with open(PORTFOLIO_FILE, encoding="utf-8") as f:
        return json.load(f)["stocks"]


def save(stocks: list[dict]) -> None:
    """종목 목록을 portfolio.json에 저장"""
    PORTFOLIO_FILE.write_text(
        json.dumps({"stocks": stocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"portfolio.json 저장 완료 ({len(stocks)}종목)")


def add(code: str, name: str, sector: str = "기타",
        quantity: int = 0, buy_price: Optional[int] = None) -> bool:
    """종목 추가. 이미 존재하면 False 반환"""
    stocks = load()
    if any(s["code"] == code for s in stocks):
        logger.warning(f"이미 존재하는 종목: {code} {name}")
        return False
    entry: dict = {"code": code, "name": name, "sector": sector, "quantity": quantity}
    if buy_price is not None:
        entry["buy_price"] = buy_price
    stocks.append(entry)
    save(stocks)
    logger.info(f"종목 추가: {code} {name}")
    return True


def remove(code: str) -> bool:
    """종목 삭제. 존재하지 않으면 False 반환"""
    stocks = load()
    new_stocks = [s for s in stocks if s["code"] != code]
    if len(new_stocks) == len(stocks):
        logger.warning(f"존재하지 않는 종목: {code}")
        return False
    save(new_stocks)
    logger.info(f"종목 삭제: {code}")
    return True


def codes() -> list[str]:
    """종목 코드 목록만 반환"""
    return [s["code"] for s in load()]
```

- [ ] **Step 2: 동작 확인**

```bash
cd ~/jarvis-pipeline
python3 -c "
from core import portfolio
stocks = portfolio.load()
print(f'종목 수: {len(stocks)}')
for s in stocks:
    print(f'  {s[\"code\"]} {s[\"name\"]} {s[\"quantity\"]}주')
"
```

Expected output:
```
종목 수: 11
  006800 미래에셋증권 30주
  005930 삼성전자 6주
  ...
```

- [ ] **Step 3: 커밋**

```bash
cd ~/jarvis-pipeline
git add core/portfolio.py
git commit -m "feat: core/portfolio.py — 포트폴리오 CRUD"
```

---

## Task 5: core/notifier.py — Telegram 알림

**Files:**
- Create: `core/notifier.py`

- [ ] **Step 1: core/notifier.py 작성**

```python
# core/notifier.py
"""Telegram 알림 전송 모듈"""

import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
logger = logging.getLogger(__name__)


def _token() -> str:
    return os.environ["JARVIS_BOT_TOKEN"]


def _chat_id() -> str:
    return os.environ["JARVIS_CHAT_ID"]


def send(message: str) -> bool:
    """텍스트 메시지 전송. HTML parse_mode 사용."""
    try:
        resp = requests.post(
            TELEGRAM_URL.format(token=_token()),
            json={"chat_id": _chat_id(), "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram 전송 실패: {e}")
        return False


def send_portfolio_report(stocks_data: list[dict]) -> bool:
    """포트폴리오 현황 리포트 전송.
    stocks_data: KISClient.get_prices() 결과 + portfolio 정보 병합 리스트
    각 항목: {code, name, close, change_pct, quantity, buy_price(optional)}
    """
    lines = ["📊 <b>포트폴리오 현황</b>\n"]
    total_value = 0
    total_profit = 0
    has_profit_data = False

    for s in stocks_data:
        close = s.get("close", 0)
        qty = s.get("quantity", 0)
        change_pct = s.get("change_pct", 0.0)
        arrow = "▲" if change_pct >= 0 else "▼"
        value = close * qty
        total_value += value

        line = f"{s['name']} {close:,}원 {arrow}{abs(change_pct):.2f}%"

        buy_price = s.get("buy_price")
        if buy_price and close:
            profit_pct = (close - buy_price) / buy_price * 100
            profit_amt = (close - buy_price) * qty
            total_profit += profit_amt
            has_profit_data = True
            sign = "+" if profit_pct >= 0 else ""
            line += f" | 수익 {sign}{profit_pct:.1f}%"

        lines.append(line)

    lines.append(f"\n💰 평가금액: {total_value:,}원")
    if has_profit_data:
        sign = "+" if total_profit >= 0 else ""
        lines.append(f"📈 평가손익: {sign}{total_profit:,}원")

    return send("\n".join(lines))


def send_alert(code: str, name: str, change_pct: float, close: int) -> bool:
    """급등/급락 알림 전송"""
    emoji = "🚀" if change_pct >= 0 else "📉"
    sign = "+" if change_pct >= 0 else ""
    message = (
        f"{emoji} <b>급등락 알림</b>\n"
        f"종목: {name} ({code})\n"
        f"등락률: {sign}{change_pct:.2f}%\n"
        f"현재가: {close:,}원"
    )
    return send(message)


def send_us_market_alert() -> bool:
    """미국 장 마감 알림 (06:05 KST)"""
    return send(
        "🇺🇸 <b>미국 장 마감 알림</b>\n"
        "Claude Cowork에서 '미국 마감 분석해줘'를 입력하세요."
    )
```

- [ ] **Step 2: 동작 확인**

```bash
cd ~/jarvis-pipeline
python3 -c "
from core.notifier import send
result = send('✅ Jarvis Pipeline 테스트 메시지')
print('전송 성공' if result else '전송 실패')
"
```

Expected: Telegram에 테스트 메시지 수신, 터미널에 `전송 성공` 출력

- [ ] **Step 3: 커밋**

```bash
cd ~/jarvis-pipeline
git add core/notifier.py
git commit -m "feat: core/notifier.py — Telegram 알림 (send, portfolio_report, alert)"
```

---

## Task 6: core/notion_saver.py — Notion 저장

**Files:**
- Create: `core/notion_saver.py`

- [ ] **Step 1: core/notion_saver.py 작성**

```python
# core/notion_saver.py
"""Notion API DB 저장 모듈"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def save_stock_prices(date_str: str, stocks_data: list[dict]) -> int:
    """종목 주가 DB에 날짜별 저장.
    stocks_data: {code, name, sector, close, change_pct, volume, quantity} 리스트
    반환: 저장 성공 종목 수
    """
    db_id = os.environ.get("NOTION_STOCK_DB_ID", "")
    if not db_id:
        logger.warning("NOTION_STOCK_DB_ID 미설정 — Notion 저장 스킵")
        return 0

    date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    success = 0

    for s in stocks_data:
        if not s.get("close"):
            continue
        payload = {
            "parent": {"database_id": db_id},
            "properties": {
                "종목명":  {"title":     [{"text": {"content": s.get("name", s["code"])}}]},
                "날짜":    {"date":      {"start": date_iso}},
                "종목코드": {"rich_text": [{"text": {"content": s["code"]}}]},
                "현재가":  {"number":    s["close"]},
                "등락률":  {"number":    s.get("change_pct", 0)},
                "거래량":  {"number":    s.get("volume", 0)},
                "섹터":    {"select":    {"name": s.get("sector", "기타")}},
            },
        }
        try:
            resp = requests.post(f"{NOTION_API}/pages", headers=_headers(),
                                 json=payload, timeout=30)
            resp.raise_for_status()
            success += 1
            logger.info(f"  Notion 저장: {s.get('name')} ({s['code']})")
        except Exception as e:
            logger.error(f"  Notion 저장 실패 {s['code']}: {e}")

    logger.info(f"Notion 주가 저장 완료: {success}/{len(stocks_data)}종목")
    return success


def save_analysis_report(date_str: str, report_text: str,
                          kospi_close: Optional[float] = None,
                          kospi_change_pct: Optional[float] = None) -> Optional[str]:
    """분석리포트 DB에 저장. 반환: 저장된 Notion 페이지 URL"""
    db_id = os.environ.get("NOTION_ANALYSIS_DB_ID", "")
    if not db_id:
        logger.warning("NOTION_ANALYSIS_DB_ID 미설정 — 분석리포트 저장 스킵")
        return None

    date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    properties: dict = {
        "이름": {"title": [{"text": {"content": f"마감분석_{date_str}"}}]},
        "날짜": {"date": {"start": date_iso}},
    }
    if kospi_close is not None:
        properties["KOSPI"] = {"number": kospi_close}
    if kospi_change_pct is not None:
        properties["KOSPI등락률"] = {"number": kospi_change_pct}

    # 리포트 텍스트를 1900자 단위 블록으로 분할
    chunk_size = 1900
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": report_text[i:i+chunk_size]}}]
            },
        }
        for i in range(0, len(report_text), chunk_size)
    ]

    try:
        resp = requests.post(f"{NOTION_API}/pages", headers=_headers(),
                             json={"parent": {"database_id": db_id},
                                   "properties": properties,
                                   "children": children},
                             timeout=30)
        resp.raise_for_status()
        url = resp.json().get("url", "")
        logger.info(f"분석리포트 Notion 저장: {url}")
        return url
    except Exception as e:
        logger.error(f"분석리포트 Notion 저장 실패: {e}")
        return None
```

- [ ] **Step 2: 커밋**

```bash
cd ~/jarvis-pipeline
git add core/notion_saver.py
git commit -m "feat: core/notion_saver.py — Notion 주가/리포트 저장"
```

---

## Task 7: collector.py — KIS 시세 수집기

**Files:**
- Create: `collector.py`
- Delete: `kis_collector.py` (이 태스크 완료 후)

- [ ] **Step 1: collector.py 작성**

```python
# collector.py
"""KIS 시세 수집 → JSON 저장 → Telegram 알림 → Notion 저장(마감 시)"""

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from core import portfolio, notifier, notion_saver
from core.kis_client import KISClient

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "collector.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def collect(closing: bool = False) -> list[dict]:
    """보유 종목 현재가 수집.
    closing=True: 마감 수집 (Notion 저장 + Telegram 마감 리포트)
    closing=False: 장중 수집 (JSON 저장만)
    반환: stocks_data 리스트
    """
    today = datetime.now().strftime("%Y%m%d")
    stocks = portfolio.load()
    client = KISClient()

    logger.info(f"{'마감' if closing else '장중'} 수집 시작: {len(stocks)}종목")

    price_map = {r["code"]: r for r in client.get_prices([s["code"] for s in stocks])}

    stocks_data = []
    for s in stocks:
        price = price_map.get(s["code"], {})
        stocks_data.append({
            "code": s["code"],
            "name": s["name"],
            "sector": s.get("sector", "기타"),
            "quantity": s.get("quantity", 0),
            "buy_price": s.get("buy_price"),
            "close": price.get("close", 0),
            "change": price.get("change", 0),
            "change_pct": price.get("change_pct", 0.0),
            "volume": price.get("volume", 0),
            "high": price.get("high", 0),
            "low": price.get("low", 0),
            "open": price.get("open", 0),
        })

    # JSON 저장
    output_file = DATA_DIR / f"market_data_{today}.json"
    payload = {
        "date": today,
        "collected_at": datetime.now().isoformat(),
        "source": "한국투자증권 KIS API",
        "stocks": stocks_data,
    }
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"JSON 저장: {output_file.name}")

    if closing:
        # Notion 저장
        notion_saver.save_stock_prices(today, stocks_data)
        # Telegram 마감 리포트
        notifier.send_portfolio_report(stocks_data)
        logger.info("마감 수집 완료 (Notion + Telegram)")
    else:
        logger.info("장중 수집 완료")

    return stocks_data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--closing", action="store_true", help="마감 수집 모드")
    args = parser.parse_args()
    collect(closing=args.closing)
```

- [ ] **Step 2: 장중 수집 테스트**

```bash
cd ~/jarvis-pipeline
python3 collector.py
```

Expected: `logs/collector.log`에 로그 기록, `data/market_data_YYYYMMDD.json` 갱신

- [ ] **Step 3: 마감 수집 테스트 (Telegram + Notion)**

```bash
cd ~/jarvis-pipeline
python3 collector.py --closing
```

Expected: Telegram에 포트폴리오 현황 메시지 수신, Notion DB에 종목 저장

- [ ] **Step 4: kis_collector.py 삭제 및 커밋**

```bash
cd ~/jarvis-pipeline
rm -f kis_collector.py
git add -A
git commit -m "feat: collector.py — KIS 시세 수집기 (core 모듈 활용, kis_collector.py 대체)"
```

---

## Task 8: price_alert.py 리팩토링

**Files:**
- Modify: `price_alert.py`

- [ ] **Step 1: price_alert.py 전체 교체**

```python
# price_alert.py
"""장중 급등/급락 알림 (±5% 기준, 10:00/13:00/14:30 KST 실행)"""

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from core import portfolio, notifier
from core.kis_client import KISClient

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "price_alert.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

THRESHOLD = 5.0  # 알림 기준 등락률 (%)


def is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def check_alerts() -> None:
    if not is_market_open():
        logger.info("장외 시간 — 알림 체크 스킵")
        return

    stocks = portfolio.load()
    client = KISClient()
    prices = client.get_prices([s["code"] for s in stocks])
    stock_map = {s["code"]: s for s in stocks}

    alert_count = 0
    for price in prices:
        code = price["code"]
        change_pct = price.get("change_pct", 0.0)
        if abs(change_pct) >= THRESHOLD:
            name = stock_map.get(code, {}).get("name", code)
            notifier.send_alert(code, name, change_pct, price.get("close", 0))
            logger.info(f"알림 전송: {name} ({code}) {change_pct:+.2f}%")
            alert_count += 1

    logger.info(f"알림 체크 완료: {alert_count}건 발송 / {len(prices)}종목 확인")


if __name__ == "__main__":
    check_alerts()
```

- [ ] **Step 2: 동작 확인**

```bash
cd ~/jarvis-pipeline
python3 price_alert.py
```

Expected: 장외 시간이면 `"장외 시간 — 알림 체크 스킵"`, 장중이면 ±5% 종목만 알림 전송

- [ ] **Step 3: 커밋**

```bash
cd ~/jarvis-pipeline
git add price_alert.py
git commit -m "refactor: price_alert.py — core 모듈 활용, 중복 코드 제거"
```

---

## Task 9: bot.py — Telegram 봇

**Files:**
- Create: `bot.py`

- [ ] **Step 1: python-telegram-bot 설치 확인**

```bash
python3 -c "import telegram; print(telegram.__version__)" 2>/dev/null || pip3 install "python-telegram-bot==20.7"
```

Expected: `20.7` 또는 설치 진행

- [ ] **Step 2: bot.py 작성**

```python
# bot.py
"""Telegram 봇 — 종목 추가/삭제/조회 명령어 처리 (별도 프로세스로 실행)"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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


def main() -> None:
    token = os.environ["JARVIS_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("p", cmd_price))
    logger.info("Jarvis 봇 시작 — /add /remove /list /p 대기 중")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 봇 동작 테스트 (포그라운드)**

```bash
cd ~/jarvis-pipeline
python3 bot.py
```

Telegram에서 `/list` 전송 → 포트폴리오 목록 수신 확인 후 Ctrl+C로 종료

- [ ] **Step 4: 커밋**

```bash
cd ~/jarvis-pipeline
git add bot.py
git commit -m "feat: bot.py — Telegram 봇 (/add /remove /list /p)"
```

---

## Task 10: scheduler.py 리팩토링

**Files:**
- Modify: `scheduler.py`

- [ ] **Step 1: scheduler.py 전체 교체**

```python
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
```

- [ ] **Step 2: 커밋**

```bash
cd ~/jarvis-pipeline
git add scheduler.py
git commit -m "refactor: scheduler.py — core 모듈 활용, bot.py 프로세스 관리 추가, RotatingFileHandler"
```

---

## Task 11: 최종 테스트 및 스케줄러 재시작

- [ ] **Step 1: 전체 파일 구조 확인**

```bash
cd ~/jarvis-pipeline
find . -name "*.py" | grep -v __pycache__ | sort
echo "---"
cat portfolio.json | python3 -m json.tool | head -20
```

Expected:
```
./bot.py
./collector.py
./core/__init__.py
./core/kis_client.py
./core/notion_saver.py
./core/notifier.py
./core/portfolio.py
./price_alert.py
./scheduler.py
```

- [ ] **Step 2: .env 불필요 항목 정리**

```bash
cd ~/jarvis-pipeline
# GOOGLE_CREDENTIALS_PATH, GSHEET_PORTFOLIO_ID 제거
grep -v "GOOGLE_CREDENTIALS_PATH\|GSHEET_PORTFOLIO_ID" .env > .env.tmp && mv .env.tmp .env
echo ".env 정리 완료"
cat .env | grep -v "^#" | grep "="
```

Expected:
```
KIS_APP_KEY=...
KIS_APP_SECRET=...
JARVIS_BOT_TOKEN=...
JARVIS_CHAT_ID=...
NOTION_TOKEN=...
NOTION_ANALYSIS_DB_ID=...
NOTION_STOCK_DB_ID=...
```

- [ ] **Step 3: 전체 임포트 체인 검증**

```bash
cd ~/jarvis-pipeline
python3 -c "
from core.kis_client import KISClient
from core import portfolio, notifier, notion_saver
import collector, price_alert, bot
print('✅ 모든 모듈 임포트 성공')
"
```

Expected: `✅ 모든 모듈 임포트 성공`

- [ ] **Step 4: 스케줄러 백그라운드로 재시작**

```bash
cd ~/jarvis-pipeline
nohup python3 scheduler.py >> logs/scheduler.log 2>&1 &
sleep 3
cat logs/scheduler.pid
tail -10 logs/scheduler.log
```

Expected: PID 출력 및 "🚀 Jarvis 스케줄러 시작" 로그 확인

- [ ] **Step 5: 봇 동작 확인**

Telegram에서 `/list` 전송 → 현재 포트폴리오 11종목 목록 수신 확인

- [ ] **Step 6: 최종 커밋**

```bash
cd ~/jarvis-pipeline
git add -A
git commit -m "chore: .env 정리 (Google Sheets 항목 제거), 고도화 완료"
```

---

## 완료 기준

- [ ] `scheduler.py` 프로세스 실행 중 (PID 확인)
- [ ] `bot.py` 프로세스 실행 중 (별도 프로세스)
- [ ] Telegram `/list` 명령 → 11종목 응답
- [ ] Telegram `/p` 명령 → 현재가 리포트 수신
- [ ] `logs/collector.log`, `logs/bot.log`, `logs/scheduler.log` 생성됨
- [ ] `data/market_data_YYYYMMDD.json` 정상 수집됨
- [ ] 키움 관련 파일 없음 (`ls *.py | grep kiwoom` → 결과 없음)
