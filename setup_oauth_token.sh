#!/usr/bin/env bash
# Claude Code 장기 OAuth 토큰을 발급하고 .env에 보안 저장하는 스크립트.
# launchd(헤드리스) 환경에서 만료 없이 구독 기반 인증을 쓰기 위함.
#
# 사용법:  bash setup_oauth_token.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
PLIST="$HOME/Library/LaunchAgents/com.raphael.claude-bridge.plist"
LABEL="com.raphael.claude-bridge"

echo "🔐 Claude Code 장기 OAuth 토큰 설정"
echo
echo "1) 먼저 토큰을 발급합니다. 브라우저가 열리면 로그인/승인하세요."
echo "   (구독 계정 필요. 발급된 토큰은 sk-ant-oat... 형태입니다)"
echo
read -r -p "지금 'claude setup-token'을 실행할까요? [Y/n] " GO
if [[ ! "${GO:-Y}" =~ ^[Nn] ]]; then
  echo "──────────────────────────────────────────"
  claude setup-token || { echo "❌ setup-token 실패"; exit 1; }
  echo "──────────────────────────────────────────"
  echo "↑ 위에 출력된 토큰을 복사하세요."
fi

echo
# no-echo 입력 — 화면/히스토리/대화 로그에 안 남음
read -r -s -p "토큰 붙여넣기(화면 미표시): " TOKEN
echo
if [[ -z "${TOKEN// }" ]]; then
  echo "❌ 토큰이 비었습니다."
  exit 1
fi

# 클린 환경에서 실제 인증 검증
echo "🔎 클린 환경에서 인증 검증 중..."
RESULT="$(env -i HOME="$HOME" \
  PATH="/Users/kwaksmacmini/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" \
  claude -p "reply with exactly: AUTHOK" --permission-mode bypassPermissions 2>&1 || true)"
if ! echo "$RESULT" | grep -q "AUTHOK"; then
  echo "❌ 인증 검증 실패. 응답:"
  echo "$RESULT" | head -5
  echo "토큰을 저장하지 않았습니다."
  exit 1
fi
echo "✅ 헤드리스 인증 성공"

# .env 갱신 (기존 키 교체)
touch "$ENV_FILE"
if grep -q '^CLAUDE_CODE_OAUTH_TOKEN=' "$ENV_FILE"; then
  TMP="$(mktemp)"; grep -v '^CLAUDE_CODE_OAUTH_TOKEN=' "$ENV_FILE" > "$TMP"; mv "$TMP" "$ENV_FILE"
fi
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$TOKEN" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
unset TOKEN RESULT
echo "✅ .env 저장 완료 (권한 600)"

# launchd 재시작
if [[ -f "$PLIST" ]]; then
  echo "🚀 브리지 재시작..."
  launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || {
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  }
  sleep 2
  echo "📋 상태:"; launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state =|pid =" | head -2 || true
fi

echo
echo "🎉 완료! 텔레그램에서 봇에게 메시지를 보내 테스트하세요."
echo "   예: /projects → /cd jarvis-pipeline → '현재 디렉터리 파일 목록 보여줘'"
