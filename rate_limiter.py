import time
import asyncio
import logging
from collections import deque

logger = logging.getLogger("RateLimiter")

TELEGRAM_LIMIT = 30
PROTECTION_THRESHOLD = 0.85
PROTECTION_LIMIT = int(TELEGRAM_LIMIT * PROTECTION_THRESHOLD)


class RateLimiter:
    def __init__(self):
        self._timestamps: deque[float] = deque()
        self._limit = PROTECTION_LIMIT
        self._paused = False
        self._wait_count = 0
        self._total_wait_time = 0.0

    def _clean_window(self) -> None:
        cutoff = time.time() - 1.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def _current_rate(self) -> int:
        self._clean_window()
        return len(self._timestamps)

    def _is_at_limit(self) -> bool:
        return self._current_rate() >= self._limit

    async def wait_if_needed(self, is_upload: bool = False) -> str | None:
        if is_upload:
            return None

        if not self._is_at_limit():
            return None

        self._paused = True
        wait_start = time.time()
        self._wait_count += 1
        logger.warning(
            f"Rate limit hit ({self._current_rate()}/{self._limit}). "
            f"Pausing non-upload messages..."
        )

        while self._is_at_limit():
            await asyncio.sleep(0.5)

        waited = time.time() - wait_start
        self._total_wait_time += waited
        self._paused = False

        status = f"⏳ Waiting/protecting from floodwait ({waited:.1f}s)..."
        logger.info(f"Rate limit cleared after {waited:.1f}s. Resuming.")
        return status

    def record_send(self) -> None:
        self._timestamps.append(time.time())

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_stats(self) -> dict:
        self._clean_window()
        return {
            "current_rate": self._current_rate(),
            "limit": self._limit,
            "is_paused": self._paused,
            "wait_count": self._wait_count,
            "total_wait_time": round(self._total_wait_time, 2),
        }


rate_limiter = RateLimiter()
