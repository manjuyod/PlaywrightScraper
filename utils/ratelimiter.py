
import asyncio, time, random

class TokenBucket:
    """Allow <rate> requests every <per> seconds (sliding window)."""
    def __init__(self, rate: int, per: float):
        self.capacity = rate
        self.tokens   = rate
        self.per      = per
        self.updated  = time.monotonic()
        self._lock    = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            # leak tokens back in
            leaked = (now - self.updated) * (self.capacity / self.per)
            self.tokens = min(self.capacity, self.tokens + leaked)
            self.updated = now

            if self.tokens < 1:
                wait = (1 - self.tokens) * (self.per / self.capacity)
                await asyncio.sleep(wait)

            self.tokens -= 1

# Global rate limiter - 5 requests per second
global_limiter = TokenBucket(rate=5, per=1.0)
