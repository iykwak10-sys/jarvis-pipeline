#!/usr/bin/env bash
# Claude bridge 봇 토큰을 보안 입력하는 스크립트.
# 토큰은 화면에 표시되지 않으며(.no-echo), 셸 히스토리/대화 로그에 남지 않습니다.
#
# 사용법:  bash setup_bridge_token.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
PLIST="$HOME/Library/LaunchAgents/com.raphael.claude-bridge.plist"
LABEL="com.raphael.claude-bridge"

echo "🔐 Claude bridge 봇 토큰 설정"
echo "   (입력 내용은 화면에 표시되지 않습니다)"
echo

# 1) no-echo 입력
read -r -s -p "봇 토큰 붙여넣기: " TOKEN
echo
if [[ -z "${TOKEN// }" ]]; then
  echo "❌ 토큰이 비었습니다. 중단."
  exit 1
fi

# 2) 형식/유효성 검증 (getMe) — 토큰은 표준출력에 노출하지 않음
echo "🔎 토큰 검증 중..."
RESP="$(curl -s "https://api.telegram.org/bot${TOKEN}/getMe")"
if ! echo "$RESP" | grep -q '"ok":true'; then
  echo "❌ 유효하지 않은 토큰입니다. (getMe 실패)"
  exit 1
fi
BOT_USER="$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["username"])')"
echo "✅ 봇 확인: @${BOT_USER}"

# 3) .env 갱신 (기존 키 있으면 교체)
touch "$ENV_FILE"
if grep -q '^CLAUDE_BRIDGE_BOT_TOKEN=' "$ENV_FILE"; then
  # macOS sed in-place — 토큰을 인자로 노출하지 않도록 임시파일 사용
  TMP="$(mktemp)"
  grep -v '^CLAUDE_BRIDGE_BOT_TOKEN=' "$ENV_FILE" > "$TMP"
  mv "$TMP" "$ENV_FILE"
fi
printf 'CLAUDE_BRIDGE_BOT_TOKEN=%s\n' "$TOKEN" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
unset TOKEN RESP
echo "✅ .env 저장 완료 (권한 600)"

# 4) launchd 잡 로드/재시작
if [[ -f "$PLIST" ]]; then
  echo "🚀 launchd 잡 (재)시작..."
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 2
  echo "📋 상태:"
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|pid" | head -3 || true
  echo
  echo "📜 최근 로그:"
  tail -n 8 "$HERE/logs/claude_bridge.log" 2>/dev/null || echo "  (아직 로그 없음)"
else
  echo "⚠️ plist 없음: $PLIST — 수동 실행: venv/bin/python claude_bridge.py"
fi

echo
echo "🎉 완료! 텔레그램에서 @${BOT_USER} 에게 /start 를 보내보세요."
