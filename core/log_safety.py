import re


_TELEGRAM_BOT_TOKEN_IN_PATH = re.compile(r"(/bot)[^/\s'\"]+", re.IGNORECASE)
_SERVICE_KEY_PARAM = re.compile(r"(serviceKey=)[^&\s'\"]+", re.IGNORECASE)


def sanitize_error_message(message: str) -> str:
    message = _TELEGRAM_BOT_TOKEN_IN_PATH.sub(r"\1[REDACTED]", message)
    return _SERVICE_KEY_PARAM.sub(r"\1[REDACTED]", message)
