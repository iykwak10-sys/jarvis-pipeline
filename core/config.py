# core/config.py
"""중앙 설정 모듈 — .env 로딩을 한 곳에서 처리"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 프로젝트 루트 경로 (이 파일 기준 ../../)
PROJECT_ROOT = Path(__file__).parent.parent

# .env 로딩 (최초 import 시 한 번만)
_env_loaded = False


def _ensure_loaded():
    """최초 한 번만 .env 로드"""
    global _env_loaded
    if not _env_loaded:
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        _env_loaded = True


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 조회 (자동 .env 로딩)"""
    _ensure_loaded()
    return os.environ.get(key, default)


def get_int(key: str, default: int = 0) -> int:
    """정수형 환경 변수 조회"""
    val = get(key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    """불리언 환경 변수 조회 ('1', 'true', 'yes' → True)"""
    val = get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "y")


# ── 주요 경로 상수 ──────────────────────────────────────────────
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
PORTFOLIO_FILE = PROJECT_ROOT / "portfolio.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ── 주요 설정값 ──────────────────────────────────────────────────
# KIS API
KIS_APP_KEY: Optional[str] = get("KIS_APP_KEY")
KIS_APP_SECRET: Optional[str] = get("KIS_APP_SECRET")

# Telegram
JARVIS_BOT_TOKEN: Optional[str] = get("JARVIS_BOT_TOKEN")
JARVIS_CHAT_ID: int = get_int("JARVIS_CHAT_ID", 0)
JARVIS_TELEGRAM_MODE: str = get("JARVIS_TELEGRAM_MODE", "news")

# OpenRouter / AI
OPENROUTER_API_KEY: Optional[str] = get("OPENROUTER_API_KEY")
OPENROUTER_MODEL: str = get("MODEL", "openai/gpt-5-nano")
OPENROUTER_BASE_URL: str = get("BASE_URL", "https://openrouter.ai/api/v1")

# Notion
NOTION_TOKEN: Optional[str] = get("NOTION_TOKEN")
NOTION_STOCK_DB_ID: Optional[str] = get("NOTION_STOCK_DB_ID")
NOTION_ANALYSIS_DB_ID: Optional[str] = get("NOTION_ANALYSIS_DB_ID")
