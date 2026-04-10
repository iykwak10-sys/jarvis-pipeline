# Jarvis Pipeline 고도화 설계 문서

**작성일:** 2026-04-10  
**상태:** 승인됨  
**목표:** KIS API 단일화, 모듈 재설계, Telegram 봇 종목 관리, Google Sheets 제거

---

## 1. 배경 및 목표

현재 Jarvis Pipeline은 KIS와 키움 두 API를 병행 사용하며, 코드 중복이 심하고 포트폴리오 소스가 파일마다 다르다. 이를 다음 방향으로 개선한다:

- **KIS API 단일 사용** (키움 관련 코드 전면 제거)
- **공통 모듈(`core/`) 분리**로 중복 제거
- **`portfolio.json` 단일 소스**로 포트폴리오 통합 관리
- **Telegram 봇**으로 종목 추가/삭제/조회
- **Google Sheets 제거** (Notion + Telegram만 유지)
- **로그 로테이션** 적용
- **보안 강화** (하드코딩 키 완전 제거)

---

## 2. 디렉토리 구조

```
jarvis-pipeline/
├── core/
│   ├── kis_client.py        # KIS API 클라이언트 + 토큰 관리
│   ├── portfolio.py         # 포트폴리오 로드/저장/추가/삭제
│   ├── notifier.py          # Telegram 알림 전송
│   └── notion_saver.py      # Notion DB 저장
│
├── portfolio.json           # 종목 목록 (단일 소스)
├── scheduler.py             # 스케줄 오케스트레이터
├── collector.py             # KIS 시세 수집
├── price_alert.py           # 급등/급락 알림 (±5%)
├── bot.py                   # Telegram 봇 (/add, /remove, /list, /p)
│
├── data/                    # 날짜별 JSON 스냅샷
├── logs/                    # 로그 파일 (RotatingFileHandler)
├── .env                     # API 키/토큰 (git 제외)
└── .gitignore
```

### 삭제 대상
- `kis_collector.py` (→ `collector.py` + `core/`로 대체)
- `kiwoom_collector.py`
- `kiwoom_api.py`
- `kiwoom_telegram_bot.py`
- `auto_saver.py` (Google Sheets 코드 포함)
- `kis_collector.py.backup`, `.backup2`, `.backup3`, `.before_enhancement`

---

## 3. 핵심 모듈 설계

### `core/kis_client.py`
- `TokenManager`: 토큰 발급 및 캐시 (`data/.kis_token.json`)
  - 유효기간 86400초, 만료 전 자동 갱신
- `KISClient`: KIS REST API 래퍼
  - `get_price(code)`: 현재가, 등락률, 거래량 반환
  - `get_prices(codes)`: 배치 조회 (0.1초 간격, 초당 20건 제한 준수)

### `core/portfolio.py`
- `load()`: `portfolio.json` 읽기
- `save(stocks)`: `portfolio.json` 쓰기
- `add(code, name, sector, buy_price)`: 종목 추가
- `remove(code)`: 종목 삭제
- 파일 구조:
```json
{
  "stocks": [
    {"code": "005930", "name": "삼성전자", "sector": "반도체", "buy_price": 75000}
  ]
}
```
- `buy_price` 필드는 선택사항 (없으면 누적손익률 미표시)

### `core/notifier.py`
- `send(message)`: Telegram 메시지 전송
- `send_portfolio_report(stocks_data)`: 포트폴리오 현황 포맷 전송
- `send_alert(code, name, change_pct)`: 급등/급락 알림 포맷 전송

### `core/notion_saver.py`
- `save_daily_report(date, stocks_data)`: 종목주가 DB에 날짜별 저장
- `save_analysis(data)`: 분석리포트 DB에 저장

---

## 4. 데이터 흐름

```
scheduler.py
  ├── 5분 간격 (09:00~15:30, 평일) → collector.py
  │     └── KIS API → data/market_data_YYYYMMDD.json
  │                 → notifier.py → Telegram (장중 현황)
  │
  ├── 10:00 / 13:00 / 14:30 → price_alert.py
  │     └── ±5% 감지 → notifier.py → Telegram 즉시 알림
  │
  ├── 15:35 (마감) → collector.py (마감 수집)
  │     └── notion_saver.py → Notion DB 저장
  │         notifier.py → Telegram 마감 리포트
  │
  └── 06:05 → notifier.py → 미국장 마감 알림 (수동 메시지)

bot.py (별도 프로세스, 상시 실행)
  ├── /add 005930 삼성전자 반도체 75000 → portfolio.json 추가
  ├── /remove 005930                   → portfolio.json 삭제
  ├── /list                            → 현재 포트폴리오 목록 출력
  ├── /p                               → 전체 종목 현재가 즉시 조회
  └── /p 005930                        → 특정 종목 현재가 조회
```

---

## 5. Telegram 봇 명령어

| 명령어 | 예시 | 동작 |
|--------|------|------|
| `/add` | `/add 005930 삼성전자 반도체 75000` | 종목 추가 (매입가 선택사항) |
| `/remove` | `/remove 005930` | 종목 삭제 |
| `/list` | `/list` | 전체 포트폴리오 목록 출력 |
| `/p` | `/p` | 전체 보유종목 현재가 즉시 조회 |
| `/p 005930` | `/p 005930` | 특정 종목 현재가 조회 |

**보안:** `.env`의 `JARVIS_CHAT_ID`와 일치하는 채팅만 수락

---

## 6. 로깅

- `RotatingFileHandler` 적용: 파일당 **10MB**, 최대 **5개** 백업 유지
- 로그 파일: `logs/scheduler.log`, `logs/collector.log`, `logs/bot.log`, `logs/price_alert.log`

---

## 7. 보안

- `.env`에 모든 API 키/토큰 집중 관리
- `.gitignore`에 `.env`, `google_credentials.json`, `credentials/`, `data/.kis_token.json` 포함
- 코드 내 하드코딩 키 완전 제거

---

## 8. 환경변수 목록 (`.env`)

```
# 한국투자증권
KIS_APP_KEY=
KIS_APP_SECRET=

# Telegram
JARVIS_BOT_TOKEN=
JARVIS_CHAT_ID=

# Notion
NOTION_TOKEN=
NOTION_ANALYSIS_DB_ID=
NOTION_STOCK_DB_ID=
```

---

## 9. 구현 순서

1. 키움/백업 파일 삭제 및 `.gitignore` 정비
2. `portfolio.json` 현행화 (현재 보유 종목으로 업데이트)
3. `core/` 모듈 작성 (`kis_client`, `portfolio`, `notifier`, `notion_saver`)
4. `collector.py` 작성 (core 활용)
5. `price_alert.py` 리팩토링 (core 활용)
6. `bot.py` 작성
7. `scheduler.py` 리팩토링 (bot 프로세스 관리 추가)
8. 테스트 후 스케줄러 재시작
