"""
Modul Retry Handler dengan Smart Exponential Backoff dan Jitter.
"""

import asyncio
import random
import logging
from typing import Callable, Any, Type, Tuple

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    coro_func: Callable,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Any:
    """
    Mengeksekusi coroutine function dengan exponential backoff dan jitter.
    """
    attempt = 0
    while True:
        try:
            return await coro_func()
        except retryable_exceptions as e:
            attempt += 1
            if attempt > max_retries:
                logger.error(f"Batas retry tercapai ({max_retries}). Gagal mengeksekusi: {e}")
                raise e

            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jitter = random.uniform(0.1, 0.5) * delay
            total_delay = delay + jitter
            logger.warning(f"Percobaan {attempt}/{max_retries} gagal ({e}). Retry dalam {total_delay:.2f}s...")
            await asyncio.sleep(total_delay)
