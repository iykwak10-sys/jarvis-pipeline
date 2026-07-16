import re


_TELEGRAM_BOT_TOKEN_IN_PATH = re.compile(r"(/bot)[^/\s'\"]+", re.IGNORECASE)


def sanitize_error_message(message: str) -> str:
    return _TELEGRAM_BOT_TOKEN_IN_PATH.sub(r"\1[REDACTED]", message)
