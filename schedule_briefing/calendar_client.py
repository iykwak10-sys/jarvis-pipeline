# schedule_briefing/calendar_client.py
"""Google Calendar 연동 — 오늘 남은 일정 조회"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests as http_requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

_PROJECT_ROOT = Path(__file__).parent.parent
_CREDENTIALS_DIR = _PROJECT_ROOT / "credentials"
_DESKTOP_CLIENT = _CREDENTIALS_DIR / "calendar_desktop_client.json"
_WEB_CLIENT = _CREDENTIALS_DIR / "client_secret_609042792231-jjm8ugkepf8tv7u50upa7gpbonl9fc1v.apps.googleusercontent.com.json"
_TOKEN_FILE = _CREDENTIALS_DIR / "calendar_token.json"

# 인증 만료 시 재인증 알림 (스로틀 상태 파일)
_AUTH_ALERT_STATE = _PROJECT_ROOT / "data" / ".calendar_auth_alert"
_AUTH_ALERT_THROTTLE_HOURS = 12

_LOCAL_PORT = 8090
_LOCAL_REDIRECT = f"http://localhost:{_LOCAL_PORT}/"
_KST = ZoneInfo("Asia/Seoul")


def _get_client_secret_path() -> Path:
    """Desktop 클라이언트 우선, 없으면 Web 클라이언트"""
    if _DESKTOP_CLIENT.exists():
        return _DESKTOP_CLIENT
    if _WEB_CLIENT.exists():
        return _WEB_CLIENT
    raise FileNotFoundError(
        f"Google OAuth 클라이언트 시크릿 없음.\n"
        f"  방법 1 (추천): Google Cloud Console → 사용자 인증 정보 →\n"
        f"    OAuth 2.0 클라이언트 ID 만들기 → '데스크톱 앱' 선택 →\n"
        f"    JSON 다운로드 → {_DESKTOP_CLIENT} 에 저장\n"
        f"  방법 2: 기존 Web 클라이언트로 수동 인증 (아래 안내 따름)"
    )


def _detect_client_type(path: Path) -> str:
    """클라이언트 JSON 파일에서 타입 감지"""
    data = json.loads(path.read_text())
    if "installed" in data:
        return "desktop"
    if "web" in data:
        return "web"
    return "unknown"


def _auth_desktop(client_path: Path) -> Credentials:
    """Desktop 클라이언트 — 로컬 서버 브라우저 인증"""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
    return flow.run_local_server(port=8090, open_browser=True)


def _auth_web_local_server(client_path: Path) -> Credentials:
    """Web 클라이언트 — 로컬 서버로 리디렉션 받아 인증

    사전 조건: Google Cloud Console에서 Web 클라이언트의
    '승인된 리디렉션 URI'에 http://localhost:8090/ 추가 필요
    """
    data = json.loads(client_path.read_text())
    client_info = data["web"]
    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]

    auth_code_result = {"code": None, "error": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                auth_code_result["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("✅ 인증 완료! 이 창을 닫아도 됩니다.".encode("utf-8"))
            else:
                auth_code_result["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"❌ 인증 실패: {auth_code_result['error']}".encode("utf-8"))

        def log_message(self, *args):
            pass

    auth_params = {
        "client_id": client_id,
        "redirect_uri": _LOCAL_REDIRECT,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(auth_params)}"

    print("\n" + "=" * 60)
    print("📅 Google Calendar 최초 인증")
    print("=" * 60)
    print(f"\n아래 URL을 브라우저에서 열어주세요:\n\n{auth_url}\n")
    print(f"(리디렉션 대기 중: localhost:{_LOCAL_PORT})\n")

    server = HTTPServer(("localhost", _LOCAL_PORT), _Handler)
    server.timeout = 120
    server.handle_request()
    server.server_close()

    if not auth_code_result["code"]:
        raise ValueError(
            f"인증 실패: {auth_code_result.get('error', '타임아웃')}\n"
            f"Google Cloud Console → 사용자 인증 정보 → Web 클라이언트 →\n"
            f"'승인된 리디렉션 URI'에 {_LOCAL_REDIRECT} 가 등록되어 있는지 확인하세요."
        )

    token_resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": auth_code_result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _LOCAL_REDIRECT,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()

    return Credentials(
        token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def _get_service():
    """Google Calendar API 서비스 객체 반환 (자동 토큰 갱신)"""
    creds: Optional[Credentials] = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_path = _get_client_secret_path()
            client_type = _detect_client_type(client_path)

            if client_type == "desktop":
                logger.info("Desktop OAuth 클라이언트로 인증 시작...")
                creds = _auth_desktop(client_path)
            elif client_type == "web":
                logger.info("Web OAuth 클라이언트 — 로컬 서버 인증 모드...")
                creds = _auth_web_local_server(client_path)
            else:
                raise ValueError(f"알 수 없는 OAuth 클라이언트 타입: {client_path}")

        _TOKEN_FILE.write_text(creds.to_json())
        logger.info(f"✅ Calendar 토큰 저장 완료: {_TOKEN_FILE}")

    return build("calendar", "v3", credentials=creds)


def _is_auth_failure(err: Exception) -> bool:
    """재인증이 필요한 인증 실패인지 판별 (일시적 네트워크 오류와 구분)"""
    msg = str(err).lower()
    return (
        "invalid_grant" in msg
        or "invalid_rapt" in msg
        or "token has been expired or revoked" in msg
    )


def _alert_auth_failure(err: Exception) -> None:
    """캘린더 인증 만료 시 텔레그램으로 재인증 알림 (12시간 스로틀).

    토큰이 죽으면 브리핑이 조용히 빈 상태가 되므로, 사용자가 즉시 인지하도록 알림.
    """
    if not _is_auth_failure(err):
        return
    try:
        from datetime import timedelta
        now = datetime.now()
        if _AUTH_ALERT_STATE.exists():
            last = datetime.fromisoformat(_AUTH_ALERT_STATE.read_text().strip())
            if now - last < timedelta(hours=_AUTH_ALERT_THROTTLE_HOURS):
                return   # 최근에 이미 알림 보냄 — 스팸 방지

        from core import notifier
        notifier.send(
            "⚠️ <b>일정 브리핑 점검 필요</b>\n\n"
            "Google 캘린더 인증이 만료되어(invalid_grant) 일정 조회가 중단됐습니다.\n"
            "재인증 전까지 일정 브리핑이 발송되지 않습니다.\n"
            "캘린더 OAuth 재인증을 진행해 주세요."
        )
        _AUTH_ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        _AUTH_ALERT_STATE.write_text(now.isoformat())
        logger.warning("캘린더 재인증 필요 — 텔레그램 알림 전송됨")
    except Exception as alert_err:
        logger.warning(f"재인증 알림 전송 실패: {alert_err}")


def get_todays_events(max_results: int = 20) -> list[dict]:
    """오늘 남은 일정 조회 (현재 시각 이후)

    Returns:
        list of {
            id, summary, location, description,
            start_dt (datetime, KST), end_dt (datetime, KST),
            has_location (bool)
        }
    """
    try:
        service = _get_service()
        # Google Calendar의 timeMin/timeMax는 명시한 timezone을 기준으로
        # 해석된다. UTC 자정으로 계산하면 한국 날짜 경계가 9시간 밀린다.
        now = datetime.now(_KST)
        tomorrow_start_dt = datetime.combine(
            now.date() + timedelta(days=1), time.min, tzinfo=_KST
        )

        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),   # 현재 시각 이후만
            timeMax=tomorrow_start_dt.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = []
        for item in result.get("items", []):
            # 종일 이벤트는 datetime이 아닌 date만 있음
            start_raw = item["start"].get("dateTime") or item["start"].get("date")
            end_raw = item["end"].get("dateTime") or item["end"].get("date")

            if "T" not in start_raw:
                # 종일 이벤트 — 장소 알림 불필요
                continue

            start_dt = datetime.fromisoformat(start_raw)
            end_dt = datetime.fromisoformat(end_raw)

            location = item.get("location", "").strip()
            events.append({
                "id": item["id"],
                "summary": item.get("summary", "(제목 없음)"),
                "location": location,
                "description": item.get("description", ""),
                "start_dt": start_dt,
                "end_dt": end_dt,
                "has_location": bool(location),
            })

        logger.info(f"캘린더 조회: {len(events)}개 일정 (장소 있음: {sum(1 for e in events if e['has_location'])}개)")
        return events

    except Exception as e:
        logger.error(f"Google Calendar 조회 실패: {e}")
        _alert_auth_failure(e)
        return []


def get_tomorrow_events(max_results: int = 20) -> list[dict]:
    """내일 하루 종일 일정 조회

    Returns:
        list of {
            id, summary, location, description,
            start_dt (datetime, KST), end_dt (datetime, KST),
            has_location (bool)
        }
    """
    try:
        service = _get_service()
        from datetime import timedelta
        now = datetime.now(_KST)
        tomorrow_date = now.date() + timedelta(days=1)
        tomorrow_start_dt = datetime.combine(tomorrow_date, time.min, tzinfo=_KST)
        tomorrow_end_dt = tomorrow_start_dt + timedelta(days=1)

        result = service.events().list(
            calendarId="primary",
            timeMin=tomorrow_start_dt.isoformat(),
            timeMax=tomorrow_end_dt.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = []
        for item in result.get("items", []):
            start_raw = item["start"].get("dateTime") or item["start"].get("date")
            end_raw = item["end"].get("dateTime") or item["end"].get("date")

            if "T" not in start_raw:
                continue

            start_dt = datetime.fromisoformat(start_raw)
            end_dt = datetime.fromisoformat(end_raw)

            location = item.get("location", "").strip()
            events.append({
                "id": item["id"],
                "summary": item.get("summary", "(제목 없음)"),
                "location": location,
                "description": item.get("description", ""),
                "start_dt": start_dt,
                "end_dt": end_dt,
                "has_location": bool(location),
            })

        logger.info(f"내일 캘린더: {len(events)}개 일정 (장소 있음: {sum(1 for e in events if e['has_location'])}개)")
        return events

    except Exception as e:
        logger.error(f"Google Calendar 내일 조회 실패: {e}")
        _alert_auth_failure(e)
        return []
