# core/retry.py
"""지수 백오프 재시도 데코레이터 — 모든 외부 API 호출에 사용"""

import asyncio
import functools
import logging
import random
import time
from typing import Callable, Optional, Type, Union

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    exceptions: Union[Type[Exception], tuple[Type[Exception], ...]] = (
        ConnectionError, TimeoutError, OSError, IOError
    ),
) -> Callable:
    """동기 함수용 지수 백오프 재시도 데코레이터.

    Args:
        max_attempts: 최대 시도 횟수 (기본: 3)
        base_delay: 초기 대기 시간 (기본: 1초)
        max_delay: 최대 대기 시간 (기본: 30초)
        jitter: 랜덤 지터 추가 여부 (기본: True)
        exceptions: 재시도할 예외 클래스 (기본: 네트워크 관련)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error(
                            "%s 최종 실패 (%d/%d): %s",
                            func.__name__, attempt, max_attempts, e,
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random() * 0.5  # 50~100% 랜덤
                    logger.warning(
                        "%s 재시도 (%d/%d) — %s 후 %.1f초 대기",
                        func.__name__, attempt, max_attempts, e, delay,
                    )
                    time.sleep(delay)
            # 도달 불가 (위 루프가 항상 raise 또는 return)
            raise last_exc  # type: ignore
        return wrapper
    return decorator


def retry_async(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    exceptions: Union[Type[Exception], tuple[Type[Exception], ...]] = (
        ConnectionError, TimeoutError, OSError, IOError
    ),
) -> Callable:
    """비동기 함수용 지수 백오프 재시도 데코레이터."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error(
                            "%s 최종 실패 (%d/%d): %s",
                            func.__name__, attempt, max_attempts, e,
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random() * 0.5
                    logger.warning(
                        "%s 재시도 (%d/%d) — %s 후 %.1f초 대기",
                        func.__name__, attempt, max_attempts, e, delay,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore
        return wrapper
    return decorator
