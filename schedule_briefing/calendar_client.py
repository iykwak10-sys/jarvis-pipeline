# schedule_briefing/calendar_client.py
"""Google Calendar 연동 — 오늘 남은 일정 조회"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# 자격증명 파일 경로 (기존 프로젝트 규칙 따라 credentials/ 사용)
_PROJECT_ROOT = Path(__file__).parent.parent
_CLIENT_SECRET = _PROJECT_ROOT / "credentials" / "client_secret_609042792231-jjm8ugkepf8tv7u50upa7gpbonl9fc1v.apps.googleusercontent.com.json"
_TOKEN_FILE = _PROJECT_ROOT / "credentials" / "calendar_token.json"


def _get_service():
    """Google Calendar API 서비스 객체 반환 (자동 토큰 갱신)"""
    creds: Optional[Credentials] = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _CLIENT_SECRET.exists():
                raise FileNotFoundError(
                    f"Google OAuth 클라이언트 시크릿 없음: {_CLIENT_SECRET}\n"
                    "Google Cloud Console에서 OAuth 자격증명을 다운로드하세요."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_FILE.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


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
        now = datetime.now(timezone.utc)

        # 오늘 자정 (UTC) ~ 내일 자정 (UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_end_dt = now.replace(hour=23, minute=59, second=59, microsecond=0)
        today_end = today_end_dt.isoformat()

        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),   # 현재 시각 이후만
            timeMax=today_end,
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
        return []
