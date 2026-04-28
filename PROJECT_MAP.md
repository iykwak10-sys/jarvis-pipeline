# 🗂 Raphael Mac Mini — 프로젝트 전체 지도

> 최종 업데이트: 2026-04-24  
> 이 문서는 Mac Mini에서 실행 중인 모든 프로젝트와 서비스의 인덱스입니다.  
> **위치**: `~/jarvis-pipeline/PROJECT_MAP.md`

---

## 📌 빠른 참조 — 현재 실행 중인 서비스

| 서비스명 | 폴더 | 실행 방식 | 역할 |
|---------|------|----------|------|
| Hermes Agent | `~/.hermes/` | LaunchAgent | AI 에이전트 코어, 텔레그램 게이트웨이, 크론잡 관리 |
| Jarvis Pipeline | `~/jarvis-pipeline/` | LaunchAgent ×2 | KIS 데이터 수집, 스케줄러, 모닝 브리핑 |
| Telegram News Bot | `~/telegram_news_bot/` | LaunchAgent | 텔레그램 뉴스봇 |
| KIS-MCP Summary | `~/kis-mcp/` | LaunchAgent | KIS 종가 요약 리포트 (매일 18:00) |
| 개인투자비서 Agent | `~/개인투자비서 Agent/` | Hermes 크론 연동 | 일일 브리핑·마감 보고서 자동 생성 |

---

## 📁 폴더별 상세 설명

### 🔒 ~/.hermes/ — Hermes Agent 코어 (절대 이동 금지)
- **역할**: AI 에이전트 시스템 전체 코어
- **주요 내용**: 크론잡(`cron/jobs.json`), 텔레그램 게이트웨이, 스킬, 메모리, 세션
- **LaunchAgent**: `ai.hermes.gateway.plist`
- **크론잡 4개**: US Market Report, 위험 신호등, 위험 신호등 리포트, Notion 포트폴리오 리포트
- **⚠️ 주의**: 절대 폴더 이동/이름 변경 금지

### ⚙️ ~/jarvis-pipeline/ — KIS 데이터 파이프라인 (절대 이동 금지)
- **역할**: 한국투자증권 API 데이터 수집 및 분석 파이프라인
- **주요 파일**: `scheduler.py`, `morning_briefing.py`, `bot.py`
- **하위 폴더**:
  - `kis-mcp/` — Claude Code용 KIS MCP 서버 (루트 ~/kis-mcp와 **다른 역할**)
  - `data/` — 일별 시장 데이터 JSON
  - `logs/` — 실행 로그
  - `credentials/` — Google 인증
  - `docs/` — 문서 및 인포그래픽 (infographic.png 포함)
- **LaunchAgent**: `com.jarvis.pipeline` (평일 15:35), `com.jarvis.scheduler` (상시)
- **⚠️ 주의**: 절대 폴더 이동 금지

### 📱 ~/telegram_news_bot/ — 텔레그램 뉴스봇 (절대 이동 금지)
- **역할**: 텔레그램 뉴스 수집·발송 봇
- **주요 파일**: `bot.py`, `news_bot.py`
- **LaunchAgent**: `com.jarvis.newsbot.plist`

### 📊 ~/개인투자비서 Agent/ — 투자 자동 브리핑 (절대 이동 금지)
- **역할**: 일일 투자 브리핑·마감 보고서 자동 생성
- **스케줄**: 평일 06:30 브리핑 / 16:00 마감 / 일요일 09:00 주간 리포트
- **연동**: Hermes 크론잡, Google Sheets, Notion, Telegram

### 🔑 ~/kis-mcp/ — KIS 요약 리포트 (절대 이동 금지)
- **역할**: KIS 종가 기반 요약 리포트 (`summary_report.py`)
- **LaunchAgent**: `com.kis-mcp.summary.plist` (매일 18:00)
- **⚠️ jarvis-pipeline/kis-mcp와 별개**

### 🤖 ~/agents/ — Claude 플러그인 마켓플레이스
- **역할**: Claude Code 플러그인·에이전트 라이브러리
- **내용**: 75개 플러그인, 182 에이전트, gptaku_plugins 통합 (2026-04-24)
- **실행 서비스 없음** — 개발/참조용

---

## 🔄 LaunchAgent 전체 목록

| plist | 실행 경로 | 스케줄 |
|-------|----------|--------|
| `ai.hermes.gateway.plist` | `~/.hermes/hermes-agent/` | 상시 (KeepAlive) |
| `com.jarvis.pipeline.plist` | `~/jarvis-pipeline/kis_collector.py` | 평일 15:35 |
| `com.jarvis.scheduler.plist` | `~/jarvis-pipeline/scheduler.py` | 상시 (KeepAlive) |
| `com.jarvis.newsbot.plist` | `~/telegram_news_bot/bot.py` | 상시 |
| `com.kis-mcp.summary.plist` | `~/kis-mcp/summary_report.py` | 매일 18:00 |

---

## 📡 Hermes 크론잡 현황 (2026-04-24 기준)

| 이름 | 스케줄 | 전송처 |
|------|--------|--------|
| US Market Survival & KR Strategy Report | 매일 07:10 | Telegram |
| 4월 위험 신호등 | 매일 07:10 | Telegram |
| 4월 위험 신호등 리포트 | 매일 06:30 | Telegram (스크립트 직접) |
| Raphael Portfolio Extended Report | 매일 16:00 | Notion |

---

## 🧹 수동 정리 필요 (터미널에서 실행)

```bash
# 1. hermes-infographic 폴더 삭제 (이미지는 jarvis-pipeline/docs/로 이동 완료)
rm -rf ~/hermes-infographic/

# 2. gptaku_plugins 원본 삭제 (agents/gptaku_plugins/로 복사 완료)
rm -rf ~/gptaku_plugins/
```

---

## 💡 자주 쓰는 명령

```bash
# Hermes 크론잡 확인
cat ~/.hermes/cron/jobs.json | python3 -m json.tool

# Hermes 게이트웨이 상태
cat ~/.hermes/gateway_state.json

# LaunchAgent 상태
launchctl list | grep -E "(jarvis|hermes|kis)"

# 스케줄러 로그
tail -f ~/jarvis-pipeline/logs/scheduler.log
```
