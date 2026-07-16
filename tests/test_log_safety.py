from core.log_safety import sanitize_error_message


def test_sanitize_error_message_redacts_telegram_bot_token() -> None:
    message = (
        "HTTPSConnectionPool(host='api.telegram.org', port=443): "
        "https://api.telegram.org/bot123456:secret/sendMessage"
    )

    sanitized = sanitize_error_message(message)

    assert "123456:secret" not in sanitized
    assert "/bot[REDACTED]/sendMessage" in sanitized
